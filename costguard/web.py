"""Small local interface for the complete experiment-to-report flow."""

import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db, gateway, real_demo
from .diagnosis import breakdown, case_rows, describe_limits, mean_output_tokens
from .investigation import NUMBER, PROPOSAL_REMINDER, evidence_numbers, unsupported_numbers

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["money"] = lambda value: format(Decimal(str(value)), ".8f") if value is not None else "Unknown"
# Plain numbers for people: "1.0000" -> "1", "0.5000" -> "0.5".
templates.env.filters["num"] = lambda value: "–" if value is None else format(Decimal(str(value)).normalize(), "f")
templates.env.filters["usd"] = lambda value: "Unknown" if value is None else (
    "$" + format(Decimal(str(value)), ",.2f") if abs(Decimal(str(value))) >= 1
    else "$" + format(Decimal(str(value)).normalize(), "f"))


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "reports": db.list_comparisons(6), "investigations": db.list_investigations(6),
        "analyst": db.get_analyst_settings(),
        "demo_enabled": os.environ.get("COSTGUARD_DEMO") == "1",
        "suite": real_demo.suite(),
    })


@router.get("/connections", response_class=HTMLResponse)
def connections(request: Request):
    return templates.TemplateResponse(request, "connections.html", {
        "gateway": gateway.status(), "analyst": db.get_analyst_settings(),
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
    original = db.get_comparison(row["report_id"])
    outcome = db.get_comparison(row["proposal_report_id"]) if row["proposal_report_id"] else None
    baseline = db.get_artifact(original["request"]["baseline_id"])
    candidate = db.get_artifact(original["request"]["candidate_id"])
    return templates.TemplateResponse(request, "investigation.html", {
        "investigation": row, "proposal_job": proposal_job,
        "timeline": _timeline(row["result"]), "answer": _answer(row),
        "variants": [_variant("Baseline", baseline, original["report"], "baseline"),
                     _variant("Candidate", candidate, original["report"], "candidate")]
                    + ([_variant("Proposal", db.get_artifact(outcome["request"]["candidate_id"]),
                                 outcome["report"], "candidate")] if outcome else []),
        "prompts": {"baseline": baseline.configuration.get("system_prompt", ""),
                    "candidate": candidate.configuration.get("system_prompt", "")},
    })


def _variant(label: str, artifact, report: dict, side: str) -> dict:
    """One column of the baseline / candidate / proposal summary."""
    economic = report.get("economic") or {}
    cost = (economic.get("cost_per_request_usd") or {}).get(side)
    quality = (economic.get("quality_score") or {}).get(side)
    return {"label": label, "id": str(artifact.artifact_id), "name": artifact.name,
            "cost": cost, "quality": quality, "cases": len(artifact.cases),
            "status": report["status"] if side == "candidate" else None,
            "output_tokens": mean_output_tokens(artifact.cases)}




def _answer(row: dict) -> dict | None:
    """The analyst's conclusion with evidence links and a check of every number it quotes."""
    result = row["result"]
    conclusion = result.get("conclusion")
    if not conclusion:
        return None
    numbers = evidence_numbers([event.get("result") for event in result.get("events", [])
                                if event.get("tool") in {"inspect_comparison", "inspect_case"}])
    report = f"/reports/{row['report_id']}"

    def link(reference: str) -> dict:
        if reference.startswith("case:"):
            case_id = reference.removeprefix("case:")
            return {"label": f"case {case_id}", "href": f"{report}#case-{quote(case_id)}"}
        if reference == "proposal":
            return {"label": "proposal", "href": "#proposal"}
        return {"label": "report" if reference == "comparison" else reference, "href": report}

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
    "propose_experiment": "Proposed a new system prompt to test",
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
    if tool == "propose_experiment":
        return f"Prompt of {len((output.get('system_prompt') or '').split())} words, to run on the same {output.get('cases')} cases"
    return None


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return templates.TemplateResponse(request, "job.html", {"job": job})


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request):
    return templates.TemplateResponse(request, "reports.html", {
        "reports": db.list_comparisons(),
    })


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_page(request: Request, report_id: str):
    report = db.get_comparison(report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    baseline = db.get_artifact(report["request"]["baseline_id"])
    candidate = db.get_artifact(report["request"]["candidate_id"])
    source = db.source_experiment(str(candidate.artifact_id)) or db.source_experiment(str(baseline.artifact_id))
    inputs = {case.case_id: case for case in source.cases} if source else {}
    return templates.TemplateResponse(request, "report.html", {
        "saved": report, "cases": case_rows(report["report"], baseline, candidate, inputs),
        "baseline": baseline, "candidate": candidate,
        "breakdown": breakdown(report["request"]["pricing"], baseline, candidate),
        "limits": describe_limits(report["request"].get("policy")),
        "related": db.related_investigations(report_id),
    })




@router.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
def artifact_page(request: Request, artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(404, "artifact not found")
    return templates.TemplateResponse(request, "artifact.html", {"artifact": artifact})
