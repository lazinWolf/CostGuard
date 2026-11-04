"""Read a call log (JSONL) and a price table into typed records, collecting data-quality warnings."""
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

MILLION = Decimal(1_000_000)


@dataclass
class Call:
    ts: float
    session: str
    user: str
    feature: str
    model: str
    inp: int
    out: int
    cached: int
    latency_ms: float
    ok: bool
    attempt: int
    prefix_hash: str | None
    prefix_tokens: int
    tool_sig: str | None
    cost: Decimal | None = None          # None: model not in the price table
    reported: Decimal | None = None      # cost the source tool itself reported, for reconciliation
    priced_as: str | None = None         # the table entry used when the logged name differed


@dataclass
class Data:
    calls: list[Call]
    plans: dict[str, str]                # user -> plan (every registered user, active or not)
    outcomes: dict[str, bool]            # session -> success (only sessions that reported one)
    prices: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def days(self) -> Decimal:
        ts = [c.ts for c in self.calls]
        return max(Decimal(str((max(ts) - min(ts)) / 86400)), Decimal("0.01")) if ts else Decimal(0)


VERSION_SUFFIX = re.compile(r"^-?(\d{4}-\d{2}-\d{2}|\d{8}|latest)$")


def resolve_model(name: str, models: dict) -> str | None:
    """Exact name, else drop a `provider/` prefix and a trailing date or `latest` (gpt-4o-2024-08-06 -> gpt-4o).
    Anything else, such as gpt-4o-audio, stays unpriced rather than being priced as a different model."""
    if name in models:
        return name
    bare = name.split("/")[-1]
    if bare in models:
        return bare
    for known in sorted(models, key=len, reverse=True):
        if bare.startswith(known) and VERSION_SUFFIX.match(bare[len(known):]):
            return known
    return None


def price_of(call: Call, models: dict) -> Decimal | None:
    key = resolve_model(call.model, models)
    p = models.get(key) if key else None
    if p is None:
        return None
    call.priced_as = key if key != call.model else None
    cached = min(call.cached, call.inp)
    return ((call.inp - cached) * Decimal(p["in"]) + cached * Decimal(p.get("cached_in", p["in"]))
            + call.out * Decimal(p["out"])) / MILLION


SESSION_GAP = 600   # seconds of silence that ends a session when the log has no session ids


def sessionize(calls: list[Call]) -> None:
    """Give calls without a session_id one, by splitting each user's calls per feature on long gaps."""
    last, number = {}, {}
    for c in sorted((c for c in calls if not c.session), key=lambda c: c.ts):
        key = (c.user, c.feature)
        if key not in last or c.ts - last[key] > SESSION_GAP:
            number[key] = number.get(key, 0) + 1
        last[key] = c.ts
        c.session = f"{c.user}|{c.feature}|{number[key]}"


def load(log: str, prices: str) -> Data:
    table = json.loads(Path(prices).read_text())
    calls, plans, outcomes, bad = [], {}, {}, 0
    for line in Path(log).read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            kind = e.get("type", "call")
            if kind == "user":
                plans[e["user_id"]] = e.get("plan", "unknown")
            elif kind == "outcome":
                outcomes[e["session_id"]] = bool(e["success"])
            else:
                calls.append(Call(
                    ts=float(e["ts"]), session=str(e.get("session_id") or ""), user=str(e.get("user_id") or "unknown"),
                    feature=e.get("feature") or "unknown",
                    model=e["model"], inp=int(e["input_tokens"]), out=int(e["output_tokens"]),
                    cached=int(e.get("cached_input_tokens") or 0), latency_ms=float(e.get("latency_ms") or 0),
                    ok=e.get("status", "ok") == "ok", attempt=int(e.get("attempt") or 1),
                    prefix_hash=e.get("prefix_hash"), prefix_tokens=int(e.get("prefix_tokens") or 0),
                    tool_sig=e.get("tool_sig"),
                    reported=Decimal(str(e["reported_cost_usd"])) if e.get("reported_cost_usd") is not None else None))
        except (KeyError, ValueError, TypeError):
            bad += 1
    data = Data(calls, plans, outcomes, table)
    if any(not c.session for c in calls):
        if all(c.user == "unknown" for c in calls if not c.session):
            for i, c in enumerate(c for c in calls if not c.session):
                c.session = f"call-{i}"
            data.warnings.append("calls have no session_id or user_id, so every call is its own session; "
                                 "per-session, per-user and agent-loop figures are unavailable")
        else:
            sessionize(calls)
            data.warnings.append("calls have no session_id; sessions were rebuilt from gaps of more than "
                                 f"{SESSION_GAP // 60} minutes between a user's calls to one feature, so session figures are approximate")
    if bad:
        data.warnings.append(f"{bad} log lines could not be read and were skipped")
    for c in calls:
        c.cost = price_of(c, table["models"])
    unknown = sorted({c.model for c in calls if c.cost is None})
    renamed = sorted({f"{c.model} as {c.priced_as}" for c in calls if c.priced_as})
    if renamed:
        data.warnings.append("priced by matching the model name: " + ", ".join(renamed[:6]) + (" ..." if len(renamed) > 6 else ""))
    if unknown:
        n = sum(c.cost is None for c in calls)
        data.warnings.append(f"{n} calls use models with no price ({', '.join(unknown)}); they are left out of every cost")
    for user in {c.user for c in calls} - set(plans):
        plans[user] = "unknown"
    if "unknown" in plans.values():
        data.warnings.append("some users have no plan record; they are grouped as 'unknown' and have no revenue")
    if calls and sum(c.prefix_hash is None for c in calls) > len(calls) * 0.2:
        data.warnings.append("most calls have no prefix_hash, so the prompt-caching and prompt-version findings are unavailable")
    return data
