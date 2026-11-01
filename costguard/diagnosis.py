"""Deterministic explanations of a comparison: what changed, why it cost more, which limits bit.

Pure functions over saved evidence. The browser pages, the analyst and the MCP server all
read from here, so one fact is worded one way everywhere.
"""

from decimal import Decimal
from difflib import SequenceMatcher
from typing import Callable, NamedTuple


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


def format_usd(value) -> str:
    """Money for people: cents at a dollar or more, otherwise every significant digit."""
    if value is None:
        return "Unknown"
    amount = Decimal(str(value))
    if abs(amount) >= 1:
        return "$" + format(amount, ",.2f")
    if amount:  # Six significant digits: enough to tell prices apart, short enough for a repeating decimal.
        amount = amount.quantize(Decimal(1).scaleb(amount.adjusted() - 5))
    return "$" + format(amount.normalize(), "f")


def _number(value) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def percent(value) -> str:
    amount = Decimal(str(value))
    return "0%" if amount == 0 else f"{amount:+,.2f}%"


class Limit(NamedTuple):
    """One policy limit: how it reads, what the report measured, and when it is broken."""
    text: str                                  # {} is the limit, {pct} the limit as a percentage
    measure: Callable[[dict], Decimal | None]  # None when the report cannot say
    show: Callable[[dict, Decimal], str]
    broken: Callable[[Decimal, Decimal], bool] = lambda measured, limit: measured > limit


def _dig(report: dict, *path):
    for key in path:
        report = (report or {}).get(key)
    return None if report is None else Decimal(str(report))


def _quality_scores(report: dict) -> tuple[Decimal, Decimal] | None:
    """Baseline and candidate score, only when every case was scored on both sides."""
    economic = report.get("economic") or {}
    scores = economic.get("quality_score")
    if not scores or economic.get("quality_cases_compared") != report.get("baseline_case_count"):
        return None
    return Decimal(str(scores["baseline"])), Decimal(str(scores["candidate"]))


def _correct(report: dict, score: Decimal) -> str:
    return f"{round(score * report['baseline_case_count'])}/{report['baseline_case_count']} correct"


def _quality_drop(report: dict) -> Decimal | None:
    scores = _quality_scores(report)
    return None if scores is None else scores[0] - scores[1]


def _candidate_quality(report: dict) -> Decimal | None:
    scores = _quality_scores(report)
    return None if scores is None else scores[1]


LIMITS: dict[str, Limit] = {
    "max_cost_increase_percent": Limit(
        "cost per request may rise by at most {}%",
        lambda r: _dig(r, "economic", "cost_per_request_usd", "delta_percent"),
        lambda r, v: percent(v)),
    "max_candidate_cost_per_request_usd": Limit(
        "cost per request at most ${}",
        lambda r: _dig(r, "economic", "cost_per_request_usd", "candidate"),
        lambda r, v: format_usd(v)),
    "max_monthly_cost_usd": Limit(
        "projected monthly cost at most ${}",
        lambda r: _dig(r, "economic", "monthly_projection", "cost_usd", "candidate"),
        lambda r, v: format_usd(v)),
    "min_candidate_quality_score": Limit(
        "at least {pct}% of answers correct", _candidate_quality,
        lambda r, v: _correct(r, v), lambda measured, limit: measured < limit),
    "max_quality_drop": Limit(
        "correct answers may fall at most {pct} percentage points below the baseline", _quality_drop,
        lambda r, v: f"{_correct(r, _quality_scores(r)[0])} → {_correct(r, _quality_scores(r)[1])}"),
    "max_candidate_mean_latency_ms": Limit(
        "mean latency at most {} ms", lambda r: _dig(r, "economic", "mean_latency_ms", "candidate"),
        lambda r, v: f"{v:,.0f} ms"),
    "max_candidate_model_calls_per_case": Limit(
        "at most {} model calls per case", lambda r: _dig(r, "model_calls", "candidate"),
        lambda r, v: _number(v)),
    "max_candidate_retries_per_case": Limit(
        "at most {} retries per case", lambda r: _dig(r, "retries", "candidate"),
        lambda r, v: _number(v)),
    "max_candidate_tool_steps_per_case": Limit(
        "at most {} tool steps per case", lambda r: _dig(r, "tool_steps", "candidate"),
        lambda r, v: _number(v)),
    "max_candidate_agent_steps_per_case": Limit(
        "at most {} agent steps per case", lambda r: _dig(r, "agent_steps", "candidate"),
        lambda r, v: _number(v)),
}


def limit_text(key: str, value) -> str:
    number = Decimal(str(value)).normalize()
    if key == "max_quality_drop" and number == 0:
        return "no fewer correct answers than the baseline"
    return LIMITS[key].text.format(format(number, "f"), pct=format((number * 100).normalize(), "f"))


