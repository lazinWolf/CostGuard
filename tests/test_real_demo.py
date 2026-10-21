import unittest
from decimal import Decimal

from pydantic import ValidationError

from costguard.real_demo import RealDemoInput, build_specs, suite


class RealDemoTests(unittest.TestCase):
    def test_builds_paired_specs_for_the_selected_model(self):
        baseline, candidate, pricing = build_specs(RealDemoInput(
            model="ollama/llama3.2:3b-instruct-q4_K_M",
            input_per_million_usd=Decimal("0.1"), output_per_million_usd=Decimal("0.4")))
        self.assertEqual((baseline.provider, baseline.model), ("ollama", "llama3.2:3b-instruct-q4_K_M"))
        self.assertEqual(baseline.suite_id, candidate.suite_id)
        self.assertEqual([c.case_id for c in baseline.cases], [c.case_id for c in candidate.cases])
        self.assertNotEqual(baseline.system_prompt, candidate.system_prompt)
        self.assertEqual(baseline.evaluator, "normalized")
        self.assertEqual((baseline.temperature, candidate.temperature), (0, 0))
        self.assertEqual(pricing.version, suite()["reference_pricing"]["version"])
        self.assertTrue(all(case.expected_text for case in baseline.cases))

    def test_model_must_be_a_gateway_route(self):
        with self.assertRaises(ValidationError):
            RealDemoInput(model="llama3.2", input_per_million_usd=0, output_per_million_usd=0)


if __name__ == "__main__":
    unittest.main()
