"""Telemetry: OpenTelemetry traces and metrics for what CostGuard itself does.

Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set; until then every call here is a no-op, so callers
instrument unconditionally. Two kinds of signal:

* **Model calls** (`model_call`): one span and a few metrics per workload, judge or analyst call, with
  tokens, cost and duration. These follow the OpenTelemetry GenAI attribute names, so any backend that
  understands them (Grafana, Langfuse, Jaeger) shows them without configuration.
* **Pipeline events** (`on_event`): the structured events from `events` become counters, so a check
  created, a verdict reached or a fix measured is a metric too.

Telemetry describes CostGuard's own spend and health. It never feeds a verdict: those come from the
stored evidence alone.
"""

import os
import time
from contextlib import contextmanager
from decimal import Decimal

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

from . import events

SECONDS = [0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]  # Model calls take seconds, not the default milliseconds.


class _Instruments:
    """The tracer and every metric, built from one pair of providers."""

    def __init__(self, tracer_provider, meter_provider):
        self.tracer = tracer_provider.get_tracer("costguard")
        meter = meter_provider.get_meter("costguard")
        self.calls = meter.create_counter("costguard.model.calls", description="Model calls, by role and outcome")
        self.tokens = meter.create_counter("costguard.model.tokens", unit="{token}", description="Tokens used by model calls")
        self.spend = meter.create_counter("costguard.model.spend", unit="USD", description="Cost of model calls at the stated prices")
        self.duration = meter.create_histogram("costguard.model.duration", unit="s", description="Model call duration",
                                               explicit_bucket_boundaries_advisory=SECONDS)
        self.checks = meter.create_counter("costguard.checks", description="Checks started")
        self.runs = meter.create_counter("costguard.runs", description="Workload runs finished")
        self.comparisons = meter.create_counter("costguard.comparisons", description="Comparisons saved, by verdict")
        self.fixes = meter.create_counter("costguard.fixes", description="Fixes measured, by verdict")
        self.errors = meter.create_counter("costguard.errors", description="Failures the runner survived")


def _no_op() -> _Instruments:
    return _Instruments(trace.NoOpTracerProvider(), metrics.NoOpMeterProvider())


_active = _no_op()
_exporting = False


def configure(tracer_provider, meter_provider) -> None:
    """Send telemetry to these providers. `setup` does this for the real exporters; tests pass in-memory ones."""
    global _active
    _active = _Instruments(tracer_provider, meter_provider)


def reset() -> None:
    global _active, _exporting
    _active, _exporting = _no_op(), False


def setup(service_name: str) -> bool:
    """Start exporting over OTLP/HTTP when an endpoint is configured. Returns whether telemetry is on.

    The endpoint, headers and metric interval come from the standard OTEL_* environment variables."""
    global _exporting
    if _exporting:
        return True
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    meter_provider = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
    configure(tracer_provider, meter_provider)
    events.add_sink(on_event)
    _exporting = True
    return True


def status() -> dict:
    """Whether this process exports telemetry, and where to look at it."""
    return {"enabled": _exporting, "endpoint": os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            "dashboard_url": os.environ.get("GRAFANA_PUBLIC_URL") or None}


def _attributes(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not None}


@contextmanager
def span(name: str, **attributes):
    """A span around some work; an exception that escapes it is recorded on the span. Yields the span."""
    with _active.tracer.start_as_current_span(name, attributes=_attributes(attributes)) as current:
        yield current


class ModelCall:
    """One call to a model. Set the outcome, then `finish` (or use `model_call`, which does).

    `waited` is time spent waiting out provider rate limits; like CostGuard's latency figures, the
    duration leaves it out."""

    def __init__(self, role: str, model: str, **attributes):
        self.role, self.model = role, model
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cost_usd: Decimal | None = None
        self.error: str | None = None
        self.waited = 0.0
        self._started = time.monotonic()
        self._span = _active.tracer.start_span(f"chat {model}", attributes=_attributes({
            "gen_ai.operation.name": "chat", "gen_ai.request.model": model, "costguard.role": role, **attributes}))
        self._finished = False

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        seconds = max(time.monotonic() - self._started - self.waited, 0)
        labels = {"role": self.role, "model": self.model}
        self._span.set_attributes(_attributes({
            "gen_ai.usage.input_tokens": self.input_tokens, "gen_ai.usage.output_tokens": self.output_tokens,
            "costguard.cost_usd": None if self.cost_usd is None else float(self.cost_usd)}))
        if self.error:
            self._span.set_status(Status(StatusCode.ERROR, self.error))
        self._span.end()
        _active.calls.add(1, labels | {"outcome": "error" if self.error else "ok"})
        _active.duration.record(seconds, labels)
        for direction, count in (("input", self.input_tokens), ("output", self.output_tokens)):
            if count:
                _active.tokens.add(count, labels | {"direction": direction})
        if self.cost_usd:
            _active.spend.add(float(self.cost_usd), labels)


@contextmanager
def model_call(role: str, model: str, **attributes):
    """Telemetry for one model call: `role` is workload, judge or analyst; `model` is provider/model."""
    call = ModelCall(role, model, **attributes)
    try:
        yield call
    except BaseException as exc:
        call.error = call.error or type(exc).__name__
        raise
    finally:
        call.finish()


def on_event(event: dict) -> None:
    """The pipeline events as counters. Registered as an `events` sink by `setup`."""
    kind = event.get("event")
    if kind == "check.created":
        _active.checks.add(1, {"model": str(event.get("model"))})
    elif kind == "run.finished":
        _active.runs.add(1, {"model": str(event.get("model"))})
    elif kind == "comparison.saved":
        _active.comparisons.add(1, {"verdict": str(event.get("status"))})
    elif kind == "fix.measured":
        _active.fixes.add(1, {"verdict": str(event.get("status"))})
    elif kind in {"runner.error", "followup.error"}:
        _active.errors.add(1, {"kind": kind, "type": str(event.get("type"))})
