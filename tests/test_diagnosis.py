import unittest

from costguard.diagnosis import describe_limits, format_usd, limit_rows, mean_output_tokens
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


if __name__ == "__main__":
    unittest.main()
