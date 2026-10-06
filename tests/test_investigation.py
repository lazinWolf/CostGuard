import json
import unittest
from decimal import Decimal

import httpx

from costguard.economics import compare_artifacts
from costguard.execution import ExperimentSpec
from costguard.investigation import AnalystSettings, Conclusion, EvidenceTools, PromptProposal, investigate
from demo.mock_provider import analyst_message
from tests.test_execution import artifact


def settings(**updates):
    values = dict(provider="test", model="analyst", pricing_version="test",
                  input_per_million_usd=10, output_per_million_usd=20, stop_after_usd="0.10")
    values.update(updates)
    return AnalystSettings.model_validate(values)


def evidence(with_source=True):
    left, right = artifact("baseline"), artifact("candidate", 2000)
    left.configuration = {"system_prompt": "Answer briefly."}
    right.configuration = {"system_prompt": "Longer candidate instructions."}
    saved = {"id": "report", "request": {"baseline_id": str(left.artifact_id),
        "candidate_id": str(right.artifact_id), "pricing": left.pricing.model_dump(mode="json")},
        "report": compare_artifacts(left, right, left.pricing).model_dump(mode="json")}
    source = ExperimentSpec(suite_id="suite", name="source", provider="test", model="echo",
        system_prompt="Longer candidate instructions.",
        cases=[{"case_id": "one", "prompt": "hello", "expected_text": "hello"}], pricing=left.pricing)
    return EvidenceTools(saved, left, right, source if with_source else None)


def response(message, usage=True):
    body = {"model": "analyst", "choices": [{"message": message, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 100, "completion_tokens": 50}
    return httpx.Response(200, json=body)


def tool_message(name, arguments):
    return {"role": "assistant", "tool_calls": [{"id": "call", "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class InvestigationTests(unittest.TestCase):
    def test_agent_inspects_evidence_and_proposes_without_execution(self):
        tools = evidence()
        receipts = []

        def handle(request):
            body = json.loads(request.content)
            self.assertEqual(body["model"], "test/analyst")
            self.assertTrue(body["tools"])
            return response(analyst_message(body["messages"]))

        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            result = investigate("Find the regression", settings(), tools, client,
                                 progress=lambda value: receipts.append(json.loads(json.dumps(value))))
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(result["calls"]), 3)
        self.assertEqual(Decimal(result["cost_usd"]), Decimal("0.006"))
        self.assertEqual([event["tool"] for event in result["events"]],
                         ["inspect_comparison", "inspect_case", "propose_experiment", "finish_investigation"])
        self.assertEqual(result["proposal"]["spec"]["system_prompt"], "Answer briefly.")
        # A usage receipt is saved before any tool result from that response.
        self.assertEqual(receipts[0]["events"], [])
        self.assertEqual(len(receipts[0]["calls"]), 1)
        self.assertEqual(receipts[0]["calls"][0]["status"], "in_flight")
        self.assertFalse(receipts[0]["cost_known"])
        self.assertEqual(receipts[1]["calls"][0]["status"], "ok")

    def test_spend_threshold_prevents_another_call(self):
        requests = []
        def handle(request):
            requests.append(request)
            return response(tool_message("inspect_comparison", {}))
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            result = investigate("Find costs", settings(stop_after_usd="0.001"), evidence(), client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["state"], "interrupted")
        self.assertIn("threshold", result["error"])

    def test_missing_usage_is_unknown_and_stops_the_loop(self):
        requests = []
        def handle(request):
            requests.append(request)
            return response(tool_message("inspect_comparison", {}), usage=False)
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            result = investigate("Find costs", settings(), evidence(), client)
        self.assertEqual(len(requests), 1)
        self.assertFalse(result["cost_known"])
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["state"], "interrupted")

    def test_citation_to_uninspected_case_is_rejected(self):
        tools = evidence()
        tools.overview()
        answer = Conclusion(summary="Found a change", findings=[
            {"text": "Claim", "evidence": ["case:one"]}])
        with self.assertRaisesRegex(ValueError, "not inspected"):
            tools.finish(answer)
        tools.inspect_case("one")
        self.assertEqual(tools.finish(answer)["summary"], "Found a change")

    def test_tools_cannot_access_unrelated_cases(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            evidence().inspect_case("another-suite-case")

    def test_same_turn_read_and_finish_cannot_claim_unseen_results(self):
        read = tool_message("inspect_comparison", {})["tool_calls"][0]
        finish = tool_message("finish_investigation", {"summary": "Claim",
            "findings": [{"text": "Unseen evidence claim", "evidence": ["comparison"]}]})["tool_calls"][0]
        finish["id"] = "finish"
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                lambda request: response({"role": "assistant", "tool_calls": [read, finish]}))) as client:
            result = investigate("Find costs", settings(max_calls=1), evidence(), client)
        self.assertEqual(result["state"], "interrupted")
        self.assertIsNone(result["conclusion"])

    def test_prompt_proposal_preserves_cases_prices_and_limits(self):
        tools = evidence()
        original = tools.source.model_dump(mode="json")
        tools.propose(PromptProposal(system_prompt="Answer briefly.", rationale="Measure fewer input tokens"))
        proposed = tools.proposal["spec"]
        for field in ("cases", "suite_id", "provider", "model", "pricing", "limits", "kind"):
            self.assertEqual(proposed[field], original[field])
        with self.assertRaisesRegex(ValueError, "One prompt"):
            tools.propose(PromptProposal(system_prompt="Another prompt", rationale="Another test"))

    def test_imported_evidence_can_be_inspected_but_not_executed(self):
        tools = evidence(with_source=False)
        self.assertFalse(tools.overview()["can_propose_prompt_experiment"])
        with self.assertRaisesRegex(ValueError, "no executable"):
            tools.propose(PromptProposal(system_prompt="Answer briefly.", rationale="Measure"))

    def test_unavailable_tool_is_not_executed(self):
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                lambda request: response(tool_message("execute_paid_workload", {})))) as client:
            result = investigate("Find costs", settings(max_calls=1), evidence(), client)
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(result["events"][0]["tool"], "unknown")
        self.assertIn("error", result["events"][0]["result"])
        self.assertIsNone(result["proposal"])

    def test_gateway_failure_is_not_retried_or_treated_as_free(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(401, text="private provider details")
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            result = investigate("Find costs", settings(), evidence(), client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["cost_known"])
        self.assertNotIn("private provider", json.dumps(result))

    def test_cancelled_inflight_call_is_accounted_without_more_tools(self):
        active = [True]
        def handle(request):
            active[0] = False
            return response(tool_message("inspect_comparison", {}))
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            result = investigate("Find costs", settings(), evidence(), client,
                                 should_continue=lambda: active[0])
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(len(result["calls"]), 1)
        self.assertTrue(result["cost_known"])
        self.assertEqual(result["events"], [])

    def test_plain_text_cannot_become_an_unvalidated_conclusion(self):
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                lambda request: response({"content": "Ship it; everything is safe"}))) as client:
            result = investigate("Find costs", settings(), evidence(), client)
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(result["conclusion"])


if __name__ == "__main__":
    unittest.main()