def limit_rows(report: dict, policy: dict | None) -> list[dict]:
    """Each limit that was set beside what was measured: status is pass, fail or unknown."""
    rows = []
    for key, limit in LIMITS.items():  # A fixed order: cost first, then quality, then behavior.
        value = (policy or {}).get(key)
        if value is None:
            continue
        measured = limit.measure(report)
        status = "unknown" if measured is None else "fail" if limit.broken(measured, Decimal(str(value))) else "pass"
        rows.append({"key": key, "limit": limit_text(key, value), "status": status,
                     "measured": "not measured" if measured is None else limit.show(report, measured)})
    return rows


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


def _lost_quality(row: dict) -> bool:
    before, after = row["before"], row["after"]
    scores = [getattr(case, "quality_score", None) for case in (before, after)]
    return None not in scores and scores[1] < scores[0]


def _cost_increase(row: dict) -> Decimal:
    return Decimal(str(row["cost"]["delta"])) if row["cost"] else Decimal(0)


def changed_cases(rows: list[dict], limit: int = 4, excerpt: int = 300) -> list[dict]:
    """The cases worth reading first: the ones that lost correctness, then the ones whose cost grew most.

    `rows` come from `case_rows`. Each case carries the message, the expected answer and both outputs, so a
    reader (or the analyst) can see what changed without opening the cases one by one.
    """
    ranked = sorted(rows, key=lambda row: (not _lost_quality(row), -_cost_increase(row)))

    def side(case, tokens):
        return None if case is None else {"output": (case.output or "")[:excerpt], "output_tokens": tokens,
                                          "quality_score": case.quality_score, "status": case.status}

    return [{"case_id": row["case_id"], "lost_correctness": _lost_quality(row), "cost_usd": row["cost"],
             "message": row["input"].prompt[:excerpt] if row["input"] else None,
             "expected": row["input"].expected_text if row["input"] else None,
             "baseline": side(row["before"], row["before_tokens"]),
             "candidate": side(row["after"], row["after_tokens"])} for row in ranked[:limit]]


def prompt_change(before: str, after: str) -> dict:
    """How the system prompt grew or shrank, in words: every prompt word is paid on every request."""
    left, right = before.split(), after.split()
    kept = sum(block.size for block in SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks())
    return {"baseline_words": len(left), "candidate_words": len(right),
            "words_added": len(right) - kept, "words_removed": len(left) - kept}


def pick_winner(fixes: list[dict]) -> str | None:
    """The name of the cheapest fix that passes every limit; ties go to the shorter prompt.

    Deterministic on purpose: the analyst proposes, measurements and this rule decide. None when no fix passes.
    """
    passing = [fix for fix in fixes if fix.get("status") == "pass" and fix.get("cost") is not None]
    if not passing:
        return None
    return min(passing, key=lambda fix: (Decimal(str(fix["cost"])), fix["words"]))["name"]


def judge_spend(*artifacts) -> dict | None:
    """What the judge's calls cost across runs, or None when nothing was judged.
    This is never part of the workload cost; `complete` is false when some call reported no usage."""
    judgements = [case.judgement for artifact in artifacts for case in artifact.cases if case.judgement]
    if not judgements:
        return None
    return {"calls": len(judgements), "model": judgements[0].model,
            "cost": sum((item.cost_usd for item in judgements if item.cost_usd is not None), Decimal(0)),
            "complete": all(item.cost_usd is not None for item in judgements)}


def correct_answers(baseline, candidate, cases: int | None) -> str | None:
    """'6/8 → 0/8 correct', or None when no quality scores were compared."""
    if baseline is None or candidate is None or not cases:
        return None
    return f"{round(Decimal(str(baseline)) * cases)}/{cases} → {round(Decimal(str(candidate)) * cases)}/{cases} correct"


def _tone(change, worse_when: str) -> str:
    """Whether a change is good or bad news, for colouring: flat when nothing moved."""
    if change is None or Decimal(str(change)) == 0:
        return "flat"
    rising = Decimal(str(change)) > 0
    return "bad" if rising == (worse_when == "up") else "good"


def headline(report: dict, breakdown_: dict | None) -> list[dict]:
    """The numbers a reader sees first: what it costs, how many answers are right, and the output behind both."""
    economic = report.get("economic")
    if not economic:
        return []
    projection = economic.get("monthly_projection")
    money = projection["cost_usd"] if projection else economic["cost_per_request_usd"]
    stats = [{"label": f"Cost per month at {projection['requests']:,} requests" if projection else "Cost per request",
              "value": format_usd(money["candidate"]), "was": f"was {format_usd(money['baseline'])}",
              "change": None if money["delta_percent"] is None else percent(money["delta_percent"]),
              "tone": _tone(money["delta_percent"], "up")}]
    scores, cases = _quality_scores(report), report.get("baseline_case_count")
    if scores:
        before, after = (round(score * cases) for score in scores)
        stats.append({"label": "Correct answers", "value": f"{after} / {cases}", "was": f"was {before} / {cases}",
                      "change": None, "tone": _tone(after - before, "down")})
    output = next((row for row in (breakdown_ or {}).get("rows", []) if row["kind"] == "output"), None)
    if output:
        stats.append({"label": "Output per case", "value": f"{output['tokens_after']} tokens",
                      "was": f"was {output['tokens_before']}", "change": None, "tone": "flat"})
    return stats
