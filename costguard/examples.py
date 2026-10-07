"""Bundled example data, separate from economic and evaluation logic."""
from pathlib import Path
from .execution import ExperimentSpec

ROOT = Path(__file__).resolve().parents[1]


def triage_examples() -> dict:
    baseline = ExperimentSpec.model_validate_json((ROOT / "examples/ticket_triage.json").read_text())
    longer = baseline.system_prompt + " " + (
        "Review the ticket carefully before selecting the classification. Reconsider the same category rules, "
        "and check the required JSON fields once more. Keep the category vocabulary unchanged. "
        "This repeated instruction adds context without introducing additional task requirements. "
    ) * 4
    return {"spec": baseline.model_dump(mode="json"), "variants": {
        "baseline": baseline.system_prompt,
        "longer": longer,
        "cheap_wrong": 'Always return {"category":"other","priority":"low"}.',
    }, "policy": {"max_cost_increase_percent": 10, "min_candidate_quality_score": 1}}
