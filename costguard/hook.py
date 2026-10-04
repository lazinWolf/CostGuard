"""Small opt-in Python hook for capturing an application's own workload evidence."""

from contextlib import contextmanager
from decimal import Decimal
from time import monotonic
from typing import Any, Iterator, Literal
from uuid import uuid4

from .execution import Call, CaseExecution, Charge, ExecutionArtifact, Step
from .models import PricingCatalog


class CaseRecorder:
    def __init__(self, case_id: str):
        self.case_id = case_id
        self.calls: list[Call] = []
        self.steps: list[Step] = []
        self.charges: list[Charge] = []
        self.quality_score: Decimal | None = None
        self.evaluator: str | None = None
        self.status: Literal["ok", "error"] = "ok"
        self.error: str | None = None
        self.latency_ms = Decimal("0")

    def record_chat(self, *, provider: str, model: str, response: dict[str, Any],
                    latency_ms: Decimal, attempt: int = 1,
                    usage_source: Literal["provider", "gateway"] = "provider") -> str:
        """Record usage from an actual OpenAI-compatible chat response."""
        usage = response.get("usage") or {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        observed = (type(input_tokens) is int and type(output_tokens) is int
                    and input_tokens >= 0 and output_tokens >= 0)
        step_id = f"model-{len(self.calls) + 1}"
        self.steps.append(Step(step_id=step_id, kind="model", name=model,
                               latency_ms=latency_ms))
        self.calls.append(Call(
            call_id=str(uuid4()), step_id=step_id, provider=provider, model=model,
            input_tokens=input_tokens if observed else None,
            output_tokens=output_tokens if observed else None,
            usage_source=usage_source if observed else "missing",
            status="ok", latency_ms=latency_ms, attempt=attempt,
        ))
        return step_id

    def record_step(self, *, kind: Literal["tool", "retrieval", "agent"],
                    name: str, latency_ms: Decimal, parent_step_id: str | None = None,
                    tags: dict[str, str] | None = None) -> str:
        step_id = f"{kind}-{len(self.steps) + 1}"
        self.steps.append(Step(step_id=step_id, parent_step_id=parent_step_id,
                               kind=kind, name=name, latency_ms=latency_ms,
                               tags=tags or {}))
        return step_id

    def add_charge(self, *, name: str, amount_usd: Decimal, source: str) -> None:
        self.charges.append(Charge(name=name, amount_usd=amount_usd, source=source))

    def score(self, value: Decimal, evaluator: str) -> None:
        self.quality_score = value
        self.evaluator = evaluator

    def as_execution(self) -> CaseExecution:
        return CaseExecution(
            case_id=self.case_id, status=self.status, latency_ms=self.latency_ms,
            calls=self.calls, steps=self.steps, charges=self.charges,
            quality_score=self.quality_score if self.status == "ok" else None,
            evaluator=self.evaluator if self.status == "ok" else None,
            error=self.error,
        )


class ArtifactRecorder:
    """Collect one baseline or candidate artifact without changing the app's model client."""

    def __init__(self, *, suite_id: str, name: str, pricing: PricingCatalog,
                 configuration: dict[str, str] | None = None):
        self.suite_id = suite_id
        self.name = name
        self.pricing = pricing
        self.configuration = configuration or {}
        self.cases: list[CaseRecorder] = []

    @contextmanager
    def case(self, case_id: str) -> Iterator[CaseRecorder]:
        case = CaseRecorder(case_id)
        started = monotonic()
        try:
            yield case
        except Exception as exc:
            case.status = "error"
            case.error = f"application error: {type(exc).__name__}"
            raise
        finally:
            case.latency_ms = Decimal(str(round((monotonic() - started) * 1000, 4)))
            self.cases.append(case)

    def artifact(self) -> ExecutionArtifact:
        return ExecutionArtifact(
            artifact_id=uuid4(), suite_id=self.suite_id, name=self.name,
            configuration=self.configuration, pricing=self.pricing,
            cases=[case.as_execution() for case in self.cases],
        )
