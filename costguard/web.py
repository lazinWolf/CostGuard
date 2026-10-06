"""Small local interface for the complete experiment-to-report flow."""

import json
import os
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["money"] = lambda value: format(Decimal(str(value)), ".8f") if value is not None else "Unknown"
root = Path(__file__).resolve().parent.parent


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "jobs": db.list_jobs()[:8], "reports": db.list_comparisons()[:8],
        "artifacts": db.list_artifacts()[:8],
        "investigations": db.list_investigations()[:8],
        "demo_enabled": os.environ.get("COSTGUARD_DEMO") == "1",
    })


@router.get("/connections", response_class=HTMLResponse)
def connections(request: Request):
    from .api import gateway_status
    return templates.TemplateResponse(request, "connections.html", {
        "gateway": gateway_status(), "analyst": db.get_analyst_settings(),
    })


@router.get("/investigations", response_class=HTMLResponse)
def investigations_page(request: Request, report_id: str | None = None):
    return templates.TemplateResponse(request, "investigations.html", {
        "reports": db.list_comparisons(), "selected_report": report_id,
        "analyst": db.get_analyst_settings(), "investigations": db.list_investigations(),
    })


@router.get("/investigations/{investigation_id}", response_class=HTMLResponse)
def investigation_page(request: Request, investigation_id: str):
    row = db.get_investigation(investigation_id)
    if row is None:
        raise HTTPException(404, "investigation not found")
    proposal_job = db.get_job(row["proposal_job_id"]) if row["proposal_job_id"] else None
    return templates.TemplateResponse(request, "investigation.html", {
        "investigation": row, "proposal_job": proposal_job,
    })


@router.get("/experiments", response_class=HTMLResponse)
def experiments(request: Request):
    example = (root / "examples" / "experiment_baseline.json").read_text()
    return templates.TemplateResponse(request, "experiments.html", {
        "example": example, "jobs": db.list_jobs()[:20], "suites": db.list_suites(),
    })


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return templates.TemplateResponse(request, "job.html", {"job": job})


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request):
    example = json.loads((root / "examples" / "experiment_baseline.json").read_text())
    return templates.TemplateResponse(request, "reports.html", {
        "artifacts": db.list_artifacts(), "reports": db.list_comparisons(),
        "pricing": json.dumps(example["pricing"], indent=2),
    })


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_page(request: Request, report_id: str):
    report = db.get_comparison(report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    return templates.TemplateResponse(request, "report.html", {"saved": report})


@router.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
def artifact_page(request: Request, artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(404, "artifact not found")
    return templates.TemplateResponse(request, "artifact.html", {"artifact": artifact})


@router.get("/scenarios", response_class=HTMLResponse)
def scenarios(request: Request):
    example = json.loads((root / "examples" / "experiment_baseline.json").read_text())
    return templates.TemplateResponse(request, "scenarios.html", {
        "artifacts": db.list_artifacts(), "pricing": json.dumps(example["pricing"], indent=2),
    })
