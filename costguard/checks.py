"""A check: a baseline and a candidate version of a workload, run on the same cases.

`CheckInput` is everything a person chooses on the New check page (or sends to the API).
The demo suite in examples/support_triage.json is the starting point the page is filled with.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .execution import CaseInput, ExecutionLimits, ExperimentSpec, Judge
from .investigation import AnalystSettings
from .models import ComparisonPolicy, PricingCatalog, Schema

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
# The starting points the New check page offers; the first is the default.
PRESETS = {"support-triage": "support_triage.json", "refund-reply": "refund_reply.json"}


def preset(name: str = "support-triage") -> dict:
    """A starting suite: both prompts, the cases, limits and reference prices."""
    return json.loads((EXAMPLES / PRESETS.get(name, PRESETS["support-triage"])).read_text())


def presets() -> list[dict]:
    return [{"name": name, "title": preset(name)["title"]} for name in PRESETS]


class CheckInput(Schema):
    model: str = Field(min_length=3, max_length=300, pattern=r"^[^/]+/.+$",
                       description="Gateway model ID in provider/model form")
    input_per_million_usd: Decimal = Field(ge=0)
    output_per_million_usd: Decimal = Field(ge=0)
    pricing_version: str = Field(default="", max_length=100)
    suite_id: str = Field(default_factory=lambda: f"check-{uuid4().hex[:8]}", min_length=1, max_length=100)
    baseline_name: str = Field(default="baseline", min_length=1, max_length=100)
    candidate_name: str = Field(default="candidate", min_length=1, max_length=100)
    baseline_prompt: str = Field(min_length=1, max_length=8000)
    candidate_prompt: str = Field(min_length=1, max_length=8000)
    cases: list[CaseInput] = Field(min_length=1, max_length=50)
    evaluator: Literal["exact", "normalized", "llm_judge"] = "normalized"
    judge: Judge | None = None
    # 0 makes repeated runs comparable: differences come from the prompt, not from sampling.
    temperature: float | None = Field(default=0, ge=0, le=2)
    limits: ExecutionLimits = Field(default_factory=lambda: ExecutionLimits(
        timeout_seconds=180, max_output_tokens=400, max_tool_calls=0))
    policy: ComparisonPolicy = Field(default_factory=ComparisonPolicy)
    monthly_requests: int = Field(default=100000, ge=0, strict=True)
    configure_analyst: bool = False

    @model_validator(mode="after")
    def can_be_checked(self) -> "CheckInput":
        if self.baseline_name == self.candidate_name:
            raise ValueError("baseline and candidate need different names")
        if (self.evaluator == "llm_judge") != (self.judge is not None):
            raise ValueError("a judge is needed exactly when answers are judged by a model")
        if self.judge is None and any(not (case.expected_text or "").strip() for case in self.cases):
            raise ValueError("every case needs an expected answer so quality can be checked")
        return self

    @property
    def route(self) -> tuple[str, str]:
        provider, name = self.model.split("/", 1)
        return provider, name

    def pricing(self) -> PricingCatalog:
        provider, name = self.route
        reference = preset()["reference_pricing"]
        return PricingCatalog(
            version=self.pricing_version or reference["version"], source=reference["source"],
            entries=[{"provider": provider, "model": name,
                      "input_per_million_usd": self.input_per_million_usd,
                      "output_per_million_usd": self.output_per_million_usd}])


def demo_check(model: str, input_per_million_usd, output_per_million_usd,
               configure_analyst: bool = False) -> CheckInput:
    """The demo suite on `model`, exactly as the New check page submits it."""
    demo = preset()
    return CheckInput(
        model=model, input_per_million_usd=input_per_million_usd, output_per_million_usd=output_per_million_usd,
        suite_id=demo["suite_id"], baseline_name=demo["baseline"]["name"],
        candidate_name=demo["candidate"]["name"], baseline_prompt=demo["baseline"]["system_prompt"],
        candidate_prompt=demo["candidate"]["system_prompt"], cases=demo["cases"],
        evaluator=demo["evaluator"], temperature=demo.get("temperature"), limits=demo["limits"],
        policy=demo["policy"], monthly_requests=demo["monthly_requests"],
        configure_analyst=configure_analyst)


def build_specs(data: CheckInput) -> tuple[ExperimentSpec, ExperimentSpec, PricingCatalog]:
    """The two experiments to run: identical except for the system prompt."""
    provider, name = data.route
    pricing = data.pricing()
    specs = [ExperimentSpec(
        suite_id=data.suite_id, name=variant_name, kind="prompt", provider=provider, model=name,
        system_prompt=prompt, evaluator=data.evaluator, temperature=data.temperature,
        cases=data.cases, pricing=pricing, limits=data.limits, judge=data.judge,
    ) for variant_name, prompt in ((data.baseline_name, data.baseline_prompt),
                                   (data.candidate_name, data.candidate_prompt))]
    return specs[0], specs[1], pricing


def analyst_from(data: CheckInput, pricing: PricingCatalog) -> AnalystSettings:
    """Use the check's own model as the analyst. Reasoning models spend output tokens thinking."""
    provider, name = data.route
    return AnalystSettings(
        provider=provider, model=name, pricing_version=pricing.version,
        input_per_million_usd=data.input_per_million_usd, output_per_million_usd=data.output_per_million_usd,
        timeout_seconds=300, max_calls=8, max_output_tokens=4096)
