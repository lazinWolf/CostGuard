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


if __name__ == "__main__":
    unittest.main()
