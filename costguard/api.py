"""Versioned API backed by the same analysis functions as the CLI and MCP server."""

import json
import os
from decimal import Decimal

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import Field

from . import db
from .analysis import _compare, _mean
from .economics import case_cost, compare_artifacts
from .execution import ExecutionArtifact, ExperimentSpec, ExecutionLimits, suite_digest
from .investigation import AnalystSettings, InvestigationInput
from .models import ComparisonPolicy, PricingCatalog, Schema

router = APIRouter(prefix="/api/v1")


def analyst_for_report(saved):
    left = db.get_artifact(saved["request"]["baseline_id"])
    right = db.get_artifact(saved["request"]["candidate_id"])
    if all(artifact and artifact.configuration.get("mode") == "fixture"
           and artifact.configuration.get("provider") == "costguard-mock" for artifact in (left, right)):
        return AnalystSettings(provider="costguard-mock", model="analyst", pricing_version="fixture-free",
                               input_per_million_usd=0, output_per_million_usd=0).model_dump(mode="json")
    return db.get_analyst_settings()


class CandidateInput(Schema):
    baseline_id: str
    name: str = Field(default="Candidate", min_length=1, max_length=200)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1, max_length=8000)
    pricing: PricingCatalog
    limits: ExecutionLimits
    temperature: Decimal | None = Field(default=None, ge=0, le=2)


@router.get("/examples/triage")
def triage_example():
    from .examples import triage_examples
    return triage_examples()


@router.get("/artifacts/{artifact_id}/source")
def artifact_source(artifact_id: str):
    source = db.source_experiment(artifact_id)
    return {"spec": source.model_dump(mode="json") if source else None}


@router.post("/candidates", status_code=202)
def start_candidate(data: CandidateInput):
    artifact = db.get_artifact(data.baseline_id)
    source = db.source_experiment(data.baseline_id)
    if artifact is None or source is None:
        raise HTTPException(409, "No executable source. Run your application again and import the candidate evidence.")
    if artifact.suite_digest != suite_digest(source.cases) or len(artifact.cases) != len(source.cases) or any(case.status != "ok" for case in artifact.cases):
        raise HTTPException(409, "Select a complete baseline before cloning a candidate")
    values = source.model_dump(mode="json")
    values.update(data.model_dump(mode="json", exclude={"baseline_id"}))
    values["mode"] = "fixture" if data.provider == "costguard-mock" else "live"
    try:
        spec = ExperimentSpec.model_validate(values)
    except ValueError as exc:
        raise HTTPException(422, "Candidate model must have an entry in its explicit pricing catalog") from exc
    return {"job_id": db.create_job(spec)}


class ProbeInput(Schema):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    pricing: PricingCatalog


@router.post("/probes", status_code=202)
def probe(data: ProbeInput):
    from uuid import uuid4
    try:
        spec = ExperimentSpec(suite_id=f"probe-{uuid4()}", name="Explicit chat connection probe",
            provider=data.provider, model=data.model, pricing=data.pricing,
            system_prompt="Reply briefly to confirm this chat connection.", cases=[{"case_id": "probe", "prompt": "OK"}],
            limits=ExecutionLimits(max_output_tokens=64, timeout_seconds=30),
            mode="fixture" if data.provider == "costguard-mock" else "live")
    except ValueError as exc:
        raise HTTPException(422, "Supply pricing for the probe model") from exc
    return {"job_id": db.create_job(spec)}


@router.get("/analyst")
def analyst_settings():
    return {"settings": db.get_analyst_settings()}


@router.put("/analyst")
def configure_analyst(settings: AnalystSettings):
    db.save_analyst_settings(settings)
    return {"settings": settings.model_dump(mode="json")}


@router.post("/demo", status_code=202)
def start_demo():
    setup_demo()
    from .examples import triage_examples
    example = triage_examples()
    specs = [ExperimentSpec.model_validate(example["spec"])]
    specs.append(ExperimentSpec.model_validate({**example["spec"], "name": "Ticket triage longer prompt",
                                               "system_prompt": example["variants"]["longer"]}))
    return {"baseline_job_id": db.create_job(specs[0]), "candidate_job_id": db.create_job(specs[1]),
            "pricing": specs[0].pricing.model_dump(mode="json"), "policy": example["policy"]}


