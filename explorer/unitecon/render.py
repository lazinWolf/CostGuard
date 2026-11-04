"""Markdown for people. Findings lead, because the point is what to do."""
from decimal import Decimal

D = Decimal


def usd(x) -> str:
    x = D(x)
    sign, x = ("-" if x < 0 else ""), abs(x)
    if x >= 100:
        return f"{sign}${x:,.0f}"
    if x >= 1:
        return f"{sign}${x:,.2f}"
    return f"{sign}${x:.4f}" if x else "$0"


def share(x) -> str:
    return f"{D(x):.0%}" if x is not None else "n/a"


def table(head, rows, align=None):
    align = align or ["---"] + ["---:"] * (len(head) - 1)
    return "\n".join(["| " + " | ".join(head) + " |", "| " + " | ".join(align) + " |"]
                     + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]) + "\n"


def render(r: dict) -> str:
    if r.get("empty"):
        return "Nothing to report: " + "; ".join(r["warnings"])
    month, out = r["month_factor"], []
    add = out.append
    u = r["users"]
    add(f"# AI unit economics\n\n{r['calls']:,} calls, {r['sessions']:,} sessions, {u['active']:,} active of {u['registered']:,} "
        f"registered users, {r['days']:.0f} days. Costs are per 30 days unless marked.\n")
    for note in r.get("notes", []):
        add(f"> Note: {note}\n")
    for w in r["warnings"]:
        add(f"> Warning: {w}\n")
    if r.get("reconciliation"):
        rc = r["reconciliation"]
        add(f"Checked against the cost the source tool reported on {rc['calls']:,} calls: ours {usd(rc['ours'])}, theirs {usd(rc['theirs'])}"
            + (f" ({rc['difference']:+.1%})." if rc["difference"] is not None else ".") + "\n")
    plans = r["plans"]
    revenue, margin = sum(p["revenue"] for p in plans.values()), sum(p["margin"] for p in plans.values())
    add(f"**Spend {usd(r['monthly_cost'])} a month** ({usd(r['monthly_cost'] / max(u['active'], 1))} per active user). "
        + (f"Subscription revenue {usd(revenue * month)}, margin after model cost {usd(margin * month)}.\n" if revenue else "No revenue configured.\n"))

    add("## What to do\n")
    add("Grouped by how well each is known, then by dollars a month. They overlap, so they do not add up.\n")
    groups = (("measured", "Fix now: read straight from the log"), ("estimated", "Likely: rests on a stated assumption"),
              ("unverified", "Worth testing: quality is not known"))
    n = 0
    for key, label in groups:
        rows = [f for f in r["findings"] if f["confidence"] == key]
        if rows:
            add(f"**{label}**\n")
            for f in rows:
                n += 1
                add(f"{n}. **{f['title']}**: {usd(f['monthly'])} a month. {f['detail']} {f['action']}")
            add("")
    if not r["findings"]:
        add("Nothing stands out.\n")

    add("## Who and what costs money\n")
    add(table(["Feature", "Cost", "Sessions", "Mean / session", "p95 / session", "Success", "Cost per success"],
              [(f"`{n}`", usd(f["cost"] * month), f"{f['sessions']:,}", usd(f["mean_session"]), usd(f["p95_session"]),
                share(f["success_rate"]) + ("" if f["outcome_coverage"] == 1 else f" ({share(f['outcome_coverage'])} reported)"),
                (usd(f["cost_per_success"]) if f["cost_per_success"] is not None else "n/a")
                + (f" ({usd(f['cost_per_success_without_stuck'])} without stuck sessions)" if f.get("cost_per_success_without_stuck") is not None else ""))
               for n, f in r["by_feature"].items()]))
    add(table(["Model", "Cost", "Calls"], [(f"`{m}`", usd(v["cost"] * month), f"{v['calls']:,}") for m, v in r["by_model"].items()]))
    add(f"Input tokens are {share(r['input_cost_share'])} of spend. Earlier conversation carried into later calls is about "
        f"{share(r['resent_history_cost'] / r['total_cost'])} of spend (estimated from consecutive calls in a session).\n")
    grow = [f"`{f}` {g['tenth'] / g['first']:.1f}x" for f, g in r["growth"].items() if g["tenth"] and g["first"]]
    if grow:
        add("A session's 10th call carries this many times the input of its 1st: " + ", ".join(grow) + ".\n")

    if u["anonymous"]:
        add("## Users\n\nThe log has no user ids, so per-user spend, plan margin and caps are not available. Add `user_id` to each call.\n")
        users_known = False
    else:
        users_known = True
        add(f"## Users\n\nA typical active user costs {usd(u['p50'] * month)} a month, the 90th percentile {usd(u['p90'] * month)}, "
            f"the 99th {usd(u['p99'] * month)}, the heaviest {usd(u['max'] * month)}. The top 1% of users are {share(u['top1_share'])} "
            f"of spend and the top 10% {share(u['top10_share'])}.\n")
        add(table(["User", "Plan", "Cost", "x median", "Mostly"],
                  [(f"`{t['user']}`", t["plan"], usd(t["cost"] * month), f"{t['x_median']:.0f}x" if t["x_median"] else "n/a", f"`{t['main_feature']}`")
                   for t in r["top_users"]], ["---", "---", "---:", "---:", "---"]))

        add("## Plans and margin\n")
        add(table(["Plan", "Price", "Registered", "Active", "Cost", "Revenue", "Margin", "Per registered user", "Underwater"],
                  [(p, usd(v["price"]) if not v["unknown_revenue"] else "?", f"{v['registered']:,}", f"{v['active']:,}", usd(v["cost"] * month),
                    usd(v["revenue"] * month), usd(v["margin"] * month), usd(v["mean_cost_registered"] * month),
                    "n/a" if v["underwater_users"] is None else f"{v['underwater_users']} users") for p, v in plans.items()]))
        b_ = r.get("breakeven")
        if b_:
            need = b_["needed"]
            add(f"A free user costs {usd(b_['free_cost'])} a month and a `{b_['paid']}` user {usd(b_['paid_cost'])}. "
                + (f"To cover the free tier, about {need[1]:.1%} of signups must pay (plausible range {need[0]:.1%} to {need[2]:.1%}); "
                   f"you are at {b_['observed_conversion']:.1%}.\n" if need else "The paid plan does not cover its own cost plus the free tier at this price.\n"))
        if r["caps"]:
            add("### If users were capped (assumes they stop at the cap)\n")
            add(table(["Plan", "Cap at", "Per-user cap", "Users hit", "Saved a month"],
                      [(p, f"p{c['percentile']}", usd(c["cap"] * month), c["users_hit"], usd(c["saving"] * month))
                       for p, rows in r["caps"].items() for c in rows]))
        if r.get("sensitivity"):
            add("### Margin per 1,000 signups if price or conversion changes\n")
            add("Holds each user's usage fixed and resamples the observed users. The range covers sampling uncertainty only. "
                "A higher price may change who signs up and how much they use.\n")
            add(table(["Paid share of signups", "Price", "Margin (median, 5th to 95th)"],
                      [(f"{g['conversion']:.1%}" + (" (observed)" if g["observed"] else ""), usd(g["price"]),
                        f"{usd(g['margin'][1])} ({usd(g['margin'][0])} to {usd(g['margin'][2])})") for g in r["sensitivity"]]))
    c = r["caching"]
    if c["by_prefix"]:
        add("## Prompt prefixes a provider could cache (estimated)\n")
        add(table(["Prefix", "Tokens", "Calls that could hit", "Saved a month"],
                  [(f"`{k[1]}`", f"{v['tokens']:,}", f"{v['calls']:,}", usd(v["saving"] * month)) for k, v in c["by_prefix"].items()]))
    if r["prompt_shifts"]:
        add("## Cost per session before and after a prompt version change (observed)\n")
        add(table(["Feature", "From", "To", "Per session before", "after", "Change"],
                  [(f"`{s['feature']}`", f"`{s['from']}`", f"`{s['to']}`", usd(s["from_mean"]), usd(s["to_mean"]), f"{s['change']:+.0%}") for s in r["prompt_shifts"]]))
    if r["stuck"]["sessions"]:
        add("## Stuck agent sessions (worst five)\n")
        add(table(["Session", "User", "Identical calls in a row", "Session cost", "Avoidable"],
                  [(f"`{s['session']}`", f"`{s['user']}`", s["run"], usd(s["session_cost"]), usd(s["avoidable"])) for s in r["stuck"]["sessions"]]))
    return "\n".join(out)
