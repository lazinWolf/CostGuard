"""What CostGuard does, independent of how it is asked.

The browser pages, HTTP API, command line, MCP server and runner all call these functions,
so a rule (which evidence may be investigated, how a comparison is saved) lives in one place.
Persistence stays in `db`, pure analysis in `economics`/`diagnosis`.

Failures are domain errors; each surface maps them to its own idiom (HTTP status, exit code).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import db, diagnosis, events
from .economics import compare_artifacts, reprice
from .execution import ExecutionArtifact, ExperimentSpec
from .investigation import AnalystSettings
from .models import ComparisonPolicy, PricingCatalog

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class NotFound(LookupError):
    """The thing asked about does not exist."""


class Conflict(ValueError):
    """The request is valid but not possible in the current state."""


class Unavailable(RuntimeError):
    """A service CostGuard depends on is not reachable."""


def compare(baseline_id: str, candidate_id: str, pricing: PricingCatalog,
            policy: ComparisonPolicy | None = None, monthly_requests: int | None = None) -> dict:
    """Compare two saved runs on one price catalog and save the report."""
    left, right = db.get_artifact(baseline_id), db.get_artifact(candidate_id)
    if left is None or right is None:
        raise NotFound("baseline or candidate artifact not found")
    report = compare_artifacts(left, right, pricing, policy, monthly_requests)
    request_payload = {"baseline_id": baseline_id, "candidate_id": candidate_id,
                       "pricing": pricing.model_dump(mode="json"),
                       "policy": policy.model_dump(mode="json") if policy else None,
                       "monthly_requests": monthly_requests}
    report_payload = json.loads(report.model_dump_json())
    report_id = db.save_comparison(baseline_id, candidate_id, request_payload, report_payload)
    events.emit("comparison.saved", report_id=report_id, status=report.status)
    return {"id": report_id, "report": report_payload}


def reprice_run(artifact_id: str, pricing: PricingCatalog, monthly_requests: int) -> dict:
    """What an observed run would cost at other prices and traffic. Behavior is held fixed."""
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        raise NotFound("artifact not found")
    return reprice(artifact, pricing, monthly_requests)


def start_investigation(report_id: str, question: str) -> str:
    """Queue the analyst on a saved comparison. A proposal needs the candidate's own job spec."""
    settings = db.get_analyst_settings()
    if settings is None:
        raise Conflict("Configure an analyst model on Connections first")
    saved = db.get_comparison(report_id)
    if saved is None:
        raise NotFound("comparison not found")
    source = db.source_experiment(saved["request"]["candidate_id"])
    artifact = db.get_artifact(saved["request"]["candidate_id"])
    if source and ({case.case_id for case in source.cases} != {case.case_id for case in artifact.cases}
                   or source.suite_id != artifact.suite_id):
        source = None
    return db.create_investigation(report_id, question, AnalystSettings.model_validate(settings), source)


def run_proposal(investigation_id: str) -> str:
    """Queue the analyst's proposed experiment (once). Returns the job ID."""
    try:
        return db.run_investigation_proposal(investigation_id)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc


def compare_proposal(investigation_id: str) -> str:
    """Compare the finished proposal run with the original baseline. Returns the report ID."""
    try:
        return db.compare_investigation_proposal(investigation_id)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc


def start_mock_demo() -> dict:
    """Queue the scripted offline demo. It only exists when Compose runs with COSTGUARD_DEMO=1."""
    if os.environ.get("COSTGUARD_DEMO") != "1":
        raise NotFound("Demo is disabled. Start Compose with COSTGUARD_DEMO=1 and --profile demo.")
    from .bootstrap_gateway import bootstrap
    try:
        bootstrap()
    except (httpx.HTTPError, RuntimeError) as exc:
        raise Unavailable("Demo gateway setup failed. Check that the demo profile is running.") from exc
    specs = [ExperimentSpec.model_validate_json((EXAMPLES / f"experiment_{name}.json").read_text())
             for name in ("baseline", "candidate")]
    if db.get_analyst_settings() is None:
        db.save_analyst_settings(AnalystSettings(provider="costguard-mock", model="analyst",
            pricing_version="mock-demo", input_per_million_usd=0, output_per_million_usd=0))
    return {"baseline_job_id": db.create_job(specs[0]), "candidate_job_id": db.create_job(specs[1]),
            "pricing": specs[0].pricing.model_dump(mode="json"),
            "policy": {"max_cost_increase_percent": 10, "min_candidate_quality_score": 1}}


def list_checks(limit: int | None = None) -> list[dict]:
    """Checks, newest first. Every fix measured for a check is nested under the original check."""
    rows = {row["id"]: row for row in db.list_comparisons(limit)}
    parents = db.fix_parents()
    checks: list[dict] = []
    for row in rows.values():
        row["correct"] = diagnosis.correct_answers(row["quality_baseline"], row["quality_candidate"],
                                                   row["quality_cases"])
        row["fixes"] = []
    for row in rows.values():
        root = row
        while parents.get(root["id"]) in rows:
            root = rows[parents[root["id"]]]
        if root is row:
            checks.append(row)
        else:
            root["fixes"].insert(0, row)  # Newest first while iterating, so this reads oldest first.
    return checks


@dataclass
class Check:
    """A saved comparison with everything a person needs to read it."""
    id: str
    saved: dict
    baseline: ExecutionArtifact
    candidate: ExecutionArtifact
    limits: list[dict]
    breakdown: dict | None
    cases: list[dict]
    related: dict

    @property
    def report(self) -> dict:
        return self.saved["report"]

    @property
    def request(self) -> dict:
        return self.saved["request"]


def get_check(report_id: str) -> Check:
    saved = db.get_comparison(report_id)
    if saved is None:
        raise NotFound("report not found")
    baseline = db.get_artifact(saved["request"]["baseline_id"])
    candidate = db.get_artifact(saved["request"]["candidate_id"])
    source = db.source_experiment(str(candidate.artifact_id)) or db.source_experiment(str(baseline.artifact_id))
    inputs = {case.case_id: case for case in source.cases} if source else {}
    return Check(
        id=report_id, saved=saved, baseline=baseline, candidate=candidate,
        limits=diagnosis.limit_rows(saved["report"], saved["request"].get("policy")),
        breakdown=diagnosis.breakdown(saved["request"]["pricing"], baseline, candidate),
        cases=diagnosis.case_rows(saved["report"], baseline, candidate, inputs),
        related=db.related_investigations(report_id),
    )
