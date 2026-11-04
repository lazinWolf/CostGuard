"""Importers: what they read, what they refuse, and that they never guess."""
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unitecon import adapters
from unitecon.analyze import analyze
from unitecon.load import load, resolve_model

D = Decimal
PRICES = {"version": "t", "cache_ttl_seconds": 300, "min_cacheable_tokens": 1024,
          "models": {"gpt-4o": {"in": "2.50", "cached_in": "1.25", "out": "10"}, "gpt-4o-mini": {"in": "0.15", "out": "0.60"}}}


def convert(records, suffix=".json", raw=None, fmt=None):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d, "export" + suffix)
        path.write_text(raw if raw is not None else json.dumps(records))
        return adapters.convert(str(path), fmt)


SYSTEM = {"role": "system", "content": "You are a helpful bot. " * 100}


class Langfuse(unittest.TestCase):
    def obs(self, **kw):
        base = {"id": "o1", "traceId": "t1", "type": "GENERATION", "model": "gpt-4o-2024-08-06",
                "startTime": "2026-10-01T00:00:00Z", "endTime": "2026-10-01T00:00:02Z",
                "usageDetails": {"input": 1000, "output": 200}, "costDetails": {"total": 0.0045},
                "input": [SYSTEM, {"role": "user", "content": "hi"}], "output": {"role": "assistant", "content": "ok"}}
        return {**base, **kw}

    def test_reads_usage_time_cost_and_derives_prefix(self):
        trace = {"id": "t1", "name": "support", "userId": "u9", "sessionId": "s7", "observations": ["o1"]}
        events, fmt, _ = convert([trace, self.obs()])             # traces listed before observations
        e = events[0]
        self.assertEqual(fmt, "langfuse")
        self.assertEqual((e["input_tokens"], e["output_tokens"], e["latency_ms"]), (1000, 200, 2000.0))
        self.assertEqual((e["user_id"], e["session_id"], e["feature"]), ("u9", "s7", "support"))
        self.assertEqual(e["reported_cost_usd"], 0.0045)
        self.assertEqual(e["prefix_tokens"], len(SYSTEM["content"]) // 4)

    def test_same_system_prompt_gives_the_same_prefix_hash(self):
        events, _, _ = convert([self.obs(id="a"), self.obs(id="b", input=[SYSTEM, {"role": "user", "content": "other"}])])
        self.assertEqual(events[0]["prefix_hash"], events[1]["prefix_hash"])

    def test_tool_call_signature_and_error_level(self):
        call = {"role": "assistant", "tool_calls": [{"function": {"name": "search", "arguments": "{\"q\": 1}"}}]}
        events, _, _ = convert([self.obs(output=call, level="ERROR")])
        self.assertTrue(events[0]["tool_sig"].startswith("search:"))
        self.assertEqual(events[0]["status"], "error")

    def test_generations_without_usage_are_skipped_and_counted(self):
        events, _, notes = convert([self.obs(), self.obs(id="o2", usageDetails=None)])
        self.assertEqual(len(events), 1)
        self.assertTrue(any("skipped" in n for n in notes))

    def test_other_observation_types_are_ignored(self):
        events, _, _ = convert([self.obs(), self.obs(id="o2", type="SPAN")])
        self.assertEqual(len(events), 1)


class Helicone(unittest.TestCase):
    CSV = ("request_id,request_model,request_user_id,request_created_at,prompt_tokens,completion_tokens,delay_ms,response_status,"
           "cost_usd,request_properties\n"
           'r1,gpt-4o,u1,2026-10-01T00:00:00Z,500,50,800,200,0.00175,"{""feature"": ""chat"", ""Helicone-Session-Id"": ""s1""}"\n'
           'r2,gpt-4o,u1,2026-10-01T00:00:10Z,500,0,30000,504,0,"{}"\n')

    def test_csv_with_nested_json_columns(self):
        events, fmt, _ = convert(None, ".csv", self.CSV)
        self.assertEqual(fmt, "helicone")
        self.assertEqual((events[0]["feature"], events[0]["session_id"], events[0]["user_id"]), ("chat", "s1", "u1"))
        self.assertEqual(events[0]["status"], "ok")
        self.assertEqual(events[1]["status"], "error")             # HTTP 504
        self.assertEqual(events[0]["latency_ms"], 800.0)

    def test_jsonl_is_accepted_too(self):
        raw = "\n".join(json.dumps({"request_id": f"r{i}", "model": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 1,
                                    "created_at": "2026-10-01T00:00:00Z"}) for i in range(3))
        events, fmt, _ = convert(None, ".jsonl", raw)
        self.assertEqual((fmt, len(events)), ("helicone", 3))


class OpenTelemetry(unittest.TestCase):
    def span(self, **attrs):
        return {"name": "chat", "spanId": "x1", "startTimeUnixNano": 1_790_812_800_000_000_000, "endTimeUnixNano": 1_790_812_801_500_000_000,
                "status": {"code": "OK"}, "attributes": {"gen_ai.request.model": "gpt-4o", "gen_ai.usage.input_tokens": 100,
                                                          "gen_ai.usage.output_tokens": 20, "gen_ai.conversation.id": "c1", **attrs}}

    def test_nanosecond_times_cache_tokens_and_conversation(self):
        events, fmt, _ = convert([self.span(**{"gen_ai.usage.cache_read.input_tokens": 60, "enduser.id": "u5"})])
        e = events[0]
        self.assertEqual(fmt, "otel")
        self.assertEqual((e["ts"], e["latency_ms"], e["cached_input_tokens"]), (1_790_812_800.0, 1500.0, 60))
        self.assertEqual((e["session_id"], e["user_id"]), ("c1", "u5"))

    def test_error_spans_and_non_model_spans(self):
        bad = self.span()
        bad["status"] = {"code": "ERROR"}
        events, _, _ = convert([bad, {"name": "db", "attributes": {"db.system": "pg"}}, self.span()])
        self.assertEqual([e["status"] for e in events], ["error", "ok"])

    def test_spans_with_no_token_counts_yield_nothing(self):
        with self.assertRaises(adapters.FormatError):
            convert([{"name": "x", "attributes": {"gen_ai.request.model": "gpt-4o"}}])


class Refusals(unittest.TestCase):
    def test_unrecognised_file_names_the_fields_it_saw(self):
        with self.assertRaises(adapters.FormatError) as ctx:
            convert([{"foo": 1, "bar": 2}])
        self.assertIn("bar", str(ctx.exception))

    def test_empty_file(self):
        with self.assertRaises(adapters.FormatError):
            convert(None, ".json", "")

    def test_native_logs_are_recognised(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "n.jsonl")
            path.write_text(json.dumps({"type": "call", "ts": 1, "model": "m", "input_tokens": 1, "output_tokens": 1}) + "\n")
            self.assertTrue(adapters.is_native(str(path)))
            path.write_text(json.dumps({"request_id": "r"}) + "\n")
            self.assertFalse(adapters.is_native(str(path)))


class ModelNames(unittest.TestCase):
    def test_dated_and_prefixed_names_match_but_variants_do_not(self):
        m = PRICES["models"]
        self.assertEqual(resolve_model("gpt-4o-2024-08-06", m), "gpt-4o")
        self.assertEqual(resolve_model("openai/gpt-4o-mini-2024-07-18", m), "gpt-4o-mini")
        self.assertIsNone(resolve_model("gpt-4o-audio-preview", m))      # never priced as a different model
        self.assertIsNone(resolve_model("gpt-4o-mini-tts", m))


class Reconciliation(unittest.TestCase):
    def run_with_reported(self, reported):
        events, _, _ = convert([{"id": "o1", "traceId": "t", "type": "GENERATION", "model": "gpt-4o", "startTime": "2026-10-01T00:00:00Z",
                                 "usageDetails": {"input": 1_000_000, "output": 0}, "costDetails": {"total": reported}},
                                {"id": "o2", "traceId": "t", "type": "GENERATION", "model": "gpt-4o", "startTime": "2026-10-02T00:00:00Z",
                                 "usageDetails": {"input": 0, "output": 0}, "costDetails": {"total": 0}}])
        with tempfile.TemporaryDirectory() as d:
            log, table = Path(d, "l.jsonl"), Path(d, "p.json")
            log.write_text("".join(json.dumps(e) + "\n" for e in events))
            table.write_text(json.dumps(PRICES))
            return analyze(load(str(log), str(table)), {})

    def test_agreement_is_reported_without_a_warning(self):
        r = self.run_with_reported(2.5)                             # 1M input tokens at $2.50
        self.assertEqual(r["reconciliation"]["difference"], D(0))
        self.assertFalse(any("differs" in w for w in r["warnings"]))

    def test_a_large_gap_is_flagged(self):
        r = self.run_with_reported(1.0)
        self.assertTrue(any("differs" in w for w in r["warnings"]))


if __name__ == "__main__":
    unittest.main()
