"""Turn a call log into unit economics: who and what costs money, margin, waste, and a forecast.

Every figure is labelled with how it is known: measured (read straight off the log), estimated
(needs a stated assumption) or unverified (a what-if that needs a replay to confirm quality).
"""
import random
from collections import defaultdict
from decimal import Decimal
from math import ceil

from .load import MILLION, Data

D = Decimal
LOOP_RUN = 5          # identical consecutive tool calls before a session counts as stuck
LOOP_FREE_STEPS = 3   # repeats that were reasonable; calls after these are counted as waste
BOOTSTRAPS = 1000
CONTEXT_WINDOW = 4000   # tokens of earlier conversation kept per call in the what-if


def pct(sorted_values, p):
    """Nearest-rank percentile of an ascending list."""
    return sorted_values[max(0, ceil(len(sorted_values) * p / 100) - 1)] if sorted_values else D(0)


def mean(values):
    return sum(values, D(0)) / len(values) if values else D(0)


def interval(values):
    values = sorted(values)
    return pct(values, 5), pct(values, 50), pct(values, 95)


def group_sessions(calls):
    sessions = defaultdict(list)
    for c in sorted(calls, key=lambda c: c.ts):
        sessions[c.session].append(c)
    return sessions


def analyze(data: Data, plan_prices: dict[str, Decimal], seed: int = 1) -> dict:
    priced = [c for c in data.calls if c.cost is not None]
    if not priced:
        return {"empty": True, "warnings": data.warnings + ["no priced calls to analyze"]}
    models, days = data.prices["models"], data.days
    month = D(30) / days
    total = sum(c.cost for c in priced)
    sessions = group_sessions(priced)
    session_cost = {sid: sum(c.cost for c in cs) for sid, cs in sessions.items()}
    session_feature = {sid: cs[0].feature for sid, cs in sessions.items()}

    out = {"days": days, "month_factor": month, "calls": len(priced), "sessions": len(sessions),
           "total_cost": total, "monthly_cost": total * month, "warnings": list(data.warnings)}
    if days < 7:
        out["warnings"].append(f"only {days:.1f} days of data; monthly figures are scaled up and noisy")

    # -- Where the money goes ---------------------------------------------------------------
    by_model = defaultdict(lambda: {"cost": D(0), "calls": 0})
    for c in priced:
        by_model[c.model]["cost"] += c.cost
        by_model[c.model]["calls"] += 1
    out["by_model"] = dict(sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]))
    out["input_cost_share"] = sum(c.inp * D(models[c.model]["in"]) for c in priced) / MILLION / total

    features = {}
    for feature in sorted({c.feature for c in priced}):
        ids = [s for s, f in session_feature.items() if f == feature]
        costs = sorted(session_cost[s] for s in ids)
        reported = [s for s in ids if s in data.outcomes]
        wins = sum(data.outcomes[s] for s in reported)
        spent = sum(session_cost[s] for s in reported)
        features[feature] = {
            "cost": sum(costs), "sessions": len(ids), "mean_session": mean(costs), "p95_session": pct(costs, 95),
            "max_session": costs[-1], "outcome_coverage": D(len(reported)) / len(ids),
            "success_rate": D(wins) / len(reported) if reported else None,
            "cost_per_success": spent / wins if wins else None}
    out["by_feature"] = dict(sorted(features.items(), key=lambda kv: -kv[1]["cost"]))

    # -- Users, plans, margin ---------------------------------------------------------------
    user_cost = defaultdict(lambda: D(0))
    for c in priced:
        user_cost[c.user] += c.cost
    plan_of = data.plans
    active = sorted(user_cost.values())

    def top_share(fraction):
        return sum(active[len(active) - max(1, ceil(len(active) * fraction)):], D(0)) / total

    median = pct(active, 50)
    out["users"] = {"anonymous": set(user_cost) <= {"unknown", ""} or len(user_cost) <= 1, "registered": len(plan_of), "active": len(active), "mean": mean(active), "p50": median,
                    "p90": pct(active, 90), "p99": pct(active, 99), "max": active[-1],
                    "top1_share": top_share(0.01), "top10_share": top_share(0.10)}
    top = sorted(user_cost.items(), key=lambda kv: -kv[1])[:8]
    spend_by_user_feature = defaultdict(lambda: defaultdict(lambda: D(0)))
    for c in priced:
        spend_by_user_feature[c.user][c.feature] += c.cost
    out["top_users"] = [{"user": u, "plan": plan_of.get(u, "unknown"), "cost": cost,
                         "main_feature": max(spend_by_user_feature[u], key=spend_by_user_feature[u].get),
                         "x_median": cost / median if median else None} for u, cost in top]

    plans = {}
    for plan in sorted(set(plan_of.values())):
        members = [u for u, p in plan_of.items() if p == plan]
        costs = [user_cost.get(u, D(0)) for u in members]            # includes users who never used it
        revenue = plan_prices.get(plan, D(0)) * len(members) * days / 30
        paid = plan_prices.get(plan, D(0)) > 0
        quota = plan_prices.get(plan, D(0)) * days / 30
        plans[plan] = {"registered": len(members), "active": sum(c > 0 for c in costs), "cost": sum(costs),
                       "revenue": revenue, "margin": revenue - sum(costs), "price": plan_prices.get(plan, D(0)),
                       "mean_cost_registered": mean(costs),
                       "underwater_users": sum(c > quota for c in costs) if paid else None,
                       "unknown_revenue": plan not in plan_prices}
    out["plans"] = plans

    # -- Waste: measured --------------------------------------------------------------------
    stuck = []
    for sid, calls in sessions.items():
        run, prev, waste, longest = 0, None, D(0), 0
        for c in calls:
            if not c.ok or c.tool_sig is None:
                continue
            run = run + 1 if c.tool_sig == prev else 1
            prev, longest = c.tool_sig, max(longest, run)
            if run > LOOP_FREE_STEPS:
                waste += c.cost
        if longest >= LOOP_RUN:
            stuck.append({"session": sid, "user": calls[0].user, "feature": calls[0].feature, "run": longest,
                          "session_cost": session_cost[sid], "avoidable": waste})
    stuck.sort(key=lambda s: -s["avoidable"])
    avoidable_by_session = {x["session"]: x["avoidable"] for x in stuck}
    for feature, row in out["by_feature"].items():
        reported = [sid for sid, f in session_feature.items() if f == feature and sid in data.outcomes]
        wins = sum(data.outcomes[sid] for sid in reported)
        trimmed = sum(session_cost[sid] - avoidable_by_session.get(sid, D(0)) for sid in reported)
        row["cost_per_success_without_stuck"] = trimmed / wins if wins and any(sid in avoidable_by_session for sid in reported) else None
    out["stuck"] = {"ids": [x["session"] for x in stuck], "sessions": stuck[:5], "count": len(stuck), "avoidable": sum(s["avoidable"] for s in stuck),
                    "share_of_cost": sum(s["session_cost"] for s in stuck) / total}
    long_sessions = {}
    for feature in features:
        if any(c.tool_sig for c in priced if c.feature == feature):
            continue                                            # loops are measured directly above
        ids = [sid for sid, f in session_feature.items() if f == feature]
        lengths = sorted(len(sessions[sid]) for sid in ids)
        limit = max(10, 2 * pct(lengths, 95))
        long = [sid for sid in ids if len(sessions[sid]) > limit]
        if long and len(ids) >= 20:
            long_sessions[feature] = {"limit": limit, "ids": long, "cost": sum(session_cost[sid] for sid in long),
                                      "excess": sum(sum(c.cost for c in sessions[sid][int(limit):]) for sid in long)}
    out["long_sessions"] = long_sessions
    failed = [c for c in priced if not c.ok]
    out["failed_calls"] = {"count": len(failed), "cost": sum(c.cost for c in failed)}

    # -- Context growth and re-sent history: estimated ---------------------------------------
    growth, resent, capped, window = defaultdict(lambda: defaultdict(list)), D(0), defaultdict(lambda: D(0)), CONTEXT_WINDOW
    for calls in sessions.values():
        ok = [c for c in calls if c.ok]
        for i, c in enumerate(ok):
            growth[c.feature][min(i, 9)].append(c.inp)
            if i:
                history = max(0, min(c.inp, ok[i - 1].inp + ok[i - 1].out) - c.prefix_tokens)
                resent += history * D(models[c.model]["in"]) / MILLION
                capped[c.feature] += max(0, history - window) * D(models[c.model]["in"]) / MILLION
    out["resent_history_cost"] = resent
    out["context_cap"] = {"window": window, "saving": sum(capped.values()), "by_feature": dict(capped)}
    out["growth"] = {f: {"first": mean([D(v) for v in by[0]]), "tenth": mean([D(v) for v in by[9]]) if by.get(9) else None}
                     for f, by in growth.items()}

    # -- Prompt caching: estimated (provider rules in the price table) ----------------------
    ttl, min_tokens = data.prices.get("cache_ttl_seconds", 300), data.prices.get("min_cacheable_tokens", 1024)
    last_seen, cache_saving, eligible, with_hash = {}, D(0), 0, 0
    cache_by = defaultdict(lambda: {"calls": 0, "tokens": 0, "saving": D(0)})
    for c in sorted(priced, key=lambda c: c.ts):
        if not c.prefix_hash:
            continue
        with_hash += 1
        key = (c.model, c.prefix_hash)
        seen = last_seen.get(key)
        last_seen[key] = c.ts
        if c.prefix_tokens >= min_tokens and seen is not None and c.ts - seen <= ttl and c.cached < c.prefix_tokens:
            p = models[c.model]
            saving = (c.prefix_tokens - c.cached) * (D(p["in"]) - D(p.get("cached_in", p["in"]))) / MILLION
            cache_saving += saving
            eligible += 1
            row = cache_by[key]
            row["calls"] += 1
            row["tokens"] = c.prefix_tokens
            row["saving"] += saving
    out["caching"] = {"saving": cache_saving, "eligible_calls": eligible, "calls_with_hash": with_hash,
                      "by_prefix": dict(sorted(cache_by.items(), key=lambda kv: -kv[1]["saving"]))}

    # -- Prompt versions: observed, not causal -----------------------------------------------
    versions = defaultdict(list)
    for sid, calls in sessions.items():
        if calls[0].prefix_hash and calls[0].feature != "unknown":
            versions[(calls[0].feature, calls[0].prefix_hash)].append((calls[0].ts, session_cost[sid], calls[0].cost))
    by_feature_versions = defaultdict(list)
    for (feature, h), rows in versions.items():
        by_feature_versions[feature].append({"hash": h, "first": min(t for t, _, _ in rows), "sessions": len(rows),
                                             "mean_session": mean([c for _, c, _ in rows]),
                                             "mean_first_call": mean([c for _, _, c in rows])})
    shifts = []
    for feature, rows in by_feature_versions.items():
        rows.sort(key=lambda r: r["first"])
        for before, after in zip(rows, rows[1:]):
            if before["sessions"] >= 20 and after["sessions"] >= 20 and before["mean_session"]:
                delta = after["mean_session"] - before["mean_session"]
                first = (after["mean_first_call"] - before["mean_first_call"]) / before["mean_first_call"] if before["mean_first_call"] else None
                shifts.append({"feature": feature, "from": before["hash"], "to": after["hash"],
                               "from_mean": before["mean_session"], "to_mean": after["mean_session"],
                               "change": delta / before["mean_session"], "first_call_change": first,
                               "monthly": delta * after["sessions"] * month})
    out["prompt_shifts"] = sorted(shifts, key=lambda s: -s["monthly"])

    # -- Model what-ifs: unverified ----------------------------------------------------------
    cheaper = {}
    for feature in features:
        current = {m for m in {c.model for c in priced if c.feature == feature}}
        for alt, p in models.items():
            if alt in current:
                continue
            rows = [c for c in priced if c.feature == feature]
            now = sum(c.cost for c in rows)
            then = sum((c.inp * D(p["in"]) + c.out * D(p["out"])) / MILLION for c in rows)
            if then < now:
                cheaper.setdefault(feature, []).append({"model": alt, "saving": now - then, "of": now})
    out["model_whatifs"] = {f: sorted(v, key=lambda r: -r["saving"]) for f, v in cheaper.items()}

    # -- Caps: assumes users simply stop at the cap -----------------------------------------
    caps = {}
    for plan, row in plans.items():
        costs = sorted(c for u, c in user_cost.items() if plan_of.get(u) == plan)
        if len(costs) < 20:
            continue
        caps[plan] = [{"percentile": p, "cap": pct(costs, p), "saving": sum(max(D(0), c - pct(costs, p)) for c in costs),
                       "users_hit": sum(c > pct(costs, p) for c in costs)} for p in (90, 95, 99)]
    out["caps"] = caps

    # -- Pricing and conversion sensitivity: bootstrap over observed users --------------------
    # Scaling users up just multiplies the mean, so the useful question is what happens when the
    # business levers move. Behavior is held fixed (people do not change usage when the price does).
    rng = random.Random(seed)
    registered_costs = {plan: [user_cost.get(u, D(0)) * month for u, p in plan_of.items() if p == plan] for plan in plans}
    means = {plan: [mean(rng.choices(v, k=len(v))) for _ in range(BOOTSTRAPS)] for plan, v in registered_costs.items() if v}
    registered = len(plan_of)
    paid = [p for p in plans if plan_prices.get(p, D(0)) > 0 and p in means]
    free = [p for p in plans if plan_prices.get(p, D(0)) == 0 and p in means]
    if len(paid) == 1 and len(free) == 1 and not out["users"].get("anonymous"):
        pp, fp = paid[0], free[0]
        price, free_means, paid_means = plan_prices[pp], means[fp], means[pp]
        boots, never = [], 0
        for f, c in zip(free_means, paid_means):
            room = price - c + f
            if room > 0:
                boots.append(f / room)
            else:
                never += 1
        observed = D(plans[pp]["registered"]) / registered
        out["breakeven"] = {"paid": pp, "free": fp, "observed_conversion": observed,
                            "needed": interval(boots) if boots else None, "never_share": D(never) / BOOTSTRAPS,
                            "free_cost": mean(registered_costs[fp]), "paid_cost": mean(registered_costs[pp])}
        grid = []
        for conversion in (D("0.02"), D("0.05"), D("0.10"), observed):
            for p in sorted({D(10), D(20), D(30), price}):
                margin = [1000 * (conversion * p - (1 - conversion) * f - conversion * c) for f, c in zip(free_means, paid_means)]
                grid.append({"conversion": conversion, "price": p, "margin": interval(margin), "observed": conversion == observed and p == price})
        out["sensitivity"] = grid

    out["findings"] = findings(out, month, plan_prices)
    return out


