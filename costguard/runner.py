"""Sequential runner with durable receipts and no replay of unobserved paid calls."""

import json
import os
import threading
import time
from decimal import Decimal
from uuid import uuid4

import httpx

from . import db
from .economics import case_cost
from .execution import Call, CaseExecution, ExecutionArtifact, ExperimentSpec, Step, evaluate
from .investigation_worker import execute_investigation, gateway_headers


def run_case(spec: ExperimentSpec, case, client: httpx.Client,
             should_continue=lambda: True, checkpoint=lambda case: None) -> CaseExecution:
    started = time.monotonic()
    result = CaseExecution(case_id=case.case_id, status="error", latency_ms=0)
    if spec.kind == "tool_loop":
        if spec.limits.max_tool_calls < 1 or spec.limits.max_steps < 2:
            result.error = "tool or step limit prevents this workload"
            checkpoint(result)
            return result
        result.steps.append(Step(step_id="tool-1", kind="tool", name="word_count", latency_ms=0,
                                 tags={"words": str(len(case.prompt.split()))}))
    text = case.prompt
    if spec.kind == "tool_loop":
        text += f"\nWord count: {len(case.prompt.split())}"
    if not should_continue():
        result.status, result.error = "cancelled", "cancelled before model call"
        checkpoint(result)
        return result
    result.steps.append(Step(step_id="model-1", kind="model", name=spec.model, latency_ms=0, status="error"))
    call = Call(call_id=str(uuid4()), step_id="model-1", provider=spec.provider, model=spec.model,
                usage_source="missing", status="in_flight", latency_ms=0, attempt=1)
    result.calls.append(call)
    result.error = "Model call is in flight; usage and charge are unknown"
    checkpoint(result)
    payload = {"model": f"{spec.provider}/{spec.model}", "messages": [
        {"role": "system", "content": spec.system_prompt}, {"role": "user", "content": text}],
        "max_tokens": spec.limits.max_output_tokens, "stream": False}
    if spec.temperature is not None:
        payload["temperature"] = float(spec.temperature)
    call_started = time.monotonic()
    try:
        response = client.post("/v1/chat/completions", json=payload, timeout=spec.limits.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") or {}
        if isinstance(usage, dict):
            counts = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if all(type(count) is int and count >= 0 for count in counts):
                call.input_tokens, call.output_tokens = counts
                call.usage_source = "gateway"
        call.response_model = data.get("model") if isinstance(data.get("model"), str) else None
        choice = data["choices"][0]
        answer = choice["message"]["content"]
        if not isinstance(answer, str):
            raise ValueError("model returned no text answer")
        call.finish_reason = choice.get("finish_reason")
        call.response_text, call.response_text_truncated = answer[:20000], len(answer) > 20000
        result.output_text = call.response_text
        if call.finish_reason not in {"stop", "length"}:
            raise ValueError("model did not return a normal text completion")
        if call.finish_reason == "length" or call.response_text_truncated:
            raise ValueError("output was truncated; increase the output-token limit")
        call.status = "ok"
        result.status, result.error = "ok", None
        result.evaluation = evaluate(answer, case)
        if result.evaluation:
            result.quality_score, result.evaluator = result.evaluation.score, result.evaluation.evaluator
    except (httpx.HTTPError, KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        call.status = "error"
        result.error = ("output was truncated; increase output-token limit" if call.finish_reason == "length"
                        else f"model request/output failed: {type(exc).__name__}; no automatic retry")
    call.latency_ms = Decimal(str(round((time.monotonic() - call_started) * 1000, 4)))
    result.steps[-1].latency_ms = call.latency_ms
    result.steps[-1].status = "ok" if call.status == "ok" else "error"
    result.latency_ms = Decimal(str(round((time.monotonic() - started) * 1000, 4)))
    if not should_continue():
        result.status, result.error = "cancelled", "cancelled after current call; usage retained"
    checkpoint(result)
    return result


def execute_job(job_id: str, spec: ExperimentSpec, gateway_url: str | None = None) -> str:
    url = gateway_url or os.environ.get("GATEWAY_URL", "http://bifrost:8080")
    cases = []
    spent = Decimal("0")
    artifact_id = uuid4()
    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(15):
            if (db.get_job(job_id) or {}).get("state") != "running":
                break
            db.update_job(job_id)

    def save_case(active):
        artifact = ExecutionArtifact(schema_version=2, artifact_id=artifact_id, suite_id=spec.suite_id,
            name=spec.name, pricing=spec.pricing, suite_cases=spec.cases, cases=[*cases, active],
            configuration={"kind": spec.kind, "provider": spec.provider, "model": spec.model,
                "system_prompt": spec.system_prompt, "limits": spec.limits.model_dump_json(),
                "mode": spec.mode,
                "temperature": str(spec.temperature) if spec.temperature is not None else "provider-default"})
        completed = bool(active.calls and active.calls[-1].status != "in_flight")
        if not db.checkpoint_job(job_id, artifact, len(cases) + int(completed)):
            raise RuntimeError("job is no longer eligible for execution")

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    state, error = "completed", None
    try:
        with httpx.Client(base_url=url, headers=gateway_headers()) as client:
            for case_input in spec.cases:
                if (db.get_job(job_id) or {}).get("state") != "running":
                    state = "interrupted"
                    break
                if spec.limits.stop_after_usd is not None and spent >= spec.limits.stop_after_usd:
                    state, error = "interrupted", "configured spend threshold reached between cases"
                    break
                case = run_case(spec, case_input, client, checkpoint=save_case,
                    should_continue=lambda: (db.get_job(job_id) or {}).get("state") == "running")
                cases.append(case)
                amount = case_cost(case, spec.pricing)
                if amount is None:
                    state, error = "interrupted", "Usage or pricing is unknown; stopped before additional calls"
                    break
                spent += amount
                if case.status != "ok":
                    state, error = "failed", case.error
                    break
    except Exception:
        db.finalize_job(job_id, "failed", "Runner failed; any saved receipts are retained and not replayed")
        raise
    finally:
        heartbeat_stop.set()
        thread.join(timeout=1)
    if not cases and state == "completed":
        state, error = "failed", "no cases completed"
    db.finalize_job(job_id, state, error)
    return job_id


def work_forever() -> None:
    while True:
        try:
            claimed = db.claim_job()
            if claimed:
                job_id, spec = claimed
                try:
                    execute_job(job_id, spec)
                except Exception as exc:
                    db.finalize_job(job_id, "failed", f"runner error: {type(exc).__name__}")
                continue
            investigation_id = db.claim_investigation()
            if investigation_id:
                execute_investigation(investigation_id)
                continue
        except Exception as exc:
            print(json.dumps({"event": "runner_error", "type": type(exc).__name__}), flush=True)
        time.sleep(1)


if __name__ == "__main__":
    work_forever()
