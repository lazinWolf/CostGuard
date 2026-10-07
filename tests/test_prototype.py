import json
import unittest
from decimal import Decimal
from unittest.mock import patch, MagicMock
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from costguard.api import CandidateInput, analyst_for_report, start_candidate
from costguard.economics import compare_artifacts
from costguard.examples import triage_examples
from costguard.execution import CaseInput, Charge, ExecutionArtifact, ExperimentSpec, evaluate, suite_digest
from costguard.investigation import EvidenceTools
from costguard.models import ComparisonPolicy
from costguard.runner import run_case
from demo.mock_provider import completion
from examples.record_application import capture
from tests.test_execution import artifact


def fixture_run(spec):
    def handle(request):
        return httpx.Response(200, json=completion(json.loads(request.content)))
    with httpx.Client(base_url="http://mock", transport=httpx.MockTransport(handle)) as client:
        return capture(spec, client)


class PrototypeTests(unittest.TestCase):
    def test_manifest_identity_includes_expectations_and_partition(self):
        cases = [CaseInput(case_id="one", prompt="hello", expected_text="hello")]
        original = suite_digest(cases)
        self.assertEqual(original, suite_digest(list(reversed(cases))))
        cases[0].expected_text = "different"
        self.assertNotEqual(original, suite_digest(cases))
        cases[0].expected_text = "hello"
        cases[0].partition = "validation"
        self.assertNotEqual(original, suite_digest(cases))

    def test_manifest_rejects_forged_digest(self):
        values = artifact("baseline").model_dump()
        values["suite_digest"] = "forged"
        with self.assertRaisesRegex(ValidationError, "suite_digest"):
            ExecutionArtifact.model_validate(values)

    def test_changed_inputs_are_not_a_valid_comparison(self):
        left = artifact("baseline")
        values = artifact("candidate").model_dump()
        values["suite_digest"] = None
        values["suite_cases"][0]["prompt"] = "a different task"
        right = ExecutionArtifact.model_validate(values)
        report = compare_artifacts(left, right, left.pricing, ComparisonPolicy(max_cost_increase_percent=10))
        self.assertEqual(report.status, "inconclusive")
        self.assertIsNone(report.economic)

    def test_legacy_evidence_cannot_prove_a_pass(self):
        left = artifact("baseline")
        values = left.model_dump()
        values.update(artifact_id=uuid4(), schema_version=1, suite_cases=None, suite_digest=None)
        right = ExecutionArtifact.model_validate(values)
        report = compare_artifacts(left, right, left.pricing, ComparisonPolicy(max_cost_increase_percent=10))
        self.assertEqual(report.status, "inconclusive")
        self.assertIsNotNone(report.economic)  # Declared costs remain inspectable.

    def test_partial_suite_is_inconclusive_even_with_matching_execution_ids(self):
        left, right = artifact("baseline"), artifact("candidate")
        for run in (left,right):
            values=run.model_dump()
            values["suite_digest"]=None
            values["suite_cases"].append({"case_id":"unexecuted","prompt":"another case"})
            updated=ExecutionArtifact.model_validate(values)
            run.suite_cases,run.suite_digest=updated.suite_cases,updated.suite_digest
        self.assertEqual(compare_artifacts(left,right,left.pricing).status,"inconclusive")

    def test_json_evaluator_exposes_field_failures(self):
        case=CaseInput(case_id="one",prompt="ticket",expected_json={"category":"billing","priority":"high"})
        good=evaluate('{"priority":"high","category":"billing"}',case)
        self.assertEqual(good.score,1)
        wrong=evaluate('{"category":"other","priority":"high"}',case)
        self.assertEqual(wrong.score,0)
        self.assertFalse(wrong.checks["field:category"])
        self.assertEqual(evaluate('not json',case).score,0)
        self.assertEqual(evaluate('{"category":"billing","priority":"high","extra":"x"}',case).score,0)

    def test_evaluator_cannot_be_ambiguous(self):
        with self.assertRaises(ValidationError):
            CaseInput(case_id="one",prompt="ticket",expected_text="x",expected_json={"a":"b"})

    def test_false_evaluator_checks_cannot_be_imported_as_pass(self):
        values=artifact('candidate').model_dump()
        values['cases'][0]['evaluation']={'evaluator':'exact:v1','score':1,'checks':{'exact_text':False}}
        with self.assertRaisesRegex(ValidationError,'score must agree'):
            ExecutionArtifact.model_validate(values)

    def test_quality_gate_requires_full_coverage(self):
        left,right=artifact("baseline"),artifact("candidate")
        right.cases[0].quality_score=None
        report=compare_artifacts(left,right,left.pricing,ComparisonPolicy(min_candidate_quality_score=1))
        self.assertEqual(report.status,"inconclusive")
        self.assertEqual(report.quality_coverage,0)

    def test_reported_model_mismatch_is_inconclusive(self):
        left,right=artifact("baseline"),artifact("candidate")
        right.cases[0].calls[0].response_model="unexpected-model"
        report=compare_artifacts(left,right,left.pricing,ComparisonPolicy(max_cost_increase_percent=10))
        self.assertEqual(report.status,"inconclusive")
        self.assertTrue(any("Reported model" in reason for reason in report.reasons))

    def test_cost_components_reconcile_and_retry_is_a_subset(self):
        left,right=artifact("baseline"),artifact("candidate",2000)
        right.cases[0].charges=[Charge(name="tool",amount_usd="0.002",source="observed")]
        right=ExecutionArtifact.model_validate(right.model_dump())
        report=compare_artifacts(left,right,left.pricing)
        total=sum((report.attribution[key].candidate for key in ("input","output","non_model")),Decimal("0"))
        self.assertEqual(total,report.economic.cost_per_request_usd.candidate)
        self.assertEqual(report.attribution["retry_subset"].candidate,0)

    def test_receipt_exists_before_network_and_output_is_saved(self):
        spec=ExperimentSpec.model_validate(triage_examples()["spec"])
        receipts=[]
        def handle(request):
            self.assertEqual(receipts[-1]["calls"][0]["status"],"in_flight")
            self.assertIsNone(receipts[-1]["calls"][0]["input_tokens"])
            return httpx.Response(200,json=completion(json.loads(request.content)))
        with httpx.Client(base_url="http://mock",transport=httpx.MockTransport(handle)) as client:
            case=run_case(spec,spec.cases[0],client,checkpoint=lambda value:receipts.append(value.model_dump(mode="json")))
        self.assertEqual(case.status,"ok")
        self.assertEqual(case.quality_score,1)
        self.assertIn('billing',case.output_text)
        self.assertEqual(receipts[-1]["calls"][0]["finish_reason"],"stop")

    def test_unknown_failed_call_is_not_retried(self):
        spec=ExperimentSpec.model_validate(triage_examples()["spec"])
        spec.limits.max_attempts=3
        requests=[]
        def handle(request):
            requests.append(request)
            raise httpx.ReadTimeout("ambiguous charge",request=request)
        with httpx.Client(base_url="http://mock",transport=httpx.MockTransport(handle)) as client:
            case=run_case(spec,spec.cases[0],client)
        self.assertEqual(len(requests),1)
        self.assertEqual(case.status,"error")
        self.assertEqual(case.calls[0].usage_source,"missing")

    def test_truncated_answer_retains_usage_but_cannot_pass(self):
        spec=ExperimentSpec.model_validate(triage_examples()["spec"])
        def handle(request):
            data=completion(json.loads(request.content)); data["choices"][0]["finish_reason"]="length"
            return httpx.Response(200,json=data)
        with httpx.Client(base_url="http://mock",transport=httpx.MockTransport(handle)) as client:
            case=run_case(spec,spec.cases[0],client)
        self.assertEqual(case.status,"error")
        self.assertIsNotNone(case.calls[0].input_tokens)
        self.assertIsNone(case.quality_score)

    def test_boolean_usage_is_missing_not_one_token(self):
        spec=ExperimentSpec.model_validate(triage_examples()["spec"])
        def handle(request):
            data=completion(json.loads(request.content));data["usage"]["prompt_tokens"]=True
            return httpx.Response(200,json=data)
        with httpx.Client(base_url="http://mock",transport=httpx.MockTransport(handle)) as client:
            case=run_case(spec,spec.cases[0],client)
        self.assertEqual(case.calls[0].usage_source,"missing")

    def test_checkpoint_failure_prevents_a_provider_call(self):
        spec=ExperimentSpec.model_validate(triage_examples()["spec"])
        requests=[]
        def fail_checkpoint(case): raise RuntimeError("database failed")
        with httpx.Client(base_url="http://mock",transport=httpx.MockTransport(lambda request:requests.append(request))) as client:
            with self.assertRaises(RuntimeError):
                run_case(spec,spec.cases[0],client,checkpoint=fail_checkpoint)
        self.assertFalse(requests)

    def test_fixture_demonstrates_cost_and_quality_regressions(self):
        example=triage_examples();spec=ExperimentSpec.model_validate(example["spec"])
        left=fixture_run(spec)
        longer=fixture_run(ExperimentSpec.model_validate({**example["spec"],"system_prompt":example["variants"]["longer"]}))
        wrong=fixture_run(ExperimentSpec.model_validate({**example["spec"],"system_prompt":example["variants"]["cheap_wrong"]}))
        policy=ComparisonPolicy.model_validate(example["policy"])
        cost=compare_artifacts(left,longer,left.pricing,policy)
        quality=compare_artifacts(left,wrong,left.pricing,policy)
        self.assertEqual(cost.status,"fail")
        self.assertGreater(cost.economic.cost_per_request_usd.delta,0)
        self.assertEqual(cost.economic.quality_score.delta,0)
        self.assertEqual(quality.status,"fail")
        self.assertLess(quality.economic.cost_per_request_usd.delta,0)
        self.assertLess(quality.economic.quality_score.candidate,1)
        self.assertEqual(cost.partition_results["validation"]["scored_pairs"],4)

    def test_validation_evidence_is_withheld_from_analyst(self):
        example=triage_examples();spec=ExperimentSpec.model_validate(example["spec"])
        left=fixture_run(spec)
        saved={"id":"report","request":{"pricing":spec.pricing.model_dump(mode="json")},
               "report":compare_artifacts(left,left,left.pricing).model_dump(mode="json")}
        tools=EvidenceTools(saved,left,left,spec)
        self.assertFalse(any(case.startswith("validation-") for case in tools.overview()["case_ids"]))
        with self.assertRaisesRegex(ValueError,"withheld"):
            tools.inspect_case("validation-refund")

    def test_candidate_cloning_preserves_suite_and_expectations(self):
        example=triage_examples();source=ExperimentSpec.model_validate(example["spec"])
        baseline=fixture_run(source)
        data=CandidateInput(baseline_id=str(baseline.artifact_id),provider=source.provider,model=source.model,
            system_prompt=example["variants"]["longer"],pricing=source.pricing,limits=source.limits)
        with patch('costguard.api.db.get_artifact',return_value=baseline),patch('costguard.api.db.source_experiment',return_value=source),patch('costguard.api.db.create_job',return_value='job') as create:
            self.assertEqual(start_candidate(data)["job_id"],"job")
        cloned=create.call_args.args[0]
        self.assertEqual(cloned.cases,source.cases)
        self.assertEqual(suite_digest(cloned.cases),baseline.suite_digest)

    def test_fixture_analyst_is_isolated_from_real_settings(self):
        example=triage_examples();run=fixture_run(ExperimentSpec.model_validate(example["spec"]))
        saved={"request":{"baseline_id":"left","candidate_id":"right"}}
        with patch('costguard.api.db.get_artifact',return_value=run),patch('costguard.api.db.get_analyst_settings') as settings:
            analyst=analyst_for_report(saved)
        self.assertEqual(analyst["provider"],"costguard-mock")
        settings.assert_not_called()

    def test_report_and_artifact_render_evaluation_objects(self):
        from main import app
        example=triage_examples();spec=ExperimentSpec.model_validate(example["spec"])
        run=fixture_run(spec)
        saved={"id":"report","request":{"baseline_id":str(run.artifact_id),"candidate_id":str(run.artifact_id),
               "pricing":spec.pricing.model_dump(mode="json")},
               "report":compare_artifacts(run,run,run.pricing).model_dump(mode="json")}
        with patch('costguard.web.db.get_comparison',return_value=saved),patch('costguard.web.db.get_artifact',return_value=run),TestClient(app) as client:
            page=client.get('/reports/report')
            self.assertEqual(page.status_code,200)
            self.assertIn('Inspect paired outputs and checks',page.text)
            self.assertIn('json_fields:v1',page.text)
            self.assertEqual(client.get('/artifacts/artifact').status_code,200)

    def test_reimport_normalizes_legacy_optional_defaults_without_mutating(self):
        from costguard import db
        run=artifact('legacy')
        old=run.model_dump(mode='json')
        old['schema_version']=1
        old.pop('suite_cases');old.pop('suite_digest')
        for case in old['cases']:
            for key in ('output_text','evaluation'):case.pop(key)
            for call in case['calls']:
                for key in ('response_model','finish_reason','response_text','response_text_truncated'):call.pop(key)
        imported=ExecutionArtifact.model_validate(old)
        existing=MagicMock(digest='old-schema-digest',payload=old)
        with patch('costguard.db.session_scope') as context:
            context.return_value.__enter__.return_value.get.return_value=existing
            self.assertEqual(db.import_artifact(imported),(str(run.artifact_id),False))
            context.return_value.__enter__.return_value.add.assert_not_called()


if __name__ == '__main__':
    unittest.main()
