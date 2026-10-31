import unittest
from decimal import Decimal
from types import SimpleNamespace

from costguard import evaluators


def score(evaluator, answer, expected):
    spec, case = SimpleNamespace(evaluator=evaluator), SimpleNamespace(expected_text=expected)
    return evaluators.evaluate(spec, case, answer, client=None)


class EvaluatorTests(unittest.TestCase):
    def test_exact_ignores_case_and_outer_whitespace_only(self):
        self.assertEqual(score("exact", " Billing\n", "billing"), evaluators.Score(Decimal("1"), "exact:v1"))
        self.assertEqual(score("exact", "billing.", "billing").quality, Decimal("0"))

    def test_normalized_also_ignores_punctuation_quotes_and_markdown(self):
        for answer in ("billing.", '"Billing"', "**billing**", "  BILLING  "):
            self.assertEqual(score("normalized", answer, "billing").quality, Decimal("1"), answer)
        self.assertEqual(score("normalized", "I think billing", "billing").quality, Decimal("0"))

    def test_a_case_without_an_expected_answer_has_no_score(self):
        self.assertIs(score("exact", "anything", None), evaluators.NO_SCORE)
        self.assertIs(score("normalized", "anything", None), evaluators.NO_SCORE)

    def test_new_evaluators_register_by_name(self):
        @evaluators.register("always-half")
        def half(spec, case, answer, client):
            return evaluators.Score(Decimal("0.5"), "always-half:v1")
        try:
            self.assertEqual(score("always-half", "x", None).quality, Decimal("0.5"))
        finally:
            del evaluators.REGISTRY["always-half"]


if __name__ == "__main__":
    unittest.main()
