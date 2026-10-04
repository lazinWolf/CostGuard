"""Versioned observations shared by imports, the runner and comparisons."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .models import PricingCatalog, Schema


class Step(Schema):
    step_id: str = Field(min_length=1)
    parent_step_id: str | None = None
    kind: Literal["model", "tool", "retrieval", "agent"]
    name: str = Field(min_length=1)
    status: Literal["ok", "error", "skipped"] = "ok"
    latency_ms: Decimal = Field(ge=0)
    tags: dict[str, str] = Field(default_factory=dict)


class Call(Schema):
    call_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    usage_source: Literal["provider", "gateway", "estimated", "missing"]
    status: Literal["ok", "error"]
    latency_ms: Decimal = Field(ge=0)
    attempt: int = Field(ge=1, strict=True)
    operation: str = "chat"
    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def usage_is_explicit(self) -> "Call":
        if self.usage_source == "missing" and (
            self.input_tokens is not None or self.output_tokens is not None
        ):
            raise ValueError("missing usage must not include token values")
        if self.usage_source != "missing" and (
            self.input_tokens is None or self.output_tokens is None
        ):
            raise ValueError("observed or estimated usage needs both token counts")
        return self


class Charge(Schema):
    name: str = Field(min_length=1)
    amount_usd: Decimal = Field(ge=0)
    source: str = Field(min_length=1)


class CaseExecution(Schema):
    case_id: str = Field(min_length=1)
    status: Literal["ok", "error", "cancelled"]
    latency_ms: Decimal = Field(ge=0)
    calls: list[Call] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    charges: list[Charge] = Field(default_factory=list)
    quality_score: Decimal | None = Field(default=None, ge=0, le=1)
    evaluator: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def structure_is_valid(self) -> "CaseExecution":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id must be unique within a case")
        if len({call.call_id for call in self.calls}) != len(self.calls):
            raise ValueError("call_id must be unique within a case")
        for step in self.steps:
            if step.parent_step_id is not None and step.parent_step_id not in step_ids:
                raise ValueError("parent_step_id must refer to a step in the case")
        for call in self.calls:
            if call.step_id not in step_ids:
                raise ValueError("call step_id must refer to a step in the case")
        if self.quality_score is not None and not self.evaluator:
            raise ValueError("scored cases require an evaluator identifier")
        return self


class ExecutionArtifact(Schema):
    schema_version: Literal[1] = 1
    artifact_id: UUID
    suite_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    configuration: dict[str, str] = Field(default_factory=dict)
    pricing: PricingCatalog
    cases: list[CaseExecution] = Field(min_length=1)

    @model_validator(mode="after")
    def cases_are_unique(self) -> "ExecutionArtifact":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id must be unique in an artifact")
        return self


class ExecutionLimits(Schema):
    timeout_seconds: int = Field(default=30, ge=1, le=300, strict=True)
    max_output_tokens: int = Field(default=256, ge=1, le=8192, strict=True)
    max_attempts: int = Field(default=1, ge=1, le=3, strict=True)
    max_tool_calls: int = Field(default=2, ge=0, le=10, strict=True)
    max_steps: int = Field(default=4, ge=1, le=20, strict=True)
    stop_after_usd: Decimal | None = Field(default=None, gt=0)


class CaseInput(Schema):
    case_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    expected_text: str | None = None


class ExperimentSpec(Schema):
    suite_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["prompt", "tool_loop"] = "prompt"
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system_prompt: str = "Answer the user request accurately and briefly."
    cases: list[CaseInput] = Field(min_length=1)
    pricing: PricingCatalog
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)

    @model_validator(mode="after")
    def valid_suite(self) -> "ExperimentSpec":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id must be unique in a suite")
        if (self.provider, self.model) not in self.pricing.as_map():
            raise ValueError("pricing is missing for the selected provider/model")
        return self
