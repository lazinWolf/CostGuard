"""Local and CI interface to the same persisted and stateless analysis engine."""

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

from . import db, real_demo
from .economics import ComparisonDetails, compare_artifacts, reprice
from .execution import ExecutionArtifact, ExperimentSpec
from .models import ComparisonPolicy, PricingCatalog


def _read(path: str):
    return json.loads(Path(path).read_text())


def _write(path: str | None, content: str):
    if path:
        Path(path).write_text(content)
    else:
        print(content)


VERDICTS = {"pass": "✅ PASS: within every limit", "fail": "❌ FAIL: blocked by policy",
            "inconclusive": "⚠️ INCONCLUSIVE: evidence is incomplete", "not_evaluated": "No policy set"}


def _plain(value, places: int | None = None) -> str:
    value = Decimal(value) if places is None else round(Decimal(value), places)
    return format(value.normalize() if places is None else value, "f")


def _change(metric) -> str:
    if metric.delta_percent is None:
        return "n/a"
    return f"{'+' if metric.delta_percent > 0 else ''}{_plain(metric.delta_percent)}%"


def _markdown(report) -> str:
    """A CI-friendly summary, e.g. for a GitHub Actions job summary or a PR comment."""
    economic = report.economic
    title = f"{economic.candidate} vs {economic.baseline}" if economic else "CostGuard comparison"
    lines = [f"### {title}", "", f"**{VERDICTS.get(report.status, report.status)}**", ""]
    if economic:
        rows = [("Cost per request (USD)", economic.cost_per_request_usd, None),
                ("Mean latency (ms)", economic.mean_latency_ms, 0)]
        if economic.monthly_projection:
            rows.insert(1, (f"Monthly cost at {economic.monthly_projection.requests:,} requests (USD)",
                            economic.monthly_projection.cost_usd, 2))
        if economic.quality_score:
            rows.append((f"Quality ({economic.quality_cases_compared} paired cases)", economic.quality_score, None))
        lines += ["| Metric | Baseline | Candidate | Change |", "| --- | ---: | ---: | ---: |"]
        lines += [f"| {name} | {_plain(metric.baseline, places)} | {_plain(metric.candidate, places)} | {_change(metric)} |"
                  for name, metric, places in rows]
        lines += ["", f"Priced with `{economic.pricing_version}` on {economic.cases_compared} matched cases.", ""]
    if report.contributors:
        lines += ["Largest observed increases:"] + [f"- {item}" for item in report.contributors] + [""]
    if report.reasons or report.policy.violations or report.policy.unknowns:
        lines += ["Evidence and policy:"] + [f"- {item}" for item in
            dict.fromkeys(report.reasons + report.policy.violations + report.policy.unknowns)]
    return "\n".join(lines).rstrip() + "\n"


def _wait(getter, item_id: str, label: str) -> dict:
    shown = None
    while True:
        item = getter(item_id)
        line = f"{label}: {item['state']}" + (f" {item['progress']}/{item['total']}" if "total" in item else "")
        if line != shown:
            print(line, file=sys.stderr, flush=True)
            shown = line
        if item["state"] not in {"queued", "running"}:
            return item
        time.sleep(2)


