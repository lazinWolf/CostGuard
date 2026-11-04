"""Write the simulated log in the shape of other tools' exports, to test the importers.

The shapes follow each tool's public docs as best I could read them; they are NOT real exports.
Messages are included so the importer can derive the prompt prefix and tool signature itself.

    python export_as.py [out/calls.jsonl] [out]
"""
import csv, json, sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

here = Path(__file__).resolve().parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
limit = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--max-calls=")), None)
src = Path(args[0]) if args else here / "out" / "calls.jsonl"
out = Path(args[1]) if len(args) > 1 else here / "out"
prices = json.loads((here.parent / "unitecon" / "prices.json").read_text())["models"]
PLAN = {}
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
# Real providers return dated model ids; use them so the importer's name matching is exercised.
DATED = {"gpt-4o": "gpt-4o-2024-08-06", "gpt-4o-mini": "gpt-4o-mini-2024-07-18"}


def cost(e):
    p = prices[e["model"]]
    return (e["input_tokens"] * Decimal(p["in"]) + e["output_tokens"] * Decimal(p["out"])) / 1_000_000


def messages(e):
    prefix = (e.get("prefix_hash") or "none") + " "
    return [{"role": "system", "content": (prefix * (e.get("prefix_tokens", 0) * 4 // len(prefix) + 1))[: e.get("prefix_tokens", 0) * 4]},
            {"role": "user", "content": "..."}]


def reply(e):
    if e.get("tool_sig"):
        return {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "search", "arguments": e["tool_sig"]}}]}
    return {"role": "assistant", "content": "..."}


calls, others = [], []
for line in src.read_text().splitlines():
    e = json.loads(line)
    (calls if e["type"] == "call" else others).append(e)
(out / "users.csv").write_text("user_id,plan\n" + "".join(f"{e['user_id']},{e['plan']}\n" for e in others if e["type"] == "user"))
if limit:                                        # export files embed full prompts, so keep tests small
    loops = set(json.loads((src.parent / "truth.json").read_text())["loop_sessions"]) if (src.parent / "truth.json").exists() else set()
    keep = calls[:limit]
    seen = {id(c) for c in keep}
    calls = sorted(keep + [c for c in calls if c["session_id"] in loops and id(c) not in seen], key=lambda c: c["ts"])
    (out / "subset.jsonl").write_text("".join(json.dumps(e) + "\n" for e in others + calls))

# Langfuse: observations plus traces carrying user, session and name.
traces, obs = {}, []
for e in calls:
    traces.setdefault(e["session_id"], {"id": e["session_id"], "name": e["feature"], "userId": e["user_id"],
                                       "sessionId": e["session_id"], "observations": []})
    start = e["ts"]
    obs.append({"id": e["call_id"], "traceId": e["session_id"], "type": "GENERATION", "name": "llm", "model": DATED[e["model"]],
                "startTime": iso(start), "endTime": iso(start + e["latency_ms"] / 1000),
                "usageDetails": {"input": e["input_tokens"], "output": e["output_tokens"], "total": e["input_tokens"] + e["output_tokens"]},
                "costDetails": {"total": float(cost(e))}, "level": "ERROR" if e["status"] == "error" else "DEFAULT",
                "input": messages(e), "output": reply(e)})
(out / "langfuse.json").write_text(json.dumps({"data": list(traces.values()) + obs}))

# Helicone: a CSV with nested columns stored as JSON text, as spreadsheets do.
with open(out / "helicone.csv", "w", newline="") as f:
    w = csv.DictWriter(f, ["request_id", "request_model", "request_user_id", "request_created_at", "prompt_tokens",
                           "completion_tokens", "delay_ms", "response_status", "cost_usd", "request_properties", "request_body", "response_body"])
    w.writeheader()
    for e in calls:
        w.writerow({"request_id": e["call_id"], "request_model": DATED[e["model"]], "request_user_id": e["user_id"],
                    "request_created_at": iso(e["ts"]), "prompt_tokens": e["input_tokens"], "completion_tokens": e["output_tokens"],
                    "delay_ms": e["latency_ms"], "response_status": 504 if e["status"] == "error" else 200, "cost_usd": float(cost(e)),
                    "request_properties": json.dumps({"feature": e["feature"], "Helicone-Session-Id": e["session_id"]}),
                    "request_body": json.dumps({"messages": messages(e)}), "response_body": json.dumps({"choices": [{"message": reply(e)}]})})

# OpenTelemetry GenAI spans, as JSON lines. No cost and no prefix/tool signal: those are not in the conventions.
with open(out / "otel.jsonl", "w") as f:
    for e in calls:
        f.write(json.dumps({"name": e["feature"], "spanId": e["call_id"], "startTimeUnixNano": int(e["ts"] * 1e9),
                            "endTimeUnixNano": int((e["ts"] + e["latency_ms"] / 1000) * 1e9),
                            "status": {"code": "ERROR" if e["status"] == "error" else "OK"},
                            "attributes": {"gen_ai.request.model": DATED[e["model"]], "gen_ai.usage.input_tokens": e["input_tokens"],
                                           "gen_ai.usage.output_tokens": e["output_tokens"], "gen_ai.conversation.id": e["session_id"],
                                           "enduser.id": e["user_id"], "app.feature": e["feature"]}}) + "\n")
print("wrote langfuse.json, helicone.csv, otel.jsonl in", out)
