import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

import httpx

from costguard import evaluators
from costguard.execution import Judge


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


class JudgeTests(unittest.TestCase):
    spec = SimpleNamespace(evaluator="llm_judge", judge=Judge(
        provider="test", model="judge", rubric="Polite and short.",
        input_per_million_usd=10, output_per_million_usd=20))
    case = SimpleNamespace(prompt="Where is my refund?", expected_text=None)

    def judge(self, reply, usage=True, status=200, seen=None):
        def handle(request):
            if seen is not None:
                seen.append(json.loads(request.content))
            body = {"choices": [{"message": {"content": reply}}]}
            if usage:
                body["usage"] = {"prompt_tokens": 100, "completion_tokens": 50}
            return httpx.Response(status, json=body)
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            return evaluators.evaluate(self.spec, self.case, "It arrives in 5 days.", client)

    def test_a_verdict_becomes_a_score_with_its_reason_and_cost(self):
        passed = self.judge('{"pass": true, "reason": "Polite and short."}')
        self.assertEqual((passed.quality, passed.evaluator), (Decimal("1"), "llm_judge:v1:test/judge"))
        self.assertEqual(passed.judgement.reason, "Polite and short.")
        self.assertEqual(passed.judgement.cost_usd, Decimal("0.002"))  # 100 in at $10 + 50 out at $20, per million.
        self.assertEqual(self.judge('```json\n{"pass": false, "reason": "Too long."}\n```').quality, Decimal("0"))

    def test_the_judge_is_pinned_and_sees_the_answer_as_data(self):
        seen = []
        self.judge('{"pass": true, "reason": ""}', seen=seen)
        request = seen[0]
        self.assertEqual((request["model"], request["temperature"]), ("test/judge", 0))
        self.assertIn("Polite and short.", request["messages"][0]["content"])
        self.assertEqual(json.loads(request["messages"][1]["content"]),
                         {"message": "Where is my refund?", "answer": "It arrives in 5 days."})

    def test_no_usable_verdict_means_no_score_not_a_pass(self):
        for reply in ("Looks good to me", '{"pass": "yes"}', '{"reason": "fine"}', "[true]", None, ""):
            result = self.judge(reply)
            self.assertIsNone(result.quality, reply)
            self.assertIsNone(result.judgement.passed)
            self.assertEqual(result.evaluator, "llm_judge:v1:test/judge")

    def test_an_empty_reply_says_why(self):
        self.assertIn("whole token budget", self.judge("").judgement.reason)

    def test_an_unreachable_judge_is_no_score_and_unknown_spend(self):
        result = self.judge("", status=500)
        self.assertIsNone(result.quality)
        self.assertIn("could not be reached", result.judgement.reason)
        self.assertIsNone(self.judge('{"pass": true, "reason": "ok"}', usage=False).judgement.cost_usd)


if __name__ == "__main__":
    unittest.main()