@router.post("/demo/setup")
def setup_demo():
    if os.environ.get("COSTGUARD_DEMO") != "1":
        raise HTTPException(404, "Demo is disabled. Start Compose with COSTGUARD_DEMO=1 and --profile demo.")
    from .bootstrap_gateway import bootstrap
    try:
        bootstrap()
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(503, "Demo gateway setup failed. Check that the demo profile is running.") from exc
    return {"status": "ready"}


@router.post("/investigations", status_code=202)
def start_investigation(data: InvestigationInput):
    saved = db.get_comparison(data.report_id)
    if saved is None:
        raise HTTPException(404, "comparison not found")
    settings = analyst_for_report(saved)
    if settings is None:
        raise HTTPException(409, "Configure an analyst model on Connections first")
    source = db.source_experiment(saved["request"]["candidate_id"])
    artifact = db.get_artifact(saved["request"]["candidate_id"])
    if source and ({case.case_id for case in source.cases} != {case.case_id for case in artifact.cases}
                   or source.suite_id != artifact.suite_id):
        source = None
    return {"investigation_id": db.create_investigation(data.report_id, data.question,
            AnalystSettings.model_validate(settings), source)}


@router.get("/investigations")
def investigations():
    return db.list_investigations()


@router.get("/investigations/{investigation_id}")
def investigation(investigation_id: str):
    result = db.get_investigation(investigation_id)
    if result is None:
        raise HTTPException(404, "investigation not found")
    return result


@router.post("/investigations/{investigation_id}/cancel")
def stop_investigation(investigation_id: str):
    if not db.cancel_investigation(investigation_id):
        raise HTTPException(409, "investigation is not active")
    return {"state": "cancelled"}


class ProposalApproval(Schema):
    system_prompt: str | None = Field(default=None, min_length=1, max_length=8000)


@router.post("/investigations/{investigation_id}/run-proposal")
def run_proposal(investigation_id: str, data: ProposalApproval | None = None):
    try:
        return {"job_id": db.run_investigation_proposal(investigation_id, data.system_prompt if data else None)}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/investigations/{investigation_id}/compare-proposal")
def compare_proposal(investigation_id: str):
    try:
        report_id = db.compare_investigation_proposal(investigation_id)
        return {"report_id": report_id, "candidate_report_id":
                db.get_investigation(investigation_id)["result"].get("candidate_comparison_id")}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class ComparisonInput(Schema):
    baseline_id: str
    candidate_id: str
    pricing: PricingCatalog
    policy: ComparisonPolicy | None = None
    monthly_requests: int | None = Field(default=None, ge=0, strict=True)


class BaselineInput(Schema):
    artifact_id: str


class ScenarioInput(Schema):
    artifact_id: str
    pricing: PricingCatalog
    monthly_requests: int = Field(ge=0, strict=True)


@router.get("/ready")
def readiness():
    try:
        db.ready()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@router.get("/gateway")
def gateway_status():
    from .investigation_worker import gateway_headers
    url = os.environ.get("GATEWAY_URL", "http://bifrost:8080")
    try:
        with httpx.Client(timeout=3, headers=gateway_headers()) as client:
            response = client.get(f"{url}/v1/models")
            response.raise_for_status()
            models = response.json().get("data", [])
        return {"status": "connected", "models": [m.get("id") for m in models],
                "settings_url": os.environ.get("BIFROST_PUBLIC_URL", "http://localhost:8080")}
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": "unavailable", "models": [], "settings_url": os.environ.get("BIFROST_PUBLIC_URL", "http://localhost:8080")}


@router.post("/suites")
def put_suite(spec: ExperimentSpec):
    return {"suite_id": db.save_suite(spec)}


@router.get("/suites")
def suites():
    return db.list_suites()


@router.get("/suites/{suite_id}")
def suite(suite_id: str):
    result = db.get_suite(suite_id)
    if result is None:
        raise HTTPException(404, "suite not found")
    return result


@router.post("/artifacts")
def import_run(artifact: ExecutionArtifact):
    try:
        artifact_id, created = db.import_artifact(artifact)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"artifact_id": artifact_id, "created": created}


