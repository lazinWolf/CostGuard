import unittest

from decimal import Decimal

from costguard.diagnosis import (case_rows, changed_cases, describe_limits, format_usd, judge_spend, limit_rows,
                                 mean_output_tokens, pick_winner, prompt_change)
from costguard.execution import Judgement
from costguard.economics import compare_artifacts
from costguard.models import ComparisonPolicy
from tests.test_execution import artifact


def stored_report(policy: dict, candidate_tokens=3000, **candidate_options):
    """A comparison the way it is saved: plain JSON of the report."""
    left, right = artifact("baseline"), artifact("candidate", candidate_tokens, **candidate_options)
    report = compare_artifacts(left, right, left.pricing, ComparisonPolicy.model_validate(policy), 1000)
    return report.model_dump(mode="json")


class LimitTests(unittest.TestCase):
    def test_limits_are_described_in_plain_words(self):
        self.assertEqual(describe_limits({"max_cost_increase_percent": "20", "max_quality_drop": "0",
                                          "min_candidate_quality_score": "0.85"}),
                         ["cost per request may rise by at most 20%",
                          "no fewer correct answers than the baseline", "at least 85% of answers correct"])
        self.assertEqual(describe_limits({"max_quality_drop": "0.1"}),
                         ["correct answers may fall at most 10 percentage points below the baseline"])
        self.assertEqual(describe_limits(None), [])

    def test_each_limit_is_shown_beside_what_was_measured(self):
        policy = {"max_cost_increase_percent": "20", "max_quality_drop": "0", "max_monthly_cost_usd": "1000"}
        rows = {row["key"]: row for row in limit_rows(stored_report(policy), policy)}
        self.assertEqual(rows["max_cost_increase_percent"]["status"], "fail")
        self.assertEqual(rows["max_cost_increase_percent"]["measured"], "+200.00%")
        self.assertEqual(rows["max_quality_drop"]["status"], "pass")
        self.assertEqual(rows["max_quality_drop"]["measured"], "1/1 correct → 1/1 correct")
        self.assertEqual(rows["max_monthly_cost_usd"]["status"], "pass")
        self.assertEqual(rows["max_monthly_cost_usd"]["limit"], "projected monthly cost at most $1000")

    def test_failed_rows_match_the_violations_the_engine_reports(self):
        policies = [
            {"max_cost_increase_percent": "20"}, {"max_cost_increase_percent": "500"},
            {"max_candidate_cost_per_request_usd": "0.0001"}, {"max_monthly_cost_usd": "0.001"},
            {"min_candidate_quality_score": "1", "max_quality_drop": "0"},
            {"max_candidate_mean_latency_ms": "50", "max_candidate_retries_per_case": "0"},
            {"max_candidate_model_calls_per_case": "1", "max_candidate_tool_steps_per_case": "0"},
        ]
        for policy in policies:
            report = stored_report(policy)
            failed = [row for row in limit_rows(report, policy) if row["status"] == "fail"]
            self.assertEqual(len(failed), len(report["policy"]["violations"]), policy)

    def test_a_quality_limit_is_unknown_without_paired_scores(self):
        policy = {"max_quality_drop": "0", "min_candidate_quality_score": "1"}
        report = stored_report(policy)
        report["economic"]["quality_score"] = None
        self.assertEqual({row["status"] for row in limit_rows(report, policy)}, {"unknown"})
        self.assertEqual(limit_rows(report, policy)[0]["measured"], "not measured")

    def test_limits_that_were_not_set_are_not_listed(self):
        policy = {"max_cost_increase_percent": None, "max_quality_drop": None}
        self.assertEqual(limit_rows(stored_report({"max_cost_increase_percent": "20"}), policy), [])
        self.assertEqual(limit_rows(stored_report({"max_cost_increase_percent": "20"}), None), [])


class FormattingTests(unittest.TestCase):
    def test_money_is_cents_above_a_dollar_and_exact_below(self):
        self.assertEqual(format_usd("1234.5"), "$1,234.50")
        self.assertEqual(format_usd("0.00009898"), "$0.00009898")
        self.assertEqual(format_usd("0.0000100"), "$0.00001")
        self.assertEqual(format_usd(None), "Unknown")

    def test_mean_output_tokens_needs_usage_for_every_case(self):
        self.assertEqual(mean_output_tokens(artifact("one").cases), 0)
        self.assertIsNone(mean_output_tokens(artifact("unknown", source="missing").cases))


class FixSearchTests(unittest.TestCase):
    def rows(self):
        """Three cases: one lost its answer, one just got dearer, one is unchanged."""
        left, right = artifact("baseline"), artifact("candidate", 2000)
        template = left.cases[0]
        for side, scores, costs in ((left, ("1", "1", "1"), ("100", "100", "100")),
                                    (right, ("0", "1", "1"), ("120", "400", "100"))):
            side.cases = [template.model_copy(update={"case_id": name, "quality_score": score, "output": f"{name} says {score}"})
                          for name, score in zip("abc", scores)]
        costs = [{"case_id": name, "cost_usd": {"baseline": "1", "candidate": "2", "delta": delta}}
                 for name, delta in zip("abc", ("1", "5", "0"))]
        report = {"economic": {"case_costs": costs}}
        return case_rows(report, left, right)

    def test_cases_that_lost_correctness_come_first_then_the_dearest(self):
        shown = changed_cases(self.rows(), limit=2)
        self.assertEqual([(case["case_id"], case["lost_correctness"]) for case in shown], [("a", True), ("b", False)])
        self.assertEqual(shown[0]["baseline"]["output"], "a says 1")
        self.assertEqual(shown[0]["candidate"]["output"], "a says 0")

    def test_prompt_change_counts_words_added_and_removed(self):
        self.assertEqual(prompt_change("Be brief and exact.", "Be brief, polite and exact. Thank them."),
                         {"baseline_words": 4, "candidate_words": 7, "words_added": 4, "words_removed": 1})
        self.assertEqual(prompt_change("same words", "same words")["words_added"], 0)

    def test_the_winner_is_the_cheapest_fix_that_passes(self):
        fix = lambda name, status, cost, words: {"name": name, "status": status, "cost": cost, "words": words}
        self.assertEqual(pick_winner([fix("a", "pass", "0.0002", 10), fix("b", "pass", "0.0001", 40),
                                      fix("c", "fail", "0.00001", 5)]), "b")
        self.assertEqual(pick_winner([fix("a", "pass", "0.0001", 40), fix("b", "pass", "0.0001", 12)]), "b")
        self.assertIsNone(pick_winner([fix("a", "fail", "0.0001", 10), fix("b", None, None, 10)]))
        self.assertIsNone(pick_winner([]))


class JudgeSpendTests(unittest.TestCase):
    def test_judge_cost_is_summed_across_runs_and_flags_unknown_usage(self):
        left, right = artifact("baseline"), artifact("candidate")
        self.assertIsNone(judge_spend(left, right))
        left.cases[0].judgement = Judgement(passed=True, reason="", model="test/judge", cost_usd=Decimal("0.002"))
        right.cases[0].judgement = Judgement(passed=False, reason="", model="test/judge", cost_usd=Decimal("0.003"))
        self.assertEqual(judge_spend(left, right), {"calls": 2, "model": "test/judge", "cost": Decimal("0.005"), "complete": True})
        right.cases[0].judgement = Judgement(passed=None, reason="", model="test/judge")
        self.assertFalse(judge_spend(left, right)["complete"])


if __name__ == "__main__":
    unittest.main()
