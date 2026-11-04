"""Hand-computed checks for the unit economics engine. Run: python -m unittest discover -s explorer/tests"""
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unitecon.analyze import analyze, pct
from unitecon.load import load

D = Decimal
DAY = 86400
# $1 per million input tokens, nothing for output or cached, so a call's cost is its input tokens / 1e6.
PRICES = {"version": "test", "cache_ttl_seconds": 300, "min_cacheable_tokens": 1024,
          "models": {"m": {"in": "1.00", "cached_in": "0.50", "out": "0"}, "big": {"in": "10.00", "out": "0"}}}


def run(events, plans=None, prices=PRICES):
    with tempfile.TemporaryDirectory() as d:
        log, table = Path(d, "log.jsonl"), Path(d, "prices.json")
        log.write_text("".join(json.dumps(e) + "\n" for e in events))
        table.write_text(json.dumps(prices))
        data = load(str(log), str(table))
        return data, analyze(data, plans or {"free": D(0), "pro": D(20)})


def call(ts, user="u1", session="s1", tokens=1000, model="m", feature="f", out=0, **extra):
    return {"type": "call", "ts": ts, "user_id": user, "session_id": session, "feature": feature, "model": model,
            "input_tokens": tokens, "output_tokens": out, **extra}


class Cost(unittest.TestCase):
    def test_cached_tokens_are_priced_at_the_cached_rate(self):
        data, _ = run([call(0, tokens=1000, cached_input_tokens=400), call(DAY, tokens=0)])
        self.assertEqual(data.calls[0].cost, D("0.0008"))          # 600 * 1.00 + 400 * 0.50, per million

    def test_unknown_model_is_excluded_and_reported(self):
        data, r = run([call(0), call(DAY, model="mystery")])
        self.assertEqual(r["total_cost"], D("0.001"))
        self.assertTrue(any("mystery" in w for w in data.warnings))

    def test_percentile_is_nearest_rank(self):
        self.assertEqual(pct([D(i) for i in range(1, 11)], 90), D(9))
        self.assertEqual(pct([D(i) for i in range(1, 11)], 50), D(5))


class Stuck(unittest.TestCase):
    def test_only_calls_after_the_third_identical_one_are_waste(self):
        events = [call(i * 5, session="s1", tokens=1000, tool_sig="a") for i in range(6)] + \
                 [call(DAY, session="s2", tokens=1000, tool_sig="b")]
        _, r = run(events)
        self.assertEqual(r["stuck"]["count"], 1)
        self.assertEqual(r["stuck"]["avoidable"], D("0.003"))      # calls 4, 5, 6 of 6

    def test_a_changing_signature_is_not_a_loop(self):
        _, r = run([call(i * 5, tool_sig=f"x{i}") for i in range(8)] + [call(DAY)])
        self.assertEqual(r["stuck"]["count"], 0)


class Caching(unittest.TestCase):
    def test_only_calls_within_the_ttl_could_hit(self):
        shared = {"prefix_hash": "p", "prefix_tokens": 2000}
        _, r = run([call(0, session="a", **shared), call(100, session="b", **shared), call(1000, session="c", **shared)])
        self.assertEqual(r["caching"]["eligible_calls"], 1)         # second call; the third is 900 s after the second
        self.assertEqual(r["caching"]["saving"], D("0.001"))        # 2000 * (1.00 - 0.50) per million

    def test_short_prefixes_cannot_be_cached(self):
        _, r = run([call(0, prefix_hash="p", prefix_tokens=500), call(10, prefix_hash="p", prefix_tokens=500), call(DAY)])
        self.assertEqual(r["caching"]["eligible_calls"], 0)


class Sessions(unittest.TestCase):
    def test_sessions_are_rebuilt_from_gaps(self):
        events = [call(0, session=None), call(100, session=None), call(1000, session=None), call(DAY, session=None)]
        data, r = run(events)
        self.assertEqual(len({c.session for c in data.calls}), 3)
        self.assertTrue(any("rebuilt" in w for w in data.warnings))

    def test_no_user_and_no_session_means_every_call_stands_alone(self):
        events = [{"type": "call", "ts": i * 5, "model": "m", "input_tokens": 100, "output_tokens": 0} for i in range(5)]
        data, r = run(events)
        self.assertEqual(r["sessions"], 5)
        self.assertTrue(r["users"]["anonymous"])
        self.assertEqual(r["stuck"]["count"], 0)


class Margin(unittest.TestCase):
    def setUp(self):
        events = []
        for i in range(10):                      # free users: $0.40 each, a month apart so the window is 30 days
            events += [{"type": "user", "user_id": f"f{i}", "plan": "free"},
                       call(0, user=f"f{i}", session=f"f{i}a", tokens=200_000), call(30 * DAY, user=f"f{i}", session=f"f{i}b", tokens=200_000)]
        for i in range(10):                      # pro users: $5 each
            events += [{"type": "user", "user_id": f"p{i}", "plan": "pro"},
                       call(0, user=f"p{i}", session=f"p{i}a", tokens=2_500_000), call(30 * DAY, user=f"p{i}", session=f"p{i}b", tokens=2_500_000)]
        _, self.r = run(events)

    def test_plan_margin(self):
        pro = self.r["plans"]["pro"]
        self.assertEqual(pro["cost"], D(50))
        self.assertEqual(pro["revenue"], D(200))
        self.assertEqual(pro["margin"], D(150))
        self.assertEqual(self.r["plans"]["free"]["margin"], D(-4))

    def test_breakeven_conversion(self):
        # c = F / (P - C + F) = 0.4 / (20 - 5 + 0.4); every user is identical so the range collapses to a point.
        low, mid, high = self.r["breakeven"]["needed"]
        self.assertAlmostEqual(float(mid), 0.4 / 15.4, places=9)
        self.assertEqual(low, high)

    def test_sensitivity_margin_per_thousand_signups(self):
        cell = next(g for g in self.r["sensitivity"] if g["conversion"] == D("0.10") and g["price"] == D(20))
        self.assertAlmostEqual(float(cell["margin"][1]), 1000 * (0.1 * 20 - 0.9 * 0.4 - 0.1 * 5), places=6)


class Shifts(unittest.TestCase):
    def events(self, feature):
        out = []
        for i in range(25):
            out.append(call(i * 1000, session=f"a{i}", tokens=1000, feature=feature, prefix_hash="v1", prefix_tokens=100))
        for i in range(25):
            out.append(call(40_000 + i * 1000, session=f"b{i}", tokens=1500, feature=feature, prefix_hash="v2", prefix_tokens=100))
        return out

    def test_a_prompt_change_is_flagged_with_its_effect(self):
        _, r = run(self.events("f"))
        shift = r["prompt_shifts"][0]
        self.assertEqual((shift["from"], shift["to"]), ("v1", "v2"))
        self.assertEqual(shift["change"], D("0.5"))

    def test_without_a_feature_no_shift_is_claimed(self):
        events = [{k: v for k, v in e.items() if k != "feature"} for e in self.events("f")]
        _, r = run(events)
        self.assertEqual(r["prompt_shifts"], [])


if __name__ == "__main__":
    unittest.main()
