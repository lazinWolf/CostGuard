"""Read exports from other tools and turn them into unitecon's call log.

Supported: Langfuse observations (and traces), Helicone request exports (JSON, JSONL or CSV), and
OpenTelemetry GenAI spans as JSON. Field names come from each tool's public docs and are matched
with several aliases; they have NOT been checked against real exports, so a missing field is
reported, never guessed. What cannot be read from an export is left out and the report says so.
"""
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

FORMATS = ("langfuse", "helicone", "otel")


class FormatError(ValueError):
    pass


# -- small helpers -------------------------------------------------------------------------
def lookup(rec: dict, key: str):
    """Exact key, then a dotted path, then inside `attributes` (where OpenTelemetry keeps dotted names)."""
    if key in rec and rec[key] not in (None, ""):
        return rec[key]
    cur = rec
    for part in key.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            break
    if cur not in (None, ""):
        return cur
    attrs = rec.get("attributes")
    if isinstance(attrs, dict) and attrs.get(key) not in (None, ""):
        return attrs[key]
    return None


def first(rec: dict, *keys):
    for key in keys:
        value = lookup(rec, key)
        if value is not None:
            return value
    return None


def number(value):
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def to_epoch(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    if value > 1e17:
        return value / 1e9
    if value > 1e14:
        return value / 1e6
    if value > 1e11:
        return value / 1e3
    return float(value)


def as_object(value):
    """CSV and some exports store nested values as JSON text."""
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def from_messages(request, response) -> dict:
    """What a prompt and a reply reveal: the static prefix (system text) and a tool-call signature.
    Prefix length is estimated as characters / 4, which is why the report calls caching an estimate."""
    out = {}
    messages = request.get("messages") if isinstance(request, dict) else request
    if isinstance(messages, list):
        system = "\n".join(str(m.get("content") or "") for m in messages if isinstance(m, dict) and m.get("role") == "system")
        if system:
            out["prefix_hash"], out["prefix_tokens"] = digest(system), len(system) // 4
    reply = response
    if isinstance(reply, dict) and isinstance(reply.get("choices"), list) and reply["choices"]:
        reply = reply["choices"][0].get("message", reply)
    calls = reply.get("tool_calls") if isinstance(reply, dict) else None
    if calls:
        fn = (calls[0] or {}).get("function", {})
        out["tool_sig"] = f"{fn.get('name')}:{digest(str(fn.get('arguments')))}"
    return out


def event(rec_id, ts, user, session, feature, model, inp, out, cached, latency_ms, ok, reported, extra) -> dict:
    e = {"type": "call", "ts": ts, "call_id": str(rec_id), "model": model, "input_tokens": inp, "output_tokens": out,
         "cached_input_tokens": cached or 0, "latency_ms": latency_ms or 0, "status": "ok" if ok else "error"}
    for key, value in (("user_id", user), ("session_id", session), ("feature", feature), ("reported_cost_usd", reported)):
        if value not in (None, ""):
            e[key] = value
    return {**e, **extra}


# -- readers -------------------------------------------------------------------------------
def read_records(path: str) -> list[dict]:
    text = Path(path).read_text()
    stripped = text.lstrip()
    if stripped[:1] == "[":
        return json.loads(text)
    if stripped[:1] == "{":
        try:
            doc = json.loads(text)
            for key in ("data", "observations", "requests", "spans", "resourceSpans"):
                if isinstance(doc.get(key), list):
                    return doc[key]
            return [doc]
        except ValueError:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
    if stripped:
        return list(csv.DictReader(io.StringIO(text)))
    return []


def detect(records: list[dict]) -> str:
    step = max(1, len(records) // 200)          # sample the whole file: traces may be listed before observations
    sample = records[:25] + records[::step]
    keys = set().union(*(set(r) | set((r.get("attributes") or {}) if isinstance(r.get("attributes"), dict) else ()) for r in sample)) if sample else set()
    if any(k.startswith("gen_ai.") for k in keys):
        return "otel"
    if "observations" in keys and "sessionId" in keys:
        return "langfuse"
    if {"traceId", "trace_id"} & keys and ({"usageDetails", "usage", "calculatedTotalCost", "promptTokens"} & keys):
        return "langfuse"
    if {"request_id", "prompt_tokens", "completion_tokens"} & keys and "request_id" in keys:
        return "helicone"
    raise FormatError("could not recognise this file. Expected Langfuse observations, a Helicone request export, or "
                      "OpenTelemetry GenAI spans (attributes such as gen_ai.usage.input_tokens). "
                      f"Saw fields: {', '.join(sorted(keys)[:12]) or 'none'}")


def convert_langfuse(records: list[dict]) -> tuple[list[dict], list[str]]:
    traces = {r["id"]: r for r in records if "observations" in r or ("traceId" not in r and "sessionId" in r)}
    events, skipped = [], 0
    for r in records:
        if r.get("id") in traces and "traceId" not in r:
            continue
        if str(r.get("type", "GENERATION")).upper() != "GENERATION":
            continue
        usage = as_object(first(r, "usageDetails", "usage")) or {}
        inp = first(usage, "input", "promptTokens", "prompt_tokens") if isinstance(usage, dict) else None
        out = first(usage, "output", "completionTokens", "completion_tokens") if isinstance(usage, dict) else None
        ts = to_epoch(first(r, "startTime", "start_time", "timestamp"))
        if inp is None or out is None or ts is None or not r.get("model"):
            skipped += 1
            continue
        end = to_epoch(first(r, "endTime", "end_time"))
        trace = traces.get(r.get("traceId"), {})
        cost = first(as_object(r.get("costDetails")) or {}, "total") if isinstance(as_object(r.get("costDetails")), dict) else None
        events.append(event(
            r.get("id"), ts, first(r, "userId") or trace.get("userId"), first(r, "sessionId") or trace.get("sessionId") or r.get("traceId"),
            trace.get("name") or r.get("name"), r["model"], int(inp), int(out),
            int(first(usage, "cache_read_input_tokens", "cached") or 0) if isinstance(usage, dict) else 0,
            round((end - ts) * 1000, 1) if end else None, str(r.get("level", "DEFAULT")).upper() != "ERROR",
            number(cost if cost is not None else first(r, "calculatedTotalCost", "totalCost")),
            from_messages(as_object(r.get("input")), as_object(r.get("output")))))
    return events, ([f"{skipped} Langfuse generations lacked a model, token usage or start time and were skipped"] if skipped else [])


def convert_helicone(records: list[dict]) -> tuple[list[dict], list[str]]:
    events, skipped = [], 0
    for r in records:
        props = as_object(first(r, "request_properties", "properties", "custom_properties")) or {}
        props = props if isinstance(props, dict) else {}
        inp, out = number(first(r, "prompt_tokens", "request_prompt_tokens")), number(first(r, "completion_tokens", "response_completion_tokens"))
        ts, model = to_epoch(first(r, "request_created_at", "created_at", "createdAt")), first(r, "request_model", "model", "response_model")
        if inp is None or out is None or ts is None or not model:
            skipped += 1
            continue
        status = number(first(r, "response_status", "status"))
        feature = next((props[k] for k in props if k.lower().replace("helicone-property-", "") in ("feature", "route", "endpoint")), None)
        session = next((props[k] for k in props if "session" in k.lower()), None) or first(r, "session_id", "helicone_session_id")
        events.append(event(
            first(r, "request_id", "id"), ts, first(r, "request_user_id", "user_id"), session, feature, model, int(inp), int(out), 0,
            number(first(r, "delay_ms", "latency", "latency_ms")), status is None or 200 <= status < 300,
            number(first(r, "cost_usd", "cost")),
            from_messages(as_object(first(r, "request_body")), as_object(first(r, "response_body")))))
    return events, ([f"{skipped} Helicone rows lacked a model, token counts or a timestamp and were skipped"] if skipped else [])


def convert_otel(records: list[dict]) -> tuple[list[dict], list[str]]:
    events, skipped = [], 0
    for r in records:
        inp, out = number(lookup(r, "gen_ai.usage.input_tokens")), number(lookup(r, "gen_ai.usage.output_tokens"))
        if inp is None and out is None:
            continue                                            # not a model call span
        ts = to_epoch(first(r, "startTimeUnixNano", "start_time", "startTime"))
        model = first(r, "gen_ai.request.model", "gen_ai.response.model")
        if ts is None or not model or inp is None or out is None:
            skipped += 1
            continue
        end = to_epoch(first(r, "endTimeUnixNano", "end_time", "endTime"))
        status = str((r.get("status") or {}).get("code", "") if isinstance(r.get("status"), dict) else r.get("status", "")).upper()
        events.append(event(
            first(r, "spanId", "span_id"), ts, first(r, "enduser.id", "user.id", "app.user_id"),
            first(r, "gen_ai.conversation.id", "session.id", "app.session_id"), first(r, "app.feature", "feature") or r.get("name"),
            model, int(inp), int(out), int(number(lookup(r, "gen_ai.usage.cache_read.input_tokens")) or 0),
            round((end - ts) * 1000, 1) if end else None, "ERROR" not in status and lookup(r, "error.type") is None, None, {}))
    return events, ([f"{skipped} spans lacked a model, token counts or a start time and were skipped"] if skipped else [])


CONVERTERS = {"langfuse": convert_langfuse, "helicone": convert_helicone, "otel": convert_otel}


def convert(path: str, fmt: str | None = None) -> tuple[list[dict], str, list[str]]:
    records = read_records(path)
    if not records:
        raise FormatError("the file is empty")
    fmt = fmt if fmt and fmt != "auto" else detect(records)
    events, notes = CONVERTERS[fmt](records)
    if not events:
        raise FormatError(f"read the file as {fmt} but found no model calls with token usage. " + " ".join(notes))
    return events, fmt, notes


def is_native(path: str) -> bool:
    with open(path) as f:
        for line in f:
            if line.strip():
                try:
                    e = json.loads(line)
                except ValueError:
                    return False
                return isinstance(e, dict) and (e.get("type") in ("call", "user", "outcome") or {"input_tokens", "output_tokens", "ts"} <= set(e))
    return False
