"""Versioned API backed by the same analysis functions as the CLI and MCP server."""

import json
import os
from decimal import Decimal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import Field

from . import db
from .analysis import _compare, _mean
from .economics import case_cost, compare_artifacts
from .execution import ExecutionArtifact, ExperimentSpec
from .models import ComparisonPolicy, PricingCatalog, Schema

router = APIRouter(prefix="/api/v1")


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
    url = os.environ.get("GATEWAY_URL", "http://bifrost:8080")
    try:
        with httpx.Client(timeout=3) as client:
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
