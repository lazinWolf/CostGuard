import json
import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import httpx
from pydantic import ValidationError

from costguard.economics import compare_artifacts
from costguard.api import ScenarioInput, scenario
from costguard.execution import ExecutionArtifact, ExperimentSpec
from costguard.hook import ArtifactRecorder
from costguard.models import ComparisonPolicy
from costguard.runner import run_case


def artifact(name: str, tokens: int = 1000, *, source: str = "provider", evaluator: str = "exact:v1"):
    call = {"call_id": "call", "step_id": "step", "provider": "test", "model": "echo",
            "input_tokens": tokens if source != "missing" else None,
            "output_tokens": 0 if source != "missing" else None,
            "usage_source": source, "status": "ok", "latency_ms": 100, "attempt": 1}
    return ExecutionArtifact.model_validate({
        "artifact_id": str(uuid4()), "suite_id": "suite", "name": name,
        "pricing": {"version": "test", "entries": [{"provider": "test", "model": "echo",
            "input_per_million_usd": 10, "output_per_million_usd": 20}]},
        "cases": [{"case_id": "one", "status": "ok", "latency_ms": 100,
                   "calls": [call], "steps": [{"step_id": "step", "kind": "model",
                       "name": "echo", "latency_ms": 100}],
                   "quality_score": "1", "evaluator": evaluator}],
    })


