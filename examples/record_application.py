"""Instrument a tiny external ticket-triage application and export its evidence.

Run from the repository root: python -m examples.record_application --help
This example makes real HTTP calls to the supplied gateway; keys stay in the environment.
"""
import argparse
import json
import os
from decimal import Decimal
from pathlib import Path
from time import monotonic

import httpx

from costguard.examples import triage_examples
from costguard.execution import ExperimentSpec, evaluate
from costguard.hook import ArtifactRecorder
from costguard.economics import case_cost


def capture(spec: ExperimentSpec, client: httpx.Client):
    recorder = ArtifactRecorder(suite_id=spec.suite_id, name=spec.name, pricing=spec.pricing,
        suite_cases=spec.cases, configuration={"provider": spec.provider, "model": spec.model,
            "system_prompt": spec.system_prompt, "mode": spec.mode, "application": "ticket-triage-example",
            "temperature": str(spec.temperature) if spec.temperature is not None else "provider-default"})
    spent = Decimal("0")
    for case_input in spec.cases:
        if spec.limits.stop_after_usd is not None and spent >= spec.limits.stop_after_usd:
            break
        try:
            with recorder.case(case_input.case_id) as case:
                started = monotonic()
                payload = {"model": f"{spec.provider}/{spec.model}", "messages": [
                    {"role": "system", "content": spec.system_prompt}, {"role": "user", "content": case_input.prompt}],
                    "max_tokens": spec.limits.max_output_tokens, "stream": False}
                if spec.temperature is not None:
                    payload["temperature"] = float(spec.temperature)
                try:
                    response = client.post("/v1/chat/completions", json=payload, timeout=spec.limits.timeout_seconds)
                    response.raise_for_status()
                    data = response.json()
                except (httpx.HTTPError, ValueError):
                    case.record_failure(provider=spec.provider, model=spec.model,
                        latency_ms=Decimal(str((monotonic()-started)*1000)))
                    raise
                case.record_chat(provider=spec.provider, model=spec.model, response=data,
                    latency_ms=Decimal(str((monotonic()-started)*1000)), usage_source="gateway")
                if case.status == "ok" and case.output_text is not None:
                    case.evaluation = evaluate(case.output_text, case_input)
                    if case.evaluation:
                        case.score(case.evaluation.score, case.evaluation.evaluator)
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            break  # Export observed failure evidence; never retry unknown charges.
        if (case.status != "ok" or any(call.usage_source == "missing" for call in case.calls)):
            break
        amount = case_cost(case.as_execution(), spec.pricing)
        if amount is None:
            break
        spent += amount
    return recorder.artifact()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", help="Experiment JSON with your provider/model and explicit pricing")
    parser.add_argument("--variant", choices=["baseline", "longer", "cheap_wrong"], default="baseline")
    parser.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", "http://localhost:8080"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    example = triage_examples()
    values = json.loads(Path(args.spec).read_text()) if args.spec else example["spec"]
    values.update(name=f"Application triage {args.variant}", system_prompt=example["variants"][args.variant])
    spec = ExperimentSpec.model_validate(values)
    headers = {"x-bf-vk": os.environ["BIFROST_VIRTUAL_KEY"]} if os.environ.get("BIFROST_VIRTUAL_KEY") else {}
    with httpx.Client(base_url=args.gateway, headers=headers) as client:
        artifact = capture(spec, client)
    Path(args.output).write_text(artifact.model_dump_json(indent=2))
    print(f"Saved {len(artifact.cases)} cases to {args.output}")
    return 0 if len(artifact.cases) == len(spec.cases) and all(case.status == "ok" for case in artifact.cases) else 2


if __name__ == "__main__":
    raise SystemExit(main())
