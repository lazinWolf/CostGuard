import unittest
from decimal import Decimal

from pydantic import ValidationError

from costguard import ComparisonRequest, compare_workloads


def request_data() -> dict:
    return {
        "baseline": {
            "name": "main",
            "runs": [
                {
                    "case_id": "support-question",
                    "calls": [
                        {
                            "provider": "example-ai",
                            "model": "small",
                            "input_tokens": 1000,
                            "output_tokens": 500,
                        }
                    ],
                    "latency_ms": 1000,
                    "quality_score": "0.80",
                }
            ],
        },
        "candidate": {
            "name": "feature/new-prompt",
            "runs": [
                {
                    "case_id": "support-question",
                    "calls": [
                        {
                            "provider": "example-ai",
                            "model": "small",
                            "input_tokens": 1500,
                            "output_tokens": 500,
                        }
                    ],
                    "latency_ms": 1200,
                    "quality_score": "0.84",
                }
            ],
        },
        "pricing": {
            "version": "test-2026-09",
            "source": "test fixture",
            "entries": [
                {
                    "provider": "example-ai",
                    "model": "small",
                    "input_per_million_usd": 10,
                    "output_per_million_usd": 20,
                }
            ],
        },
        "monthly_requests": 100000,
        "policy": {"max_cost_increase_percent": 20},
    }


class ComparisonTests(unittest.TestCase):
    def test_compares_cost_latency_quality_and_projection(self) -> None:
        report = compare_workloads(ComparisonRequest.model_validate(request_data()))

        self.assertEqual(report.cost_per_request_usd.baseline, Decimal("0.02000000"))
        self.assertEqual(report.cost_per_request_usd.candidate, Decimal("0.02500000"))
        self.assertEqual(report.cost_per_request_usd.delta_percent, Decimal("25.00"))
        self.assertEqual(report.mean_latency_ms.delta_percent, Decimal("20.00"))
        self.assertEqual(report.quality_score.delta, Decimal("0.0400"))
        self.assertEqual(report.quality_cases_compared, 1)
        self.assertEqual(
            report.monthly_projection.cost_usd.delta,
            Decimal("500.00000000"),
        )
        self.assertEqual(report.policy.status, "fail")
        self.assertEqual(report.case_costs[0].case_id, "support-question")
        self.assertEqual(report.case_costs[0].cost_usd.delta, Decimal("0.00500000"))
        self.assertEqual(len(report.model_costs), 1)

    def test_passes_policy_when_regression_is_allowed(self) -> None:
        data = request_data()
        data["policy"]["max_cost_increase_percent"] = 25

        report = compare_workloads(ComparisonRequest.model_validate(data))

        self.assertEqual(report.policy.status, "pass")
        self.assertEqual(report.policy.violations, [])

    def test_policy_uses_unrounded_values(self) -> None:
        data = request_data()
        baseline_call = data["baseline"]["runs"][0]["calls"][0]
        candidate_call = data["candidate"]["runs"][0]["calls"][0]
        baseline_call.update(input_tokens=25000, output_tokens=0)
        candidate_call.update(input_tokens=30001, output_tokens=0)
        data["policy"]["max_cost_increase_percent"] = 20

        report = compare_workloads(ComparisonRequest.model_validate(data))

        self.assertEqual(report.cost_per_request_usd.delta_percent, Decimal("20.00"))
        self.assertEqual(report.policy.status, "fail")

    def test_rejects_negative_tokens(self) -> None:
        data = request_data()
        data["candidate"]["runs"][0]["calls"][0]["input_tokens"] = -1

        with self.assertRaises(ValidationError):
            ComparisonRequest.model_validate(data)

    def test_rejects_boolean_token_counts(self) -> None:
        data = request_data()
        data["candidate"]["runs"][0]["calls"][0]["input_tokens"] = True

        with self.assertRaises(ValidationError):
            ComparisonRequest.model_validate(data)

    def test_quality_drop_policy_compares_against_the_baseline(self) -> None:
        data = request_data()
        data["policy"] = {"max_quality_drop": 0}
        self.assertEqual(compare_workloads(ComparisonRequest.model_validate(data)).policy.status, "pass")
        data["candidate"]["runs"][0]["quality_score"] = "0.70"
        policy = compare_workloads(ComparisonRequest.model_validate(data)).policy
        self.assertEqual(policy.status, "fail")
        self.assertEqual(policy.violations, ["quality fell from 0.8 to 0.7, more than the allowed drop of 0"])
        data["policy"] = {"max_quality_drop": "0.1"}
        self.assertEqual(compare_workloads(ComparisonRequest.model_validate(data)).policy.status, "pass")
        data["candidate"]["runs"][0]["quality_score"] = None
        self.assertEqual(compare_workloads(ComparisonRequest.model_validate(data)).policy.status, "inconclusive")

    def test_reports_when_no_quality_cases_are_comparable(self) -> None:
        data = request_data()
        data["candidate"]["runs"][0]["quality_score"] = None

        report = compare_workloads(ComparisonRequest.model_validate(data))

        self.assertIsNone(report.quality_score)
        self.assertEqual(report.quality_cases_compared, 0)

    def test_requires_matching_cases(self) -> None:
        data = request_data()
        data["candidate"]["runs"][0]["case_id"] = "different-case"

        with self.assertRaisesRegex(ValidationError, "case_id values must match"):
            ComparisonRequest.model_validate(data)

    def test_requires_pricing_for_every_used_model(self) -> None:
        data = request_data()
        data["pricing"]["entries"] = [
            {
                "provider": "example-ai",
                "model": "other",
                "input_per_million_usd": 1,
                "output_per_million_usd": 1,
            }
        ]

        with self.assertRaisesRegex(ValidationError, "pricing is missing"):
            ComparisonRequest.model_validate(data)


