"""Versioned API backed by the same analysis functions as the CLI and MCP server."""

from fastapi import APIRouter, HTTPException
from pydantic import Field

from . import db, gateway, real_demo, service
from .execution import ExecutionArtifact, ExperimentSpec
from .investigation import AnalystSettings, InvestigationInput
from .models import ComparisonPolicy, PricingCatalog, Schema
from .real_demo import RealDemoInput

router = APIRouter(prefix="/api/v1")


@router.get("/analyst")
def analyst_settings():
    return {"settings": db.get_analyst_settings()}


@router.put("/analyst")
def configure_analyst(settings: AnalystSettings):
    db.save_analyst_settings(settings)
    return {"settings": settings.model_dump(mode="json")}


@router.post("/demo", status_code=202)
def start_demo():
    return service.start_mock_demo()


@router.post("/demo/real", status_code=202)
def start_real_demo(data: RealDemoInput):
    """Run the support-triage suite on a gateway model you choose; may incur provider charges."""
    return real_demo.start(data)


@router.post("/investigations", status_code=202)
def start_investigation(data: InvestigationInput):
    return {"investigation_id": service.start_investigation(data.report_id, data.question)}


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


@router.post("/investigations/{investigation_id}/run-proposal")
def run_proposal(investigation_id: str):
    return {"job_id": service.run_proposal(investigation_id)}


@router.post("/investigations/{investigation_id}/compare-proposal")
def compare_proposal(investigation_id: str):
    return {"report_id": service.compare_proposal(investigation_id)}


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
    return gateway.status()


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
        raise service.Conflict(str(exc)) from exc
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
    return service.compare(data.baseline_id, data.candidate_id, data.pricing, data.policy, data.monthly_requests)


@router.get("/comparisons")
def comparisons():
    return db.list_comparisons()


@router.get("/comparisons/{report_id}")
def comparison(report_id: str):
    result = db.get_comparison(report_id)
    if result is None:
        raise HTTPException(404, "comparison not found")
    return result


@router.post("/scenarios")
def scenario(data: ScenarioInput):
    return service.reprice_run(data.artifact_id, data.pricing, data.monthly_requests)