def findings(r: dict, month: Decimal, plan_prices) -> list[dict]:
    """Ranked by dollars per month. The savings overlap, so they are not additive."""
    items = []
    s = r["stuck"]
    if s["count"]:
        items.append({"title": f"{s['count']} sessions were stuck repeating the same tool call",
                      "monthly": s["avoidable"] * month, "confidence": "measured",
                      "detail": f"They are {s['share_of_cost']:.0%} of all spend. Counting only calls after the third identical tool call.",
                      "action": f"Stop an agent after {LOOP_FREE_STEPS + 1} identical tool calls or a step budget."})
    for shift in r["prompt_shifts"][:2]:
        if shift["change"] > D("0.05") and shift["monthly"] > 0:
            items.append({"title": f"Sessions of `{shift['feature']}` got {shift['change']:.0%} more expensive after prompt `{shift['from']}` became `{shift['to']}`",
                          "monthly": shift["monthly"], "confidence": "measured",
                          "detail": (f"The first call of a session alone got {shift['first_call_change']:+.0%}, which points at the prompt itself. "
                                     if shift["first_call_change"] is not None else "") + "Observed over time, not proven causal.",
                          "action": "Diff the two prompts; a gate on the next change would have caught this."})
    c = r["caching"]
    if c["saving"] > 0:
        top = next(iter(c["by_prefix"].items()))
        items.append({"title": f"{c['eligible_calls']:,} calls re-sent a long identical prompt prefix a provider could have cached",
                      "monthly": c["saving"] * month, "confidence": "estimated",
                      "detail": f"Largest: `{top[0][1]}` ({top[1]['tokens']:,} tokens). Assumes the provider's cache rules in the price table.",
                      "action": "Put the static prompt first and turn on prompt caching."})
    for plan, rows in r["caps"].items():
        if plan_prices.get(plan, D(0)) == 0:
            row = rows[-1]
            items.append({"title": f"A cap at the 99th percentile on the `{plan}` plan would hit {row['users_hit']} users",
                          "monthly": row["saving"] * month, "confidence": "estimated",
                          "detail": f"The cap is ${row['cap']:.2f} per user per period. Assumes those users stop rather than leave.",
                          "action": "Set a per-user budget; see the table of caps for other levels."})
    for feature, row in r["long_sessions"].items():
        items.append({"title": f"{len(row['ids'])} `{feature}` sessions ran past {row['limit']:.0f} calls (an unusually long session)",
                      "monthly": row["excess"] * month, "confidence": "estimated",
                      "detail": "The log has no tool signatures, so a stuck loop cannot be told from heavy use. Counting only calls past that length.",
                      "action": "Log the tool name and arguments per call to separate loops from heavy use, and set a step budget."})
    if r["failed_calls"]["count"]:
        items.append({"title": f"{r['failed_calls']['count']:,} failed calls were billed for their input",
                      "monthly": r["failed_calls"]["cost"] * month, "confidence": "measured",
                      "detail": "Counts only calls the log marks as failed and records input tokens for.",
                      "action": "Lower timeouts that fire after the provider has started, or retry smaller."})
    cap = r["context_cap"]
    if cap["saving"] > 0:
        best = max(cap["by_feature"], key=cap["by_feature"].get)
        items.append({"title": f"Keeping only the last {cap['window']:,} tokens of earlier conversation per call would trim input cost",
                      "monthly": cap["saving"] * month, "confidence": "unverified",
                      "detail": f"Most of it in `{best}`. Answers may get worse when older context is dropped.",
                      "action": "Summarize or truncate history and replay real sessions to check quality."})
    for feature, rows in r["model_whatifs"].items():
        best = rows[0]
        items.append({"title": f"`{feature}` on `{best['model']}` would cost {best['saving'] / best['of']:.0%} less",
                      "monthly": best["saving"] * month, "confidence": "unverified",
                      "detail": "Same tokens, other price. Answer quality is not known.",
                      "action": "Replay real requests on it and compare answers before switching."})
    return sorted(items, key=lambda i: -i["monthly"])
