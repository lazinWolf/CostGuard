"""What the 'Explain and fix' section of a check page shows, prepared from the stored investigation.

Pure presentation: the analyst's conclusion with links and the number check, its activity in plain words,
and the measured fixes. The page template only lays this out.
"""

from decimal import Decimal
from urllib.parse import quote

from . import db, service
from .investigation import NUMBER, PROPOSAL_REMINDER, evidence_numbers, proposals_of, unsupported_numbers

DEFAULT_QUESTION = ("Explain the cost and quality change, inspect the cases that changed most, "
                    "and propose a prompt experiment that restores quality at lower cost.")


def analyst_view(check: service.Check) -> dict:
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
        answer=answer(row), timeline=_timeline(row["result"]), proposals=proposals, fixes=fixes,
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


def answer(row: dict) -> dict | None:
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
