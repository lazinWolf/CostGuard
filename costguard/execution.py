"""Versioned observations shared by imports, the runner and comparisons."""

import hashlib
import json
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .models import PricingCatalog, Schema


class CaseInput(Schema):
    case_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20000)
    expected_text: str | None = None
    expected_json: dict[str, str] | None = None
    partition: Literal["exploration", "validation"] = "exploration"

    @model_validator(mode="after")
    def one_evaluator(self):
        if self.expected_text is not None and self.expected_json is not None:
            raise ValueError("choose either expected_text or expected_json")
        if self.expected_json == {}:
            raise ValueError("expected_json needs at least one required field")
        return self


def suite_digest(cases: list[CaseInput]) -> str:
    """Identity includes task inputs, expectations and validation partitions, not the variant."""
    payload = [case.model_dump(mode="json") for case in sorted(cases, key=lambda case: case.case_id)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


class EvaluationResult(Schema):
    evaluator: str
    score: Decimal = Field(ge=0, le=1)
    checks: dict[str, bool] = Field(min_length=1)


def evaluate(answer: str, case: CaseInput) -> EvaluationResult | None:
    if case.expected_json is not None:
        try:
            output = json.loads(answer)
        except (ValueError, TypeError):
            output = None
        checks = {"json_object": isinstance(output, dict)}
        checks["required_fields_only"] = isinstance(output, dict) and set(output) == set(case.expected_json)
        for key, expected in case.expected_json.items():
            checks[f"field:{key}"] = isinstance(output, dict) and output.get(key) == expected
        return EvaluationResult(evaluator="json_fields:v1", score=int(all(checks.values())), checks=checks)
    if case.expected_text is not None:
        passed = answer.strip().casefold() == case.expected_text.strip().casefold()
        return EvaluationResult(evaluator="exact:v1", score=int(passed), checks={"exact_text": passed})
    return None


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
    status: Literal["ok", "error", "in_flight"]
    latency_ms: Decimal = Field(ge=0)
    attempt: int = Field(ge=1, strict=True)
    operation: str = "chat"
    tags: dict[str, str] = Field(default_factory=dict)
    response_model: str | None = None
    finish_reason: str | None = None
    response_text: str | None = None
    response_text_truncated: bool = False

    @model_validator(mode="after")
    def usage_is_explicit(self) -> "Call":
        if self.status == "in_flight" and self.usage_source != "missing":
            raise ValueError("in-flight calls must have unknown usage")
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
    output_text: str | None = None
    evaluation: EvaluationResult | None = None

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
        if self.evaluation is not None and (self.quality_score != self.evaluation.score or self.evaluator != self.evaluation.evaluator):
            raise ValueError("evaluation checks and case quality must use the same score and evaluator")
        if (self.evaluation is not None and self.evaluator in {"exact:v1", "json_fields:v1"}
                and self.quality_score != int(all(self.evaluation.checks.values()))):
            raise ValueError("built-in evaluator score must agree with its checks")
        return self


class ExecutionArtifact(Schema):
    schema_version: Literal[1, 2] = 1
    artifact_id: UUID
    suite_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    configuration: dict[str, str] = Field(default_factory=dict)
    pricing: PricingCatalog
    cases: list[CaseExecution] = Field(min_length=1)
    suite_cases: list[CaseInput] | None = None
    suite_digest: str | None = None

    @model_validator(mode="after")
    def cases_are_unique(self) -> "ExecutionArtifact":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id must be unique in an artifact")
        if self.suite_cases is not None:
            manifest_ids = [case.case_id for case in self.suite_cases]
            if len(manifest_ids) != len(set(manifest_ids)) or not set(ids) <= set(manifest_ids):
                raise ValueError("suite manifest needs unique IDs and must include every executed case")
            digest = suite_digest(self.suite_cases)
            if self.suite_digest is not None and self.suite_digest != digest:
                raise ValueError("suite_digest does not match the case manifest")
            self.suite_digest = digest
        elif self.suite_digest is not None:
            raise ValueError("suite_digest requires the case manifest")
        if self.schema_version == 2 and self.suite_cases is None:
            raise ValueError("version 2 artifacts require the case manifest")
        return self


class ExecutionLimits(Schema):
    timeout_seconds: int = Field(default=30, ge=1, le=300, strict=True)
    max_output_tokens: int = Field(default=256, ge=1, le=8192, strict=True)
    max_attempts: int = Field(default=1, ge=1, le=3, strict=True)
    max_tool_calls: int = Field(default=2, ge=0, le=10, strict=True)
    max_steps: int = Field(default=4, ge=1, le=20, strict=True)
    stop_after_usd: Decimal | None = Field(default=None, gt=0)


class ExperimentSpec(Schema):
    suite_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["prompt", "tool_loop"] = "prompt"
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    system_prompt: str = Field(default="Answer the user request accurately and briefly.", max_length=8000)
    cases: list[CaseInput] = Field(min_length=1, max_length=100)
    pricing: PricingCatalog
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    temperature: Decimal | None = Field(default=None, ge=0, le=2)
    mode: Literal["live", "fixture"] = "live"

    @model_validator(mode="after")
    def valid_suite(self) -> "ExperimentSpec":
        if self.mode == "fixture" and self.provider != "costguard-mock":
            raise ValueError("fixture mode is restricted to the CostGuard mock provider")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id must be unique in a suite")
        if (self.provider, self.model) not in self.pricing.as_map():
            raise ValueError("pricing is missing for the selected provider/model")
        return self
