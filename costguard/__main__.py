"""Local and CI interface to the same persisted and stateless analysis engine."""

import argparse
import json
import sys
import time
from pathlib import Path

from . import db
from .economics import compare_artifacts
from .execution import ExecutionArtifact, ExperimentSpec
from .models import ComparisonPolicy, PricingCatalog


def _read(path: str):
    return json.loads(Path(path).read_text())


def _write(path: str | None, content: str):
    if path:
        Path(path).write_text(content)
    else:
        print(content)


def _markdown(report) -> str:
    from .reporting import comparison_markdown
    return comparison_markdown({"report": report.model_dump(mode="json"), "request": {
        "pricing": report.pricing_snapshot.model_dump(mode="json") if report.pricing_snapshot else None,
        "policy": report.policy_snapshot.model_dump(mode="json") if report.policy_snapshot else None,
        "monthly_requests": report.monthly_requests}})


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
        elif args.command == "scenario":
            from .api import scenario, ScenarioInput
            result = scenario(ScenarioInput(artifact_id=args.artifact_id,
                pricing=PricingCatalog.model_validate(_read(args.pricing)),
                monthly_requests=args.monthly_requests))
            print(json.dumps(result, default=lambda value: value.model_dump(mode="json")))
    except Exception as exc:
        print(f"CostGuard: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