class ExecutionTests(unittest.TestCase):
    def test_complete_regression_is_attributed(self):
        left, right = artifact("baseline"), artifact("candidate", 2000)
        result = compare_artifacts(left, right, left.pricing)
        self.assertEqual(result.economic.cost_per_request_usd.delta, Decimal("0.01000000"))
        self.assertEqual(result.p95_case_cost_usd.candidate, Decimal("0.02000000"))
        self.assertTrue(any("one" in item for item in result.contributors))

    def test_missing_usage_is_inconclusive_not_free(self):
        left, right = artifact("baseline"), artifact("candidate", source="missing")
        result = compare_artifacts(left, right, left.pricing)
        self.assertEqual(result.status, "inconclusive")
        self.assertIsNone(result.economic)
        self.assertEqual(result.missing_usage_calls, 1)

    def test_quality_versions_must_match(self):
        left, right = artifact("baseline"), artifact("candidate", evaluator="other:v1")
        result = compare_artifacts(left, right, left.pricing)
        self.assertIsNone(result.economic.quality_score)

    def test_failed_case_is_inconclusive(self):
        left, right = artifact("baseline"), artifact("candidate")
        right.cases[0].status = "error"
        result = compare_artifacts(left, right, left.pricing)
        self.assertEqual(result.status, "inconclusive")

    def test_gate_markdown_states_verdict_and_metrics(self):
        from costguard.__main__ import _markdown
        left, right = artifact("baseline"), artifact("candidate", 2000)
        report = compare_artifacts(left, right, left.pricing,
                                   ComparisonPolicy(max_cost_increase_percent=10), 1000)
        text = _markdown(report)
        self.assertIn("### candidate vs baseline", text)
        self.assertIn("❌ FAIL", text)
        self.assertIn("| Cost per request (USD) | 0.01 | 0.02 | +100% |", text)
        self.assertIn("| Monthly cost at 1,000 requests (USD) | 10.00 | 20.00 | +100% |", text)
        self.assertIn("| Mean latency (ms) | 100 | 100 | 0% |", text)

    def test_limits_are_described_in_plain_words(self):
        from costguard.web import _limits
        self.assertEqual(_limits({"max_cost_increase_percent": "20", "max_quality_drop": "0",
                                  "min_candidate_quality_score": "0.85"}),
                         ["cost per request may rise by at most 20%",
                          "no fewer correct answers than the baseline", "at least 85% of answers correct"])
        self.assertEqual(_limits({"max_quality_drop": "0.1"}),
                         ["correct answers may fall at most 10 percentage points below the baseline"])

    def test_scenario_rejects_failed_evidence(self):
        run = artifact("failed")
        run.cases[0].status = "error"
        with patch("costguard.api.db.get_artifact", return_value=run):
            result = scenario(ScenarioInput(
                artifact_id=str(run.artifact_id), pricing=run.pricing, monthly_requests=100,
            ))
        self.assertEqual(result["status"], "inconclusive")

    def test_execution_behavior_policy(self):
        left, right = artifact("baseline"), artifact("candidate")
        retry = right.cases[0].calls[0].model_copy(update={"call_id": "retry", "attempt": 2})
        right.cases[0].calls.append(retry)
        result = compare_artifacts(left, right, left.pricing, ComparisonPolicy(
            max_candidate_mean_latency_ms=50,
            max_candidate_retries_per_case=0,
        ))
        self.assertEqual(result.status, "fail")
        self.assertEqual(len(result.policy.violations), 2)
        self.assertEqual(result.policy.status, result.economic.policy.status)

    def test_call_must_reference_step(self):
        run = artifact("baseline")
        run.cases[0].calls[0].step_id = "wrong"
        with self.assertRaisesRegex(ValidationError, "call step_id"):
            ExecutionArtifact.model_validate(run.model_dump())

    def test_runner_records_gateway_usage_and_quality(self):
        spec = ExperimentSpec.model_validate({
            "suite_id": "suite", "name": "run", "provider": "test", "model": "echo",
            "cases": [{"case_id": "one", "prompt": "hello", "expected_text": "hello"}],
            "pricing": artifact("price").pricing.model_dump(),
        })

        bodies = []

        def response(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1}})

        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
            case = run_case(spec, spec.cases[0], client)
            run_case(spec.model_copy(update={"temperature": 0}), spec.cases[0], client)
        self.assertNotIn("temperature", bodies[0])  # The provider default applies unless set.
        self.assertEqual(bodies[1]["temperature"], 0)
        self.assertEqual(case.status, "ok")
        self.assertEqual(case.calls[0].input_tokens, 10)
        self.assertEqual(case.quality_score, Decimal("1"))

    def test_python_hook_records_existing_application_evidence(self):
        recorder = ArtifactRecorder(suite_id="suite", name="hook", pricing=artifact("price").pricing)
        with recorder.case("one") as case:
            model_step = case.record_chat(
                provider="test", model="echo",
                response={"usage": {"prompt_tokens": 10, "completion_tokens": 2}},
                latency_ms=Decimal("12"),
            )
            case.record_step(kind="tool", name="search", latency_ms=Decimal("3"),
                             parent_step_id=model_step)
            case.score(Decimal("1"), "exact:v1")
        captured = recorder.artifact()
        self.assertEqual(captured.cases[0].calls[0].input_tokens, 10)
        self.assertEqual(captured.cases[0].steps[1].kind, "tool")
        self.assertEqual(captured.cases[0].quality_score, Decimal("1"))

    def test_partial_quality_coverage_is_inconclusive(self):
        def two_cases(name, scored_second):
            run = artifact(name)
            second = run.cases[0].model_copy(update={"case_id": "two"})
            if not scored_second:
                second = second.model_copy(update={"quality_score": None, "evaluator": None})
            run.cases.append(second)
            return run
        left, right = two_cases("baseline", True), two_cases("candidate", False)
        result = compare_artifacts(left, right, left.pricing,
                                   ComparisonPolicy(min_candidate_quality_score=1))
        self.assertEqual(result.status, "inconclusive")
        self.assertIn("quality scores cover only 1 of 2 cases", result.policy.unknowns)
        complete = compare_artifacts(left, two_cases("candidate", True), left.pricing,
                                     ComparisonPolicy(min_candidate_quality_score=1))
        self.assertEqual(complete.status, "pass")

    def test_money_is_serialized_exactly(self):
        result = compare_artifacts(artifact("baseline"), artifact("candidate", 2000), artifact("p").pricing)
        delta = result.model_dump(mode="json")["economic"]["cost_per_request_usd"]["delta"]
        self.assertEqual(delta, "0.01000000")
        same = compare_artifacts(artifact("baseline"), artifact("candidate"), artifact("p").pricing)
        self.assertEqual(same.model_dump(mode="json")["economic"]["cost_per_request_usd"]["delta"], "0.00000000")

    def _spec(self, **updates):
        values = {"suite_id": "suite", "name": "run", "provider": "test", "model": "echo",
                  "cases": [{"case_id": "one", "prompt": "hello", "expected_text": "billing"}],
                  "pricing": artifact("price").pricing.model_dump(),
                  "limits": {"max_attempts": 3}}
        values.update(updates)
        return ExperimentSpec.model_validate(values)

    def test_client_errors_are_not_retried(self):
        spec, requests = self._spec(), []
        def response(request):
            requests.append(request)
            return httpx.Response(401, json={"error": "bad key"})
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
            case = run_case(spec, spec.cases[0], client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(case.error, "model request failed: HTTP 401 (the gateway or provider rejected the credentials)")
        self.assertNotIn("bad key", case.error)

    def test_gateway_failures_are_explained_without_provider_text(self):
        from costguard.ratelimit import error_reason
        reason = lambda status, body=None: error_reason(httpx.Response(status, json=body) if body else httpx.Response(status))
        self.assertEqual(reason(400, {"error": {"message": "This model's maximum context length is 8192 tokens"}}),
                         "the request exceeded the model's context window")
        self.assertEqual(reason(400, {"error": {"message": "no keys found that support model: x"}}),
                         "the gateway has no route for this model")
        self.assertEqual(reason(504), "the model did not answer within the gateway's request timeout")
        self.assertIn("context window filling up", reason(500, {"error": {"message": "secret-ish detail"}}))
        self.assertEqual(reason(422, {"error": "secret-ish detail"}), "")

    def test_server_errors_are_retried_and_output_recorded(self):
        spec, requests = self._spec(evaluator="normalized"), []
        def response(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"choices": [{"message": {"content": " **Billing.**\n"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
            case = run_case(spec, spec.cases[0], client)
        self.assertEqual(len(requests), 2)
        self.assertEqual(case.quality_score, Decimal("1"))
        self.assertEqual(case.evaluator, "normalized:v1")
        self.assertEqual(case.output, "**Billing.**")

    def test_rate_limited_requests_wait_and_are_not_counted_as_calls(self):
        spec, requests = self._spec(), []
        def response(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(429, headers={"retry-after": "0"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "billing"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1}})
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
            case = run_case(spec, spec.cases[0], client)
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(case.calls), 1)
        self.assertEqual(case.calls[0].attempt, 1)
        self.assertEqual(case.status, "ok")
        self.assertEqual(case.calls[0].tags, {"rate_limit_wait_ms": "0"})

    def test_rate_limit_wait_is_bounded(self):
        from costguard import ratelimit
        waits = []
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"x-ratelimit-reset-tokens": "1m0.5s"}))) as client:
            response = ratelimit.post(client, "/v1/chat/completions", json={}, timeout=1,
                                      sleep=waits.append, max_wait=150)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(waits, [60.5, 60.5])

    def test_exact_evaluator_rejects_decorated_answers(self):
        spec = self._spec()
        def response(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "Billing."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
            case = run_case(spec, spec.cases[0], client)
        self.assertEqual(case.quality_score, Decimal("0"))

    def test_python_hook_marks_missing_usage(self):
        recorder = ArtifactRecorder(suite_id="suite", name="hook", pricing=artifact("price").pricing)
        with recorder.case("one") as case:
            case.record_chat(provider="test", model="echo", response={}, latency_ms=Decimal("1"))
        self.assertEqual(recorder.artifact().cases[0].calls[0].usage_source, "missing")


if __name__ == "__main__":
    unittest.main()
