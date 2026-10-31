"""Deterministic explanations of a comparison: what changed, why it cost more, which limits bit.

Pure functions over saved evidence. The browser pages, the analyst and the MCP server all
read from here, so one fact is worded one way everywhere.
"""

from decimal import Decimal


def breakdown(pricing: dict, baseline, candidate) -> dict | None:
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
    return {"rows": rows, "driver": driver, "explanation": explain(driver, baseline, candidate),
            "prices": [{"model": f"{entry['provider']}/{entry['model']}",
                        "input": entry["input_per_million_usd"], "output": entry["output_per_million_usd"]}
                       for entry in pricing["entries"]]}


def output_tokens(case) -> int | None:
    """Output tokens a case used, or None when any call lacks usage."""
    if case is None or any(call.output_tokens is None for call in case.calls):
        return None
    return sum(call.output_tokens for call in case.calls)


def mean_output_tokens(cases) -> int | None:
    """Mean output tokens per case, or None unless every case reports usage."""
    counts = [output_tokens(case) for case in cases]
    known = [count for count in counts if count is not None]
    return round(sum(known) / len(known)) if known and len(known) == len(counts) else None


def explain(driver: dict | None, baseline, candidate) -> list[str]:
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


def describe_limits(policy: dict | None) -> list[str]:
    result = []
    for key, value in (policy or {}).items():
        if value is None or key not in LIMITS:
            continue
        number = Decimal(str(value)).normalize()
        if key == "max_quality_drop" and number == 0:
            result.append("no fewer correct answers than the baseline")
            continue
        result.append(LIMITS[key].format(format(number, "f"), pct=format((number * 100).normalize(), "f")))
    return result


def case_rows(report: dict, baseline, candidate, inputs: dict | None = None) -> list[dict]:
    """Pair both sides of each case, largest cost increase first when costs are known."""
    costs = {item["case_id"]: item["cost_usd"]
             for item in (report.get("economic") or {}).get("case_costs", [])}
    left = {case.case_id: case for case in baseline.cases}
    right = {case.case_id: case for case in candidate.cases}
    order = list(costs) + [case_id for case_id in right if case_id not in costs]
    order += [case_id for case_id in left if case_id not in order]
    return [{"case_id": case_id, "cost": costs.get(case_id),
             "before": left.get(case_id), "after": right.get(case_id),
             "before_tokens": output_tokens(left.get(case_id)),
             "after_tokens": output_tokens(right.get(case_id)),
             "input": (inputs or {}).get(case_id)} for case_id in order]
