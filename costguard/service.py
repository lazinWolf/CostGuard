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

from . import checks, db, diagnosis, events
from .economics import compare_artifacts, reprice
from .execution import ExecutionArtifact, ExperimentSpec
from .investigation import AnalystSettings, proposals_of
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


def test_fixes(investigation_id: str) -> list[dict]:
    """Queue every fix the analyst proposed. The runner measures each against the original baseline."""
    row = db.get_investigation(investigation_id)
    if row is None:
        raise NotFound("investigation not found")
    specs = [ExperimentSpec.model_validate(proposal["spec"]) for proposal in proposals_of(row["result"])]
    try:
        db.create_fix_jobs(investigation_id, specs)
    except ValueError as exc:
        raise Conflict(str(exc)) from exc
    if not row["fixes"]:
        events.emit("fixes.started", investigation_id=investigation_id, fixes=len(specs))
    return fix_rows(db.get_investigation(investigation_id))


def finish_fix(job_id: str) -> str | None:
    """When `job_id` was a fix run, compare it with the baseline of the check that was investigated,
    under that check's prices and limits. Returns the new report ID; a fix is compared once."""
    found = db.find_fix(job_id)
    job = db.get_job(job_id)
    if found is None or found["report_id"] or job["state"] != "completed" or not job["artifact_id"]:
        return None
    request = db.get_comparison(found["check_id"])["request"]
    saved = compare(request["baseline_id"], job["artifact_id"], PricingCatalog.model_validate(request["pricing"]),
                    ComparisonPolicy.model_validate(request["policy"]) if request.get("policy") else None,
                    request.get("monthly_requests"))
    db.set_fix_report(found["investigation_id"], job_id, saved["id"])
    events.emit("fix.measured", investigation_id=found["investigation_id"], job_id=job_id,
                report_id=saved["id"], status=saved["report"]["status"])
    return saved["id"]


def job_finished(job_id: str) -> None:
    """Everything that may follow a finished run. Called by the runner after every job."""
    finish_check(job_id)
    finish_fix(job_id)


def fix_rows(row: dict) -> list[dict]:
    """Each fix an investigation proposed, with how far its measurement is. One is marked the winner."""
    proposals, recorded = proposals_of(row["result"]), row["fixes"]
    rows = []
    for index, proposal in enumerate(proposals):
        spec, fix = proposal["spec"], recorded[index] if index < len(recorded) else None
        job = db.get_job(fix["job_id"]) if fix else None
        saved = db.get_comparison(fix["report_id"]) if fix and fix["report_id"] else None
        report = saved["report"] if saved else {}
        economic = report.get("economic") or {}
        rows.append({
            "label": f"Fix {'ABC'[index]}" if len(proposals) > 1 else "Fix", "name": spec["name"],
            "hypothesis": proposal["hypothesis"], "words": len(spec["system_prompt"].split()),
            "state": "measured" if saved else "failed" if job and job["state"] in RUN_FAILED
                     else "running" if job else "proposed",
            "job_id": job and job["id"], "progress": job and job["progress"], "total": job and job["total"],
            "report_id": fix and fix["report_id"], "status": report.get("status"),
            "cost": (economic.get("cost_per_request_usd") or {}).get("candidate"),
            "quality": (economic.get("quality_score") or {}).get("candidate"),
            "cases": report.get("baseline_case_count"), "winner": False})
    winner = diagnosis.pick_winner(rows)
    for fix in rows:
        fix["winner"] = fix["name"] == winner
    return rows


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


PENDING = "pending-check:"
RUN_FAILED = {"failed", "cancelled", "interrupted"}


def create_check(data: checks.CheckInput) -> dict:
    """Queue both runs of a check. The runner compares them when the second finishes."""
    baseline, candidate, pricing = checks.build_specs(data)
    db.save_suite(baseline)
    if data.configure_analyst:
        db.save_analyst_settings(checks.analyst_from(data, pricing))
    baseline_job, candidate_job = db.create_job(baseline), db.create_job(candidate)
    db.save_setting(PENDING + candidate_job, {
        "baseline_job_id": baseline_job, "candidate_job_id": candidate_job,
        "names": [baseline.name, candidate.name], "model": data.model, "report_id": None,
        "pricing": pricing.model_dump(mode="json"), "policy": data.policy.model_dump(mode="json"),
        "monthly_requests": data.monthly_requests})
    events.emit("check.created", baseline_job_id=baseline_job, candidate_job_id=candidate_job, model=data.model)
    return {"baseline_job_id": baseline_job, "candidate_job_id": candidate_job}


def finish_check(job_id: str) -> str | None:
    """When `job_id` was the second run of a check and both completed, save the comparison.

    Called by the runner after every job; a job that belongs to no check is ignored. Returns the new
    report ID. Safe to call again: a check is compared once.
    """
    plan = db.get_setting(PENDING + job_id)
    if plan is None or plan["report_id"]:
        return None
    runs = [db.get_job(plan["baseline_job_id"]), db.get_job(job_id)]
    if any(run is None or run["state"] != "completed" or not run["artifact_id"] for run in runs):
        return None
    saved = compare(runs[0]["artifact_id"], runs[1]["artifact_id"], PricingCatalog.model_validate(plan["pricing"]),
                    ComparisonPolicy.model_validate(plan["policy"]), plan["monthly_requests"])
    db.save_setting(PENDING + job_id, {**plan, "report_id": saved["id"]})
    return saved["id"]


def check_progress(candidate_job_id: str) -> dict:
    """Where a check is: running, done (with its report) or failed (with the reason)."""
    plan = db.get_setting(PENDING + candidate_job_id)
    if plan is None:
        raise NotFound("check run not found")
    baseline, candidate = db.get_job(plan["baseline_job_id"]), db.get_job(candidate_job_id)
    broken = next((run for run in (baseline, candidate) if run["state"] in RUN_FAILED), None)
    state = "done" if plan["report_id"] else "failed" if broken else "running"
    return {"state": state, "report_id": plan["report_id"], "names": plan["names"], "model": plan["model"],
            "error": (broken["error"] or f"The run {broken['state']}.") if broken and state == "failed" else None,
            "baseline": baseline, "candidate": candidate}


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
    judge: dict | None  # Who scored the answers, with what rubric and at what cost, when a model did.

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
    judge = source.judge if source else None
    return Check(
        id=report_id, saved=saved, baseline=baseline, candidate=candidate,
        limits=diagnosis.limit_rows(saved["report"], saved["request"].get("policy")),
        breakdown=diagnosis.breakdown(saved["request"]["pricing"], baseline, candidate),
        cases=diagnosis.case_rows(saved["report"], baseline, candidate, inputs),
        related=db.related_investigations(report_id),
        judge=judge and {"model": f"{judge.provider}/{judge.model}", "rubric": judge.rubric,
                         "spend": diagnosis.judge_spend(baseline, candidate)},
    )