class CostPerSuccessTests(unittest.TestCase):
    """Cost per successful task is mean cost per request over mean quality score."""

    def compare(self, baseline_score="1", candidate_score="0.5", **policy):
        data = request_data()  # $0.02 per request before, $0.025 after.
        data["baseline"]["runs"][0]["quality_score"] = baseline_score
        data["candidate"]["runs"][0]["quality_score"] = candidate_score
        data["policy"] = policy or None
        return compare_workloads(ComparisonRequest.model_validate(data))

    def test_a_cheaper_request_can_be_a_dearer_success(self) -> None:
        success = self.compare().cost_per_successful_task_usd
        self.assertEqual((success.baseline, success.candidate), (Decimal("0.02"), Decimal("0.05")))
        self.assertEqual((success.delta, success.delta_percent), (Decimal("0.03"), Decimal("150.00")))

    def test_a_fractional_score_counts_as_a_partial_success(self) -> None:
        success = self.compare("0.8", "0.8").cost_per_successful_task_usd
        self.assertEqual((success.baseline, success.candidate), (Decimal("0.025"), Decimal("0.03125")))

    def test_a_side_without_successes_has_no_cost_per_success(self) -> None:
        success = self.compare(candidate_score="0").cost_per_successful_task_usd
        self.assertEqual((success.baseline, success.candidate), (Decimal("0.02"), None))
        self.assertIsNone(success.delta_percent)

    def test_it_is_not_measured_unless_every_case_is_scored_on_both_sides(self) -> None:
        self.assertIsNone(self.compare(candidate_score=None).cost_per_successful_task_usd)

    def test_the_increase_limit_compares_against_the_baseline(self) -> None:
        self.assertEqual(self.compare(max_cost_per_success_increase_percent=150).policy.status, "pass")
        policy = self.compare(max_cost_per_success_increase_percent=100).policy
        self.assertEqual(policy.status, "fail")
        self.assertEqual(policy.violations, ["cost per successful task increase 150.00% exceeds 100%"])

    def test_the_absolute_limit_is_checked_against_the_candidate(self) -> None:
        self.assertEqual(self.compare(max_candidate_cost_per_success_usd="0.05").policy.status, "pass")
        self.assertEqual(self.compare(max_candidate_cost_per_success_usd="0.04").policy.status, "fail")

    def test_a_candidate_that_never_succeeds_breaks_either_limit(self) -> None:
        for limit in ({"max_cost_per_success_increase_percent": 1000}, {"max_candidate_cost_per_success_usd": 1000}):
            policy = self.compare(candidate_score="0", **limit).policy
            self.assertEqual((policy.status, policy.violations), ("fail", ["the candidate has no successful tasks"]))

    def test_a_baseline_without_successes_leaves_the_increase_unknown(self) -> None:
        policy = self.compare(baseline_score="0", max_cost_per_success_increase_percent=10).policy
        self.assertEqual(policy.status, "inconclusive")

    def test_unscored_cases_make_the_limit_inconclusive_not_a_pass(self) -> None:
        policy = self.compare(candidate_score=None, max_cost_per_success_increase_percent=10).policy
        self.assertEqual(policy.status, "inconclusive")


if __name__ == "__main__":
    unittest.main()
