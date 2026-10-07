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
        "schema_version": 2, "suite_cases": [{"case_id": "one", "prompt": "hello", "expected_text": "hello"}],
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

        def response(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1}})

        with httpx.Client(base_url="http://test", transport=httpx.MockTransport(response)) as client:
            case = run_case(spec, spec.cases[0], client)
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

    def test_python_hook_marks_missing_usage(self):
        recorder = ArtifactRecorder(suite_id="suite", name="hook", pricing=artifact("price").pricing)
        with recorder.case("one") as case:
            case.record_chat(provider="test", model="echo", response={}, latency_ms=Decimal("1"))
        self.assertEqual(recorder.artifact().cases[0].calls[0].usage_source, "missing")


if __name__ == "__main__":
    unittest.main()
