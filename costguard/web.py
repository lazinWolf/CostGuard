"""The browser interface. Pages are thin: they ask the service for a check and present it."""

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import checks, db, gateway, service
from .analyst_view import analyst_view
from .diagnosis import format_usd, headline, percent

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Plain numbers for people: "1.0000" -> "1", "0.5000" -> "0.5".
templates.env.filters["num"] = lambda value: "–" if value is None else format(Decimal(str(value)).normalize(), "f")
templates.env.filters["usd"] = format_usd
templates.env.filters["percent"] = percent

def _page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


@router.get("/", response_class=HTMLResponse)
def checks_page(request: Request):
    return _page(request, "checks.html", checks=service.list_checks())


@router.get("/checks/new", response_class=HTMLResponse)
def new_check_page(request: Request, preset: str = "support-triage"):
    return _page(request, "new_check.html", suite=checks.preset(preset), presets=checks.presets(),
                 current=preset if preset in checks.PRESETS else "support-triage", analyst=db.get_analyst_settings())


@router.get("/checks/running/{candidate_job_id}", response_class=HTMLResponse)
def running_check_page(request: Request, candidate_job_id: str):
    progress = service.check_progress(candidate_job_id)
    if progress["report_id"]:
        return RedirectResponse(f"/checks/{progress['report_id']}")
    return _page(request, "running.html", candidate_job_id=candidate_job_id, progress=progress)


@router.get("/checks/{report_id}", response_class=HTMLResponse)
def check_page(request: Request, report_id: str):
    check = service.get_check(report_id)
    return _page(request, "check.html", check=check,
                 stats=headline(check.report, check.breakdown),
                 analyst=analyst_view(check), presets=_price_presets(),
                 prompts={"baseline": check.baseline.configuration.get("system_prompt", ""),
                          "candidate": check.candidate.configuration.get("system_prompt", "")})


@router.get("/connections", response_class=HTMLResponse)
def connections(request: Request):
    return _page(request, "connections.html", gateway=gateway.status(), analyst=db.get_analyst_settings())


# Earlier versions had separate report and investigation pages; their links still work.
@router.get("/reports")
@router.get("/investigations")
def old_list(report_id: str | None = None):
    return RedirectResponse(f"/checks/{quote(report_id)}#explain" if report_id else "/")


@router.get("/reports/{report_id}")
def old_report(report_id: str):
    return RedirectResponse(f"/checks/{quote(report_id)}")


@router.get("/investigations/{investigation_id}")
def old_investigation(investigation_id: str):
    row = db.get_investigation(investigation_id)
    if row is None:
        raise HTTPException(404, "investigation not found")
    return RedirectResponse(f"/checks/{quote(row['report_id'])}#explain")


def _price_presets() -> list[dict]:
    """Recorded catalog prices a person can try with one click on the what-if form."""
    path = service.EXAMPLES / "ci" / "pricing.json"
    if not path.exists():
        return []
    catalog = json.loads(path.read_text())
    return [{"label": catalog["version"], "input": entry["input_per_million_usd"],
             "output": entry["output_per_million_usd"]} for entry in catalog["entries"][:1]]


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _page(request, "job.html", job=job)


@router.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
def artifact_page(request: Request, artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(404, "artifact not found")
    return _page(request, "artifact.html", artifact=artifact)
