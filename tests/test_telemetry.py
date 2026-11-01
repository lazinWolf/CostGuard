import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from costguard import telemetry


class Collecting(unittest.TestCase):
    """Telemetry goes to in-memory exporters, which the test reads back."""

    def setUp(self):
        self.spans, self.reader = InMemorySpanExporter(), InMemoryMetricReader()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(self.spans))
        telemetry.configure(tracer_provider, MeterProvider(metric_readers=[self.reader]))
        self.addCleanup(telemetry.reset)

    def points(self, name: str) -> list:
        """(attributes, value) for every data point of one metric."""
        found = []
        for resource in self.reader.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name == name:
                        found += [(dict(point.attributes), getattr(point, "value", getattr(point, "sum", None)))
                                  for point in metric.data.data_points]
        return found


class TelemetryTests(Collecting):
    def test_a_model_call_is_a_span_with_tokens_and_cost(self):
        with telemetry.model_call("analyst", "ollama/granite", **{"costguard.attempt": 2}) as call:
            call.input_tokens, call.output_tokens, call.cost_usd = 1200, 80, Decimal("0.0021")
        span, = self.spans.get_finished_spans()
        self.assertEqual(span.name, "chat ollama/granite")
        self.assertEqual(span.attributes["costguard.role"], "analyst")
        self.assertEqual(span.attributes["gen_ai.usage.input_tokens"], 1200)
        self.assertEqual(span.attributes["costguard.cost_usd"], 0.0021)
        self.assertEqual(span.attributes["costguard.attempt"], 2)

    def test_spend_and_tokens_are_counted_by_role_and_model(self):
        for role, cost in (("workload", "0.001"), ("workload", "0.002"), ("judge", "0.0005")):
            with telemetry.model_call(role, "m/x") as call:
                call.input_tokens, call.output_tokens, call.cost_usd = 10, 5, Decimal(cost)
        spend = {labels["role"]: value for labels, value in self.points("costguard.model.spend")}
        self.assertAlmostEqual(spend["workload"], 0.003)
        self.assertAlmostEqual(spend["judge"], 0.0005)
        tokens = {(labels["role"], labels["direction"]): value for labels, value in self.points("costguard.model.tokens")}
        self.assertEqual(tokens[("workload", "input")], 20)
        self.assertEqual(tokens[("workload", "output")], 10)
        self.assertEqual(sum(value for _, value in self.points("costguard.model.calls")), 3)

    def test_a_failed_call_marks_the_span_and_counts_as_an_error(self):
        with telemetry.model_call("workload", "m/x") as call:
            call.error = "HTTP 504"
        span, = self.spans.get_finished_spans()
        self.assertEqual(span.status.status_code, StatusCode.ERROR)
        self.assertEqual([labels["outcome"] for labels, _ in self.points("costguard.model.calls")], ["error"])
        self.assertEqual(self.points("costguard.model.spend"), [])  # Unknown cost is not counted as free spend.

    def test_an_exception_in_a_call_is_recorded_and_still_raised(self):
        with self.assertRaises(RuntimeError):
            with telemetry.model_call("workload", "m/x"):
                raise RuntimeError("boom")
        span, = self.spans.get_finished_spans()
        self.assertEqual(span.status.status_code, StatusCode.ERROR)

    def test_finishing_twice_counts_once(self):
        call = telemetry.ModelCall("analyst", "m/x")
        call.cost_usd = Decimal("1")
        call.finish()
        call.finish()
        self.assertEqual(self.points("costguard.model.spend")[0][1], 1)

    def test_a_call_inside_a_span_is_its_child(self):
        with telemetry.span("case one"):
            with telemetry.model_call("workload", "m/x"):
                pass
        calls = {span.name: span for span in self.spans.get_finished_spans()}
        self.assertEqual(calls["chat m/x"].parent.span_id, calls["case one"].context.span_id)

    def test_pipeline_events_become_counters(self):
        for event in ({"event": "check.created", "model": "m/x"}, {"event": "comparison.saved", "status": "fail"},
                      {"event": "comparison.saved", "status": "pass"}, {"event": "fix.measured", "status": "pass"},
                      {"event": "runner.error", "type": "OperationalError"}, {"event": "example.loaded"}):
            telemetry.on_event(event)
        self.assertEqual(self.points("costguard.checks"), [({"model": "m/x"}, 1)])
        self.assertEqual(sorted(labels["verdict"] for labels, _ in self.points("costguard.comparisons")), ["fail", "pass"])
        self.assertEqual(self.points("costguard.fixes"), [({"verdict": "pass"}, 1)])
        self.assertEqual(self.points("costguard.errors")[0][0]["type"], "OperationalError")


class InstrumentedWorkTests(Collecting):
    """The runner and the judge report their own calls; a case is one span with its calls beneath it."""

    def test_a_judged_case_reports_a_workload_call_and_a_judge_call_under_one_case_span(self):
        import json
        import httpx
        from costguard.execution import ExperimentSpec, Judge
        from costguard.runner import run_case
        from tests.test_execution import artifact

        judge = Judge(provider="test", model="judge", rubric="Short.", input_per_million_usd=1, output_per_million_usd=1)
        spec = ExperimentSpec(suite_id="s", name="n", provider="test", model="echo", pricing=artifact("x").pricing,
                              evaluator="llm_judge", judge=judge, cases=[{"case_id": "a", "prompt": "hi"}])

        def handle(request):
            content = '{"pass": true, "reason": "ok"}' if json.loads(request.content)["model"] == "test/judge" else "Hello"
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        with httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(handle)) as client:
            with telemetry.span("case a"):
                run_case(spec, spec.cases[0], client)
        spans = {span.name: span for span in self.spans.get_finished_spans()}
        roles = {span.attributes["costguard.role"] for name, span in spans.items() if name.startswith("chat ")}
        self.assertEqual(roles, {"workload", "judge"})
        self.assertTrue(all(span.parent.span_id == spans["case a"].context.span_id
                            for name, span in spans.items() if name.startswith("chat ")))
        self.assertEqual({labels["role"] for labels, _ in self.points("costguard.model.spend")}, {"workload", "judge"})


class SetupTests(unittest.TestCase):
    def test_nothing_is_exported_without_an_endpoint(self):
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": ""}):
            self.assertFalse(telemetry.setup("costguard-test"))
        self.assertFalse(telemetry.status()["enabled"])

    def test_calls_are_harmless_when_telemetry_is_off(self):
        telemetry.reset()
        with telemetry.span("anything"):
            with telemetry.model_call("workload", "m/x") as call:
                call.cost_usd = Decimal("1")
        telemetry.on_event({"event": "comparison.saved", "status": "fail"})


if __name__ == "__main__":
    unittest.main()
