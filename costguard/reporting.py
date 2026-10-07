"""Portable human-readable summaries of deterministic economic decisions."""
import json


def comparison_markdown(saved: dict) -> str:
    report = saved["report"]
    lines = [f"# CostGuard comparison: {report['status']}", "",
             f"Baseline: {report['baseline_id']}", f"Candidate: {report['candidate_id']}", ""]
    economic = report.get("economic")
    if economic:
        cost = economic["cost_per_request_usd"]
        lines += [f"Cost/request: ${cost['baseline']} → ${cost['candidate']} ({cost['delta_percent']}%)",
                  f"Scored pairs: {economic['quality_cases_compared']} / {economic['cases_compared']}", ""]
        lines += ["Assumptions:"] + [f"- {note}" for note in economic["assumptions"]]
    lines += ["", "Evidence and policy:"] + [f"- {note}" for note in
        report.get("reasons", []) + report["policy"]["violations"] + report["policy"]["unknowns"]]
    lines += ["", "Observed attribution (USD/request; retry_subset overlaps input/output):",
              "```json", json.dumps(report.get("attribution", {}), indent=2), "```",
              "", "Pricing and policy snapshot:", "```json", json.dumps(saved.get("request", {}), indent=2), "```",
              "", "Only client-visible calls are recorded; gateway-internal retries/routing are not captured."]
    return "\n".join(lines) + "\n"
