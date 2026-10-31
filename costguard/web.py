"""The browser interface. Pages are thin: they ask the service for a check and present it."""

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import checks, db, gateway, service
from .diagnosis import format_usd, headline, percent
from .investigation import NUMBER, PROPOSAL_REMINDER, evidence_numbers, proposals_of, unsupported_numbers

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Plain numbers for people: "1.0000" -> "1", "0.5000" -> "0.5".
templates.env.filters["num"] = lambda value: "–" if value is None else format(Decimal(str(value)).normalize(), "f")
templates.env.filters["usd"] = format_usd
templates.env.filters["percent"] = percent

DEFAULT_QUESTION = ("Explain the cost and quality change, inspect the cases that changed most, "
                    "and propose a prompt experiment that restores quality at lower cost.")


def _page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


@router.get("/", response_class=HTMLResponse)
def checks_page(request: Request):
    return _page(request, "checks.html", checks=service.list_checks())


@router.get("/checks/new", response_class=HTMLResponse)
def new_check_page(request: Request):
    return _page(request, "new_check.html", suite=checks.preset(), analyst=db.get_analyst_settings())


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
                 analyst=_analyst_view(check), presets=_price_presets(),
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


def _analyst_view(check: service.Check) -> dict:
    """Everything the 'Explain and fix' section shows: nothing yet, a live run, or the finished chain."""
    investigations = check.related["investigations"]
    view = {"settings": db.get_analyst_settings(), "question": DEFAULT_QUESTION, "investigation": None}
    if not investigations:
        view["steps"] = ["current", "", ""]
        return view
    row = db.get_investigation(investigations[0]["id"])
    proposals = proposals_of(row["result"])
    fixes = service.fix_rows(row)
    measured = bool(fixes) and all(fix["state"] in {"measured", "failed"} for fix in fixes)
    winner = next((fix for fix in fixes if fix["winner"]), None)
    view.update(
        investigation=row, active=row["state"] in {"queued", "running"},
        answer=_answer(row), timeline=_timeline(row["result"]), proposals=proposals, fixes=fixes,
        testing=bool(row["fixes"]) and not measured, measured=measured, winner=winner,
        fixes_key=[[fix["state"], fix["progress"], fix["report_id"]] for fix in fixes],
        scoreboard=[_variant("Baseline", check.baseline, check.report, "baseline"),
                    _variant("This version", check.candidate, check.report, "candidate")],
        steps=["done" if row["state"] == "completed" else "current",
               "done" if measured else "current" if proposals and row["state"] == "completed" else "",
               "done" if winner else ""])
    return view


def _variant(label: str, artifact, report: dict, side: str) -> dict:
    """One row of the baseline / this version summary."""
    economic = report.get("economic") or {}
    return {"label": label, "id": str(artifact.artifact_id), "name": artifact.name,
            "cost": (economic.get("cost_per_request_usd") or {}).get(side),
            "quality": (economic.get("quality_score") or {}).get(side), "cases": len(artifact.cases),
            "status": report["status"] if side == "candidate" else None,
            "words": len(artifact.configuration.get("system_prompt", "").split())}


def _answer(row: dict) -> dict | None:
    """The analyst's conclusion with evidence links and a check of every number it quotes."""
    result = row["result"]
    conclusion = result.get("conclusion")
    if not conclusion:
        return None
    numbers = evidence_numbers([result.get("digest")] + [event.get("result") for event in result.get("events", [])
                                                         if event.get("tool") in {"inspect_comparison", "inspect_case"}])
    report = f"/checks/{row['report_id']}"

    def link(reference: str) -> dict:
        if reference.startswith("case:"):
            case_id = reference.removeprefix("case:")
            return {"label": f"case {case_id}", "href": f"{report}#case-{quote(case_id)}"}
        if reference == "proposal":
            return {"label": "proposal", "href": "#proposal"}
        return {"label": "check" if reference == "comparison" else reference, "href": report}

    return {"summary": conclusion["summary"],
            "summary_unsupported": unsupported_numbers(conclusion["summary"], numbers),
            "findings": [{"text": finding["text"], "links": [link(ref) for ref in finding["evidence"]],
                          "unsupported": unsupported_numbers(finding["text"], numbers),
                          "quotes_numbers": NUMBER.search(finding["text"]) is not None}
                         for finding in conclusion["findings"]],
            "hypotheses": conclusion.get("hypotheses", []),
            "next_steps": conclusion.get("next_steps", [])}


TOOL_STEPS = {
    "inspect_comparison": "Read the comparison: costs, quality, policy and configurations",
    "propose_fixes": "Proposed system prompts to test",
    "propose_experiment": "Proposed a new system prompt to test",  # Before v4 the analyst proposed one.
    "finish_investigation": "Wrote its findings",
    "format_reminder": "Answered in prose instead of a tool call; CostGuard asked it to use the tools",
    "unknown": "Asked for a tool that does not exist",
}


def _timeline(result: dict) -> list[dict]:
    """The analyst's tool activity in plain language."""
    steps = []
    for event in result.get("events", []):
        output = event.get("result") or {}
        error = output.get("error") if isinstance(output, dict) else None
        if event["tool"] == "finish_investigation" and error == PROPOSAL_REMINDER:
            steps.append({"tool": event["tool"], "error": None, "detail": output, "saw": None,
                          "text": "Tried to finish without a proposal; CostGuard asked it to propose one first"})
            continue
        if event["tool"] == "inspect_case":
            text = f"Inspected case {output.get('evidence_ref', '').removeprefix('case:')}" if not error else "Tried to inspect a case"
        else:
            text = TOOL_STEPS.get(event["tool"], event["tool"])
        steps.append({"tool": event["tool"], "text": text, "error": error, "detail": output,
                      "saw": None if error else _saw(event["tool"], output)})
    return steps


def _saw(tool: str, output: dict) -> str | None:
    """One line of what a tool returned, so nobody has to read raw JSON."""
    if tool == "inspect_comparison":
        metrics = output.get("metrics") or {}
        cost = (metrics.get("cost_per_request_usd") or {}).get("delta_percent")
        quality = metrics.get("quality_score") or {}
        parts = [f"verdict {output.get('status')}"]
        if cost is not None:
            parts.append(f"cost {'+' if float(cost) > 0 else ''}{cost}%")
        if quality:
            parts.append(f"quality {format(Decimal(str(quality.get('baseline'))).normalize(), 'f')} → "
                         f"{format(Decimal(str(quality.get('candidate'))).normalize(), 'f')}")
        parts.append(f"{output.get('case_count')} cases")
        return "Saw " + " · ".join(parts)
    if tool == "inspect_case":
        sides = []
        for side in ("baseline", "candidate"):
            case = output.get(side) or {}
            tokens = sum(call.get("output_tokens") or 0 for call in case.get("calls", []))
            mark = "✓" if case.get("quality_score") in (1, "1", 1.0) else "✗" if case.get("quality_score") is not None else ""
            answer = (case.get("output") or "").strip().replace("\n", " ")
            sides.append(f"{side} {mark} “{answer[:50]}{'…' if len(answer) > 50 else ''}” ({tokens} output tokens)")
        return "Saw " + " · ".join(sides)
    if tool in {"propose_fixes", "propose_experiment"}:
        words = [len((fix.get("system_prompt") or "").split()) for fix in output.get("fixes", [output])]
        return f"{len(words)} prompt{'' if len(words) == 1 else 's'} of {' / '.join(map(str, words))} words, to run on the same {output.get('cases')} cases"
    return None


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
