"""Read-only MCP access to CostGuard's saved economic evidence."""

import json

from mcp.server import MCPServer

from . import db
from .economics import compare_artifacts
from .models import ComparisonPolicy, PricingCatalog

mcp = MCPServer("CostGuard")


@mcp.tool()
def list_investigations() -> list[dict]:
    """List model-led investigations without initiating model calls."""
    return db.list_investigations()


@mcp.tool()
def get_investigation(investigation_id: str) -> dict:
    """Read findings, evidence-tool activity, proposals and separately recorded analyst cost."""
    result = db.get_investigation(investigation_id)
    if result is None:
        raise ValueError("investigation not found")
    return result


@mcp.tool()
def list_runs() -> list[dict]:
    """List saved workload execution artifacts."""
    return db.list_artifacts()


@mcp.tool()
def get_run(artifact_id: str) -> dict:
    """Inspect cases, model calls, usage, steps and configuration in a saved run."""
    artifact = db.get_artifact(artifact_id)
    if artifact is None:
        raise ValueError("run not found")
    return json.loads(artifact.model_dump_json())


@mcp.tool()
def list_reports() -> list[dict]:
    """List saved economic comparison reports."""
    return db.list_comparisons()


@mcp.tool()
def get_report(report_id: str) -> dict:
    """Read a saved comparison, including hotspots and policy evidence."""
    report = db.get_comparison(report_id)
    if report is None:
        raise ValueError("report not found")
    return report


@mcp.tool()
def compare_saved_runs(baseline_id: str, candidate_id: str, pricing: dict,
                       policy: dict | None = None, monthly_requests: int | None = None) -> dict:
    """Compare two saved runs without persisting or executing anything."""
    left = db.get_artifact(baseline_id)
    right = db.get_artifact(candidate_id)
    if left is None or right is None:
        raise ValueError("run not found")
    result = compare_artifacts(left, right, PricingCatalog.model_validate(pricing),
        ComparisonPolicy.model_validate(policy) if policy is not None else None,
        monthly_requests)
    return json.loads(result.model_dump_json())


@mcp.tool()
def inspect_hotspots(report_id: str) -> list[str]:
    """Show the largest observed cost increases in a saved comparison."""
    saved = db.get_comparison(report_id)
    if saved is None:
        raise ValueError("report not found")
    return saved["report"].get("contributors", [])


@mcp.tool()
def reprice_saved_run(artifact_id: str, pricing: dict, monthly_requests: int) -> dict:
    """Calculate a hypothetical price/traffic scenario without model calls."""
    from .api import scenario, ScenarioInput
    result = scenario(ScenarioInput(artifact_id=artifact_id,
        pricing=PricingCatalog.model_validate(pricing), monthly_requests=monthly_requests))
    return json.loads(json.dumps(result, default=lambda value: value.model_dump(mode="json")))


if __name__ == "__main__":
    mcp.run()