@router.get("/artifacts")
def artifacts():
    return db.list_artifacts()


@router.get("/artifacts/{artifact_id}")
def artifact(artifact_id: str):
    result = db.get_artifact(artifact_id)
    if result is None:
        raise HTTPException(404, "artifact not found")
    return result


@router.put("/baselines/{suite_id}")
def promote_baseline(suite_id: str, data: BaselineInput):
    try:
        db.set_baseline(suite_id, data.artifact_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"suite_id": suite_id, "artifact_id": data.artifact_id}


@router.get("/baselines/{suite_id}")
def baseline(suite_id: str):
    artifact_id = db.get_baseline(suite_id)
    if artifact_id is None:
        raise HTTPException(404, "baseline not found")
    return {"suite_id": suite_id, "artifact_id": artifact_id}


@router.post("/jobs")
def start_job(spec: ExperimentSpec):
    return {"job_id": db.create_job(spec)}


@router.get("/jobs")
def jobs():
    return db.list_jobs()


@router.get("/jobs/{job_id}")
def job(job_id: str):
    result = db.get_job(job_id)
    if result is None:
        raise HTTPException(404, "job not found")
    return result


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    if not db.cancel_job(job_id):
        raise HTTPException(409, "job not active or not found")
    return {"job_id": job_id, "state": "cancelled"}


@router.post("/comparisons")
def compare_saved(data: ComparisonInput):
    left = db.get_artifact(data.baseline_id)
    right = db.get_artifact(data.candidate_id)
    if left is None or right is None:
        raise HTTPException(404, "baseline or candidate artifact not found")
    report = compare_artifacts(left, right, data.pricing, data.policy, data.monthly_requests)
    request_payload = json.loads(data.model_dump_json())
    report_payload = json.loads(report.model_dump_json())
    report_id = db.save_comparison(data.baseline_id, data.candidate_id, request_payload, report_payload)
    return {"id": report_id, "report": report_payload}


@router.get("/comparisons")
def comparisons():
    return db.list_comparisons()


@router.get("/comparisons/{report_id}")
def comparison(report_id: str):
    result = db.get_comparison(report_id)
    if result is None:
        raise HTTPException(404, "comparison not found")
    return result


@router.get("/comparisons/{report_id}/export")
def export_report(report_id: str, format: str = "json"):
    saved = db.get_comparison(report_id)
    if saved is None:
        raise HTTPException(404, "comparison not found")
    if format == "markdown":
        from .reporting import comparison_markdown
        return Response(comparison_markdown(saved), media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="costguard-{report_id}.md"'})
    if format != "json":
        raise HTTPException(422, "Choose json or markdown")
    bundle = {"comparison": saved, "baseline": db.get_artifact(saved["request"]["baseline_id"]).model_dump(mode="json"),
              "candidate": db.get_artifact(saved["request"]["candidate_id"]).model_dump(mode="json")}
    return Response(json.dumps(bundle, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="costguard-{report_id}.json"'})


@router.post("/scenarios")
def scenario(data: ScenarioInput):
    saved = db.get_artifact(data.artifact_id)
    if saved is None:
        raise HTTPException(404, "artifact not found")
    if (any(case.status != "ok" for case in saved.cases)
            or any(call.usage_source in {"missing", "estimated"}
                   for case in saved.cases for call in case.calls)):
        return {"status": "inconclusive", "reason": "failed cases or unobserved usage"}
    original = [case_cost(case, saved.pricing) for case in saved.cases]
    hypothetical = [case_cost(case, data.pricing) for case in saved.cases]
    if any(cost is None for cost in original + hypothetical):
        return {"status": "inconclusive", "reason": "missing usage or pricing"}
    metric = _compare(_mean(original), _mean(hypothetical), Decimal("0.00000001"))
    monthly = _compare(_mean(original) * data.monthly_requests,
                       _mean(hypothetical) * data.monthly_requests,
                       Decimal("0.00000001"))
    return {"status": "hypothetical", "cost_per_request_usd": metric,
            "monthly_cost_usd": monthly, "pricing_versions": [saved.pricing.version, data.pricing.version],
            "assumption": "Observed usage and execution behavior remain unchanged; quality and latency are not predicted."}
