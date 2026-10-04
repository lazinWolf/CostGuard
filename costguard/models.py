from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_encoders={Decimal: float},
    )


class ModelPrice(Schema):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_per_million_usd: Decimal = Field(ge=0)
    output_per_million_usd: Decimal = Field(ge=0)

    @property
    def key(self) -> tuple[str, str]:
        return self.provider, self.model


class PricingCatalog(Schema):
    version: str = Field(min_length=1)
    source: str | None = None
    entries: list[ModelPrice] = Field(min_length=1)

    @model_validator(mode="after")
    def prices_are_unique(self) -> "PricingCatalog":
        keys = [entry.key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("pricing entries must be unique by provider and model")
        return self

    def as_map(self) -> dict[tuple[str, str], ModelPrice]:
        return {entry.key: entry for entry in self.entries}


class ModelCall(Schema):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)

    @property
    def key(self) -> tuple[str, str]:
        return self.provider, self.model


class WorkloadRun(Schema):
    case_id: str = Field(min_length=1)
    calls: list[ModelCall] = Field(min_length=1)
    latency_ms: Decimal = Field(ge=0)
    quality_score: Decimal | None = Field(default=None, ge=0, le=1)
    additional_charges_usd: Decimal = Field(default=Decimal("0"), ge=0)


class Workload(Schema):
    name: str = Field(min_length=1)
    runs: list[WorkloadRun] = Field(min_length=1)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> "Workload":
        case_ids = [run.case_id for run in self.runs]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values must be unique within a workload")
        return self


class ComparisonPolicy(Schema):
    max_cost_increase_percent: Decimal | None = Field(default=None, ge=0)
    max_candidate_cost_per_request_usd: Decimal | None = Field(default=None, ge=0)
    max_monthly_cost_usd: Decimal | None = Field(default=None, ge=0)
    min_candidate_quality_score: Decimal | None = Field(default=None, ge=0, le=1)
    max_candidate_mean_latency_ms: Decimal | None = Field(default=None, ge=0)
    max_candidate_model_calls_per_case: Decimal | None = Field(default=None, ge=0)
    max_candidate_retries_per_case: Decimal | None = Field(default=None, ge=0)
    max_candidate_tool_steps_per_case: Decimal | None = Field(default=None, ge=0)
    max_candidate_agent_steps_per_case: Decimal | None = Field(default=None, ge=0)


class ComparisonRequest(Schema):
    baseline: Workload
    candidate: Workload
    pricing: PricingCatalog
    monthly_requests: int | None = Field(default=None, ge=0, strict=True)
    policy: ComparisonPolicy | None = None

    @model_validator(mode="after")
    def inputs_are_comparable(self) -> "ComparisonRequest":
        baseline_ids = {run.case_id for run in self.baseline.runs}
        candidate_ids = {run.case_id for run in self.candidate.runs}
        if baseline_ids != candidate_ids:
            missing = sorted(baseline_ids - candidate_ids)
            added = sorted(candidate_ids - baseline_ids)
            raise ValueError(
                f"baseline and candidate case_id values must match; "
                f"missing={missing}, added={added}"
            )

        known_prices = set(self.pricing.as_map())
        used_models = {
            call.key
            for workload in (self.baseline, self.candidate)
            for run in workload.runs
            for call in run.calls
        }
        unknown = sorted(used_models - known_prices)
        if unknown:
            names = [f"{provider}/{model}" for provider, model in unknown]
            raise ValueError(f"pricing is missing for: {', '.join(names)}")
        return self


class MetricComparison(Schema):
    baseline: Decimal
    candidate: Decimal
    delta: Decimal
    delta_percent: Decimal | None


class ModelCostComparison(Schema):
    provider: str
    model: str
    cost_per_request_usd: MetricComparison


class CaseCostComparison(Schema):
    case_id: str
    cost_usd: MetricComparison


class MonthlyProjection(Schema):
    requests: int
    cost_usd: MetricComparison


class PolicyResult(Schema):
    status: Literal["not_evaluated", "pass", "fail", "inconclusive"]
    violations: list[str]
    unknowns: list[str] = Field(default_factory=list)


class ComparisonReport(Schema):
    baseline: str
    candidate: str
    cases_compared: int
    pricing_version: str
    cost_per_request_usd: MetricComparison
    mean_latency_ms: MetricComparison
    quality_score: MetricComparison | None
    quality_cases_compared: int
    case_costs: list[CaseCostComparison]
    model_costs: list[ModelCostComparison]
    monthly_projection: MonthlyProjection | None
    policy: PolicyResult
    assumptions: list[str]
