"""A real-model demo: one support-triage suite run with two system prompts on your own model."""

import json
from decimal import Decimal
from pathlib import Path

from pydantic import Field

from . import db
from .execution import ExperimentSpec
from .investigation import AnalystSettings
from .models import PricingCatalog, Schema

SUITE_PATH = Path(__file__).resolve().parent.parent / "examples" / "support_triage.json"


def suite() -> dict:
    return json.loads(SUITE_PATH.read_text())


class RealDemoInput(Schema):
    model: str = Field(min_length=3, max_length=300, pattern=r"^[^/]+/.+$",
                       description="Gateway model ID in provider/model form")
    input_per_million_usd: Decimal = Field(ge=0)
    output_per_million_usd: Decimal = Field(ge=0)
    pricing_version: str = Field(default="", max_length=100)
    configure_analyst: bool = True


def _route(model: str) -> tuple[str, str]:
    provider, name = model.split("/", 1)
    return provider, name


def build_specs(data: RealDemoInput) -> tuple[ExperimentSpec, ExperimentSpec, PricingCatalog]:
    definition = suite()
    provider, model = _route(data.model)
    reference = definition["reference_pricing"]
    pricing = PricingCatalog(
        version=data.pricing_version or reference["version"],
        source=reference["source"],
        entries=[{"provider": provider, "model": model,
                  "input_per_million_usd": data.input_per_million_usd,
                  "output_per_million_usd": data.output_per_million_usd}],
    )
    specs = [ExperimentSpec(
        suite_id=definition["suite_id"], name=variant["name"], kind="prompt",
        provider=provider, model=model, system_prompt=variant["system_prompt"],
        evaluator=definition["evaluator"], temperature=definition.get("temperature"),
        cases=definition["cases"],
        pricing=pricing, limits=definition["limits"],
    ) for variant in (definition["baseline"], definition["candidate"])]
    return specs[0], specs[1], pricing


def start(data: RealDemoInput) -> dict:
    """Queue both variants. The caller compares them once the runner completes the jobs."""
    baseline, candidate, pricing = build_specs(data)
    db.save_suite(baseline)
    if data.configure_analyst:
        provider, model = _route(data.model)
        db.save_analyst_settings(AnalystSettings(
            provider=provider, model=model, pricing_version=pricing.version,
            input_per_million_usd=data.input_per_million_usd,
            output_per_million_usd=data.output_per_million_usd,
            # Reasoning models (Granite, Qwen) spend output tokens thinking before each tool call.
            timeout_seconds=300, max_calls=8, max_output_tokens=4096,
        ))
    definition = suite()
    return {"baseline_job_id": db.create_job(baseline), "candidate_job_id": db.create_job(candidate),
            "pricing": pricing.model_dump(mode="json"), "policy": definition["policy"],
            "monthly_requests": definition["monthly_requests"]}
