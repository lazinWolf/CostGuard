"""Small local interface for the complete experiment-to-report flow."""

import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db, real_demo
from .investigation import NUMBER, evidence_numbers, unsupported_numbers

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
        "demo_enabled": os.environ.get("COSTGUARD_DEMO") == "1",
        "suite": real_demo.suite(),
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
            "output_tokens": _mean([_output_tokens(case) for case in artifact.cases])}


def _mean(values):
    known = [value for value in values if value is not None]
    return round(sum(known) / len(known)) if known and len(known) == len(values) else None


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
        "saved": report, "cases": _case_rows(report["report"], baseline, candidate, inputs),
        "baseline": baseline, "candidate": candidate,
        "breakdown": _breakdown(report["request"]["pricing"], baseline, candidate),
        "limits": _limits(report["request"].get("policy")),
        "related": db.related_investigations(report_id),
    })


def _breakdown(pricing: dict, baseline, candidate) -> dict | None:
    """Mean tokens and cost per case, split into input and output, for both sides."""
    prices = {(entry["provider"], entry["model"]): entry for entry in pricing["entries"]}
    sides = {}
    for side, artifact in (("baseline", baseline), ("candidate", candidate)):
        calls = [call for case in artifact.cases for call in case.calls]
        if not calls or any(call.input_tokens is None or call.output_tokens is None
                            or (call.provider, call.model) not in prices for call in calls):
            return None
        count = Decimal(len(artifact.cases))
        price = lambda call, kind: Decimal(str(prices[(call.provider, call.model)][f"{kind}_per_million_usd"]))
        sides[side] = {
            "input_tokens": sum(call.input_tokens for call in calls) / count,
            "output_tokens": sum(call.output_tokens for call in calls) / count,
            "input_cost": sum(call.input_tokens * price(call, "input") for call in calls) / count / 1000000,
            "output_cost": sum(call.output_tokens * price(call, "output") for call in calls) / count / 1000000,
        }
    rows = []
    for kind in ("input", "output"):
        before, after = sides["baseline"], sides["candidate"]
        rows.append({"kind": kind, "tokens_before": round(before[f"{kind}_tokens"]),
                     "tokens_after": round(after[f"{kind}_tokens"]),
                     "cost_before": before[f"{kind}_cost"], "cost_after": after[f"{kind}_cost"],
                     "cost_delta": after[f"{kind}_cost"] - before[f"{kind}_cost"]})
    total = sum(abs(row["cost_delta"]) for row in rows)
    for row in rows:
        row["share"] = round(abs(row["cost_delta"]) / total * 100) if total else 0
    driver = max(rows, key=lambda row: abs(row["cost_delta"])) if total else None
    return {"rows": rows, "driver": driver, "explanation": _explain(driver, baseline, candidate),
            "prices": [{"model": f"{entry['provider']}/{entry['model']}",
                        "input": entry["input_per_million_usd"], "output": entry["output_per_million_usd"]}
                       for entry in pricing["entries"]]}


def _output_tokens(case) -> int | None:
    if case is None or any(call.output_tokens is None for call in case.calls):
        return None
    return sum(call.output_tokens for call in case.calls)


def _explain(driver: dict | None, baseline, candidate) -> list[str]:
    """Plain sentences about what moved the cost, in the direction it actually moved."""
    if driver is None:
        return []
    more = driver["cost_delta"] > 0
    sentences = [f"{driver['share']}% of the change comes from {driver['kind']} tokens: "
                 f"{driver['tokens_before']} → {driver['tokens_after']} per case on average."]
    if driver["kind"] == "output":
        sentences.append("The model is writing longer responses." if more else "The model is writing shorter responses.")
    else:
        sentences.append("More text is sent to the model, for example a longer prompt or more context." if more
                         else "Less text is sent to the model, for example a shorter prompt.")
    thinking = [artifact.name for artifact in (baseline, candidate) if any(case.reasoning for case in artifact.cases)]
    if thinking:
        sentences.append("This model reasons before it answers. Its hidden reasoning is billed as output tokens "
                         "but is not part of the answer, so a one-word answer can still cost a hundred tokens. "
                         "Expand an answer below to see the reasoning.")
    return sentences


LIMITS = {
    "max_cost_increase_percent": "cost per request may rise by at most {}%",
    "max_candidate_cost_per_request_usd": "cost per request at most ${}",
    "max_monthly_cost_usd": "projected monthly cost at most ${}",
    "min_candidate_quality_score": "at least {pct}% of answers correct",
    "max_quality_drop": "correct answers may fall at most {pct} percentage points below the baseline",
    "max_candidate_mean_latency_ms": "mean latency at most {} ms",
    "max_candidate_model_calls_per_case": "at most {} model calls per case",
    "max_candidate_retries_per_case": "at most {} retries per case",
    "max_candidate_tool_steps_per_case": "at most {} tool steps per case",
    "max_candidate_agent_steps_per_case": "at most {} agent steps per case",
}


def _limits(policy: dict | None) -> list[str]:
    result = []
    for key, value in (policy or {}).items():
        if value is None or key not in LIMITS:
            continue
        number = Decimal(str(value)).normalize()
        result.append(LIMITS[key].format(format(number, "f"), pct=format((number * 100).normalize(), "f")))
    return result


def _case_rows(report: dict, baseline, candidate, inputs: dict | None = None) -> list[dict]:
    """Pair both sides of each case, largest cost increase first when costs are known."""
    costs = {item["case_id"]: item["cost_usd"]
             for item in (report.get("economic") or {}).get("case_costs", [])}
    left = {case.case_id: case for case in baseline.cases}
    right = {case.case_id: case for case in candidate.cases}
    order = list(costs) + [case_id for case_id in right if case_id not in costs]
    order += [case_id for case_id in left if case_id not in order]
    return [{"case_id": case_id, "cost": costs.get(case_id),
             "before": left.get(case_id), "after": right.get(case_id),
             "before_tokens": _output_tokens(left.get(case_id)),
             "after_tokens": _output_tokens(right.get(case_id)),
             "input": (inputs or {}).get(case_id)} for case_id in order]


@router.get("/artifacts/{artifact_id}", response_class=HTMLResponse)
def artifact_page(request: Request, artifact_id: str):
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(404, "artifact not found")
    return templates.TemplateResponse(request, "artifact.html", {"artifact": artifact})
