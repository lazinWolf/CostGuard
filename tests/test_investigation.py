import json
import unittest
from decimal import Decimal

import httpx

from costguard.economics import compare_artifacts
from costguard.execution import ExperimentSpec
from costguard.investigation import (AnalystSettings, Conclusion, EvidenceTools, PromptProposal,
                                     evidence_numbers, investigate, unsupported_numbers)
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
        self.assertEqual(result["proposal"]["spec"]["name"], "analyst-fix-1")
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

    def test_paths_inside_inspected_evidence_resolve_to_their_reference(self):
        tools = evidence()
        tools.overview()
        tools.inspect_case("one")
        answer = Conclusion.model_validate({"summary": "Output grew", "findings": [
            {"id": 1, "text": "Verbose", "evidence": ["case:one.candidate.output", "comparison.metrics"]}]})
        self.assertEqual(tools.finish(answer)["findings"][0]["evidence"], ["case:one", "comparison"])
        other = Conclusion(summary="x", findings=[{"text": "y", "evidence": ["case:onex"]}])
        with self.assertRaisesRegex(ValueError, "not inspected"):
            tools.finish(other)

    def test_fixes_of_fixes_are_numbered(self):
        from costguard.investigation import fix_name
        self.assertEqual([fix_name("helpful-prompt"), fix_name("analyst-fix-1"), fix_name("analyst-fix-9")],
                         ["analyst-fix-1", "analyst-fix-2", "analyst-fix-10"])

    def test_tools_cannot_access_unrelated_cases(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            evidence().inspect_case("another-suite-case")

    def test_runnable_comparison_is_reminded_once_to_propose(self):
        finish = {"summary": "Cost rose", "findings": [{"text": "Input grew", "evidence": ["comparison"]}]}
        for replies, proposed in (
                ([("inspect_comparison", {}), ("finish_investigation", finish),
                  ("propose_experiment", {"system_prompt": "Answer briefly.", "rationale": "Shorter"}),
                  ("finish_investigation", finish)], True),
                ([("inspect_comparison", {}), ("finish_investigation", finish),
                  ("finish_investigation", finish)], False)):
            replies = iter(replies)
            with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                    lambda request: response(tool_message(*next(replies))))) as client:
                result = investigate("Find costs", settings(), evidence(), client)
            self.assertEqual(result["state"], "completed", result["error"])
            self.assertIn("propose_experiment", result["events"][1]["result"]["error"])
            self.assertEqual(result["proposal"] is not None, proposed)

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

    def test_overview_states_the_limits_a_proposal_is_judged_by(self):
        tools = evidence()
        tools.saved["request"]["policy"] = {"max_cost_increase_percent": "20", "max_quality_drop": "0",
                                            "min_candidate_quality_score": None}
        self.assertEqual(tools.overview()["policy_limits"],
                         {"max_cost_increase_percent": "20", "max_quality_drop": "0"})

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

    def test_object_arguments_and_fenced_conclusions_are_accepted(self):
        def handle(request):
            body = json.loads(request.content)
            if not any(message["role"] == "tool" for message in body["messages"]):
                return response({"role": "assistant", "tool_calls": [{"id": "read", "type": "function",
                    "function": {"name": "inspect_comparison", "arguments": {}}}]})
            conclusion = {"summary": "Cost rose", "findings": [{"text": "Input grew", "evidence": ["comparison"]}]}
            return response({"role": "assistant", "content": "```json\n" + json.dumps(conclusion) + "\n```"})
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            result = investigate("Find costs", settings(), evidence(), client)
        self.assertEqual(result["state"], "completed", result["error"])
        self.assertEqual(result["conclusion"]["summary"], "Cost rose")

    def test_list_fields_sent_as_json_strings_are_decoded(self):
        replies = iter([tool_message("inspect_comparison", {"unused": True}),
                        tool_message("finish_investigation", {"summary": "Cost rose",
                            "findings": json.dumps([{"text": "Input grew", "evidence": ["comparison"]}]),
                            "next_steps": "[\"Run the proposal\"]"})])
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                lambda request: response(next(replies)))) as client:
            result = investigate("Find costs", settings(), evidence(with_source=False), client)
        self.assertEqual(result["state"], "completed", result["error"])
        self.assertEqual(result["conclusion"]["next_steps"], ["Run the proposal"])

    def test_tool_schemas_omit_length_limits_but_validation_keeps_them(self):
        from costguard.investigation import TOOLS
        self.assertNotIn("maxLength", json.dumps(TOOLS))
        with self.assertRaises(ValueError):
            Conclusion(summary="", findings=[{"text": "x", "evidence": ["comparison"]}])

    def test_truncated_reasoning_names_the_output_limit(self):
        body = {"model": "analyst", "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 1200}}
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=body))) as client:
            result = investigate("Find costs", settings(), evidence(), client)
        self.assertEqual(result["state"], "failed")
        self.assertIn("1200-token limit", result["error"])
        self.assertTrue(result["cost_known"])

    def test_case_inspection_includes_recorded_output(self):
        tools = evidence()
        tools.candidate.cases[0].output = "A long explanation"
        self.assertEqual(tools.inspect_case("one")["candidate"]["output"], "A long explanation")

    def test_plain_text_cannot_become_an_unvalidated_conclusion(self):
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                lambda request: response({"content": "Ship it; everything is safe"}))) as client:
            result = investigate("Find costs", settings(max_calls=2), evidence(), client)
        self.assertEqual(result["state"], "interrupted")
        self.assertIsNone(result["conclusion"])
        self.assertEqual([event["tool"] for event in result["events"]], ["format_reminder"] * 2)
        self.assertIn("structured conclusion", result["error"])

    def test_tool_mistakes_are_explained_to_the_analyst(self):
        replies = iter([
            tool_message("inspect_case", {"case_id": "the most expensive case"}),
            tool_message("inspect_case", {"wrong": "field"}),
        ])
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(
                lambda request: response(next(replies)))) as client:
            result = investigate("Find costs", settings(max_calls=2), evidence(), client)
        self.assertIn("valid case_id values: one", result["events"][0]["result"]["error"])
        self.assertIn("case_id: Field required", result["events"][1]["result"]["error"])
        self.assertNotIn("most expensive", json.dumps(result["events"][1]))