def _demo(args) -> int:
    from .api import ComparisonInput, InvestigationInput, compare_saved, start_investigation
    reference = real_demo.suite()["reference_pricing"]
    started = real_demo.start(real_demo.RealDemoInput(
        model=args.model,
        input_per_million_usd=(reference["input_per_million_usd"] if args.input_price is None
                               else args.input_price),
        output_per_million_usd=(reference["output_per_million_usd"] if args.output_price is None
                                else args.output_price),
        configure_analyst=not args.keep_analyst,
    ))
    artifacts = {}
    for side in ("baseline", "candidate"):
        job = _wait(db.get_job, started[f"{side}_job_id"], f"{side} run")
        if job["state"] != "completed":
            raise ValueError(f"{side} run ended {job['state']}: {job['error']}")
        artifacts[side] = job["artifact_id"]
    saved = compare_saved(ComparisonInput(
        baseline_id=artifacts["baseline"], candidate_id=artifacts["candidate"],
        pricing=started["pricing"], policy=started["policy"],
        monthly_requests=started["monthly_requests"]))
    print(_markdown(ComparisonDetails.model_validate(saved["report"])))
    print(f"Report: http://localhost:8000/reports/{saved['id']}", flush=True)
    if args.investigate:
        investigation_id = start_investigation(InvestigationInput(
            report_id=saved["id"], question=args.question))["investigation_id"]
        result = _wait(db.get_investigation, investigation_id, "investigation")
        conclusion = result["result"].get("conclusion")
        if conclusion:
            print(f"\n## Analyst findings\n\n{conclusion['summary']}")
            for finding in conclusion["findings"]:
                print(f"- {finding['text']} [{', '.join(finding['evidence'])}]")
        else:
            print(f"\nInvestigation {result['state']}: {result['error']}")
        print(f"Investigation: http://localhost:8000/investigations/{investigation_id}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m costguard", description="CostGuard economic experiments")
    sub = p.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="Persist a versioned execution artifact")
    imp.add_argument("artifact")
    run = sub.add_parser("run", help="Queue an experiment")
    run.add_argument("spec")
    run.add_argument("--wait", action="store_true")
    baseline = sub.add_parser("baseline", help="Promote an existing artifact")
    baseline.add_argument("suite_id")
    baseline.add_argument("artifact_id")
    gate = sub.add_parser("gate", help="Compare artifacts and exit with a CI status")
    gate.add_argument("--baseline", required=True)
    gate.add_argument("--candidate", required=True)
    gate.add_argument("--pricing", required=True)
    gate.add_argument("--policy", required=True)
    gate.add_argument("--monthly-requests", type=int)
    gate.add_argument("--json-output")
    gate.add_argument("--markdown-output")
    report = sub.add_parser("report", help="Export a saved comparison report")
    report.add_argument("report_id")
    report.add_argument("--json-output")
    demo = sub.add_parser("demo", help="Run the support-triage demo on a real gateway model")
    demo.add_argument("--model", required=True,
                      help="gateway provider/model ID, e.g. ollama/llama3.2:3b-instruct-q4_K_M")
    demo.add_argument("--input-price", type=Decimal, help="USD per million input tokens")
    demo.add_argument("--output-price", type=Decimal, help="USD per million output tokens")
    demo.add_argument("--keep-analyst", action="store_true",
                      help="keep the configured analyst instead of using this model")
    demo.add_argument("--investigate", action="store_true",
                      help="also ask the analyst to investigate the comparison")
    demo.add_argument("--question", default="Explain the cost and quality change, inspect the cases "
                      "that changed most, and propose a prompt experiment that restores quality at lower cost.")
    scenario = sub.add_parser("scenario", help="Reprice a saved artifact")
    scenario.add_argument("artifact_id")
    scenario.add_argument("--pricing", required=True)
    scenario.add_argument("--monthly-requests", type=int, required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "import":
            artifact = ExecutionArtifact.model_validate(_read(args.artifact))
            artifact_id, created = db.import_artifact(artifact)
            print(json.dumps({"artifact_id": artifact_id, "created": created}))
        elif args.command == "run":
            spec = ExperimentSpec.model_validate(_read(args.spec))
            job_id = db.create_job(spec)
            print(json.dumps({"job_id": job_id}), flush=True)
            if args.wait:
                while True:
                    job = db.get_job(job_id)
                    if job["state"] not in {"queued", "running"}:
                        print(json.dumps(job), flush=True)
                        return 0 if job["state"] == "completed" else 2
                    time.sleep(1)
        elif args.command == "baseline":
            db.set_baseline(args.suite_id, args.artifact_id)
            print(json.dumps({"suite_id": args.suite_id, "artifact_id": args.artifact_id}))
        elif args.command == "gate":
            left = ExecutionArtifact.model_validate(_read(args.baseline))
            right = ExecutionArtifact.model_validate(_read(args.candidate))
            pricing = PricingCatalog.model_validate(_read(args.pricing))
            policy = ComparisonPolicy.model_validate(_read(args.policy))
            if not any(value is not None for value in policy.model_dump().values()):
                raise ValueError("gate requires at least one policy threshold")
            report = compare_artifacts(left, right, pricing, policy, args.monthly_requests)
            payload = report.model_dump_json(indent=2)
            _write(args.json_output, payload)
            if args.markdown_output:
                _write(args.markdown_output, _markdown(report))
            return 0 if report.status == "pass" else 1 if report.status == "fail" else 2
        elif args.command == "report":
            result = db.get_comparison(args.report_id)
            if result is None:
                raise ValueError("report not found")
            _write(args.json_output, json.dumps(result, indent=2))
        elif args.command == "demo":
            return _demo(args)
        elif args.command == "scenario":
            artifact = db.get_artifact(args.artifact_id)
            if artifact is None:
                raise ValueError("artifact not found")
            result = reprice(artifact, PricingCatalog.model_validate(_read(args.pricing)),
                             args.monthly_requests)
            print(json.dumps(result, default=lambda value: value.model_dump(mode="json")))
    except Exception as exc:
        print(f"CostGuard: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