class NumberCheckTests(unittest.TestCase):
    outputs = [{"evidence_ref": "comparison", "metrics": {
                    "cost_per_request_usd": {"baseline": "0.00005071", "candidate": "0.00017070", "delta_percent": "236.5743"},
                    "quality_score": {"baseline": "0.8750", "candidate": "0.0000"}}},
               {"evidence_ref": "case:refund", "baseline": {"calls": [{"output_tokens": 58}]},
                "candidate": {"calls": [{"output_tokens": 400}, {"output_tokens": 12}]}}]

    def check(self, text):
        return unsupported_numbers(text, evidence_numbers(self.outputs))

    def test_numbers_rounded_to_their_written_precision_are_found(self):
        self.assertEqual(self.check("Cost rose 236.57% ($0.0000507 → $0.0001707), about 237%."), [])
        self.assertEqual(self.check("Quality fell from 87.5% (0.875) to 0."), [])

    def test_sums_and_approximations_are_found(self):
        self.assertEqual(self.check("The candidate wrote 412 tokens, ≈60 before."), [])

    def test_numbers_missing_from_the_evidence_are_reported_as_written(self):
        self.assertEqual(self.check("Latency rose 138.14% and output reached 1,250 tokens, costing $5."),
                         ["138.14%", "1,250", "$5"])
        self.assertEqual(self.check("Output went from 60 to 412 tokens."), ["60"])

    def test_identifiers_and_small_counts_are_not_checked(self):
        self.assertEqual(self.check("case-2 on llama3.2:3b-q4_K_M used 3 calls."), [])

    def test_answer_links_cited_cases_and_flags_unfound_numbers(self):
        from costguard.web import _answer
        row = {"report_id": "r1", "result": {
            "events": [{"tool": "inspect_comparison", "result": self.outputs[0]},
                       {"tool": "inspect_case", "result": self.outputs[1]}],
            "conclusion": {"summary": "Cost rose 236.57%.", "hypotheses": [], "next_steps": [], "findings": [
                {"text": "Output grew to 412 tokens.", "evidence": ["case:refund", "comparison"]},
                {"text": "Latency rose 138.14%.", "evidence": ["comparison"]}]}}}
        answer = _answer(row)
        self.assertEqual(answer["summary_unsupported"], [])
        self.assertEqual(answer["findings"][0]["links"], [
            {"label": "case refund", "href": "/reports/r1#case-refund"}, {"label": "report", "href": "/reports/r1"}])
        self.assertEqual(answer["findings"][0]["unsupported"], [])
        self.assertEqual(answer["findings"][1]["unsupported"], ["138.14%"])
        self.assertTrue(answer["findings"][0]["quotes_numbers"])

if __name__ == "__main__":
    unittest.main()
