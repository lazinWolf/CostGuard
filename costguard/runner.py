"""Sequential experiment runner. Network calls are never replayed after a lost lease."""

import json
import os
import threading
import time
from decimal import Decimal
from uuid import uuid4

import httpx

from . import db, ratelimit
from .economics import case_cost
from .execution import Call, CaseExecution, ExecutionArtifact, ExperimentSpec, Step
from .investigation_worker import execute_investigation, gateway_headers


OUTPUT_LIMIT = 2000
# Request errors worth another attempt; other 4xx responses will not change on retry.
RETRYABLE_STATUS = {408, 409, 429}


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip(" .,;:!?\"'`*_").casefold()


def _quality(answer: str, expected: str | None,
             evaluator: str = "exact") -> tuple[Decimal | None, str | None]:
    if expected is None:
        return None, None
    if evaluator == "normalized":
        passed = _normalize(answer) == _normalize(expected)
    else:
        passed = answer.strip().casefold() == expected.strip().casefold()
    return Decimal("1") if passed else Decimal("0"), f"{evaluator}:v1"


def run_case(spec: ExperimentSpec, case, client: httpx.Client,
             should_continue=lambda: True) -> CaseExecution:
    started = time.monotonic()
    rate_limited = 0.0  # Seconds spent waiting for provider rate limits, kept out of latency.
    calls: list[Call] = []
    steps: list[Step] = []
    answer = ""
    reasoning = ""
    error = None
    if spec.kind == "tool_loop":
        if spec.limits.max_tool_calls < 1 or spec.limits.max_steps < 2:
            error = "tool or step limit prevents this workload"
        else:
            steps.append(Step(step_id="tool-1", kind="tool", name="word_count",
                              latency_ms=0, tags={"words": str(len(case.prompt.split()))}))
    if error is None:
        text = case.prompt
        if spec.kind == "tool_loop":
            text += f"\nWord count: {len(case.prompt.split())}"
        if not should_continue():
            error = "cancelled"
        else:
            model_step = "model-1"
            steps.append(Step(step_id=model_step, kind="model", name=spec.model, latency_ms=0))
            for attempt in range(1, spec.limits.max_attempts + 1):
                if not should_continue():
                    error = "cancelled"
                    break
                call_started = time.monotonic()
                waits: list[float] = []
                usage_source = "missing"
                input_tokens = output_tokens = None
                status = "error"
                retryable = True
                try:
                    response = ratelimit.post(
                        client, "/v1/chat/completions",
                        json={"model": f"{spec.provider}/{spec.model}",
                              "messages": [{"role": "system", "content": spec.system_prompt},
                                           {"role": "user", "content": text}],
                              "max_tokens": spec.limits.max_output_tokens,
                              "stream": False}
                             | ({} if spec.temperature is None else {"temperature": spec.temperature}),
                        timeout=spec.limits.timeout_seconds, should_continue=should_continue,
                        waits=waits,
                    )
                    response.raise_for_status()
                    data = response.json()
                    message = data["choices"][0]["message"]
                    answer = message["content"] or ""
                    thought = message.get("reasoning_content") or message.get("reasoning")
                    reasoning = thought if isinstance(thought, str) else ""
                    usage = data.get("usage")
                    if usage and isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
                        input_tokens = usage["prompt_tokens"]
                        output_tokens = usage["completion_tokens"]
                        usage_source = "gateway"
                    status = "ok"
                    error = None
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    reason = ratelimit.error_reason(exc.response)
                    error = f"model request failed: HTTP {code}" + (f" ({reason})" if reason else "")
                    retryable = code >= 500 or code in RETRYABLE_STATUS
                except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                    error = f"model request failed: {type(exc).__name__}"
                rate_limited += sum(waits)
                elapsed = Decimal(str(round(max(time.monotonic() - call_started - sum(waits), 0) * 1000, 4)))
                calls.append(Call(
                    call_id=str(uuid4()), step_id=model_step, provider=spec.provider,
                    model=spec.model, input_tokens=input_tokens,
                    output_tokens=output_tokens, usage_source=usage_source,
                    status=status, latency_ms=elapsed, attempt=attempt,
                    tags={"rate_limit_wait_ms": str(round(sum(waits) * 1000))} if waits else {},
                ))
                if status == "ok" or not retryable:
                    break
            steps[-1].latency_ms = sum((call.latency_ms for call in calls), Decimal("0"))
            steps[-1].status = "ok" if error is None else "error"
    quality, evaluator = (_quality(answer, case.expected_text, spec.evaluator)
                          if error is None else (None, None))
    status = "cancelled" if error == "cancelled" else "error" if error else "ok"
    return CaseExecution(
        case_id=case.case_id, status=status,
        latency_ms=Decimal(str(round(max(time.monotonic() - started - rate_limited, 0) * 1000, 4))),
        calls=calls, steps=steps, quality_score=quality, evaluator=evaluator,
        error=error, output=answer[:OUTPUT_LIMIT] if answer else None,
        reasoning=reasoning[:OUTPUT_LIMIT] if reasoning else None,
    )


def _summary(case: CaseExecution, case_input, cost: Decimal | None) -> dict:
    """What a person watching the run needs: the answer, its tokens, cost and score."""
    tokens = [(call.input_tokens, call.output_tokens) for call in case.calls]
    known = all(None not in pair for pair in tokens)
    return {"case_id": case.case_id, "status": case.status, "error": case.error,
            "prompt": case_input.prompt[:300], "expected": case_input.expected_text,
            "output": (case.output or "")[:400],
            "reasoning_chars": len(case.reasoning or ""),
            "input_tokens": sum(pair[0] for pair in tokens) if known else None,
            "output_tokens": sum(pair[1] for pair in tokens) if known else None,
            "cost_usd": format(cost, "f") if cost is not None else None,
            "latency_ms": round(float(case.latency_ms)),
            "quality": None if case.quality_score is None else float(case.quality_score)}


def execute_job(job_id: str, spec: ExperimentSpec, gateway_url: str | None = None) -> str:
    url = gateway_url or os.environ.get("GATEWAY_URL", "http://bifrost:8080")
    cases: list[CaseExecution] = []
    summaries: list[dict] = []
    spent = Decimal("0")
    heartbeat_stop = threading.Event()

    def heartbeat():
        while not heartbeat_stop.wait(15):
            if (db.get_job(job_id) or {}).get("state") != "running":
                break
            db.update_job(job_id)

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        with httpx.Client(base_url=url, headers=gateway_headers()) as client:
            for input_case in spec.cases:
                job = db.get_job(job_id)
                if job is None or job["state"] == "cancelled":
                    break
                if spec.limits.stop_after_usd is not None and spent >= spec.limits.stop_after_usd:
                    db.update_job(job_id, state="interrupted", error="configured spend threshold reached")
                    break
                db.update_job(job_id, progress=len(cases))
                case = run_case(spec, input_case, client,
                                should_continue=lambda: (db.get_job(job_id) or {}).get("state") == "running")
                cases.append(case)
                amount = case_cost(case, spec.pricing)
                if amount is not None:
                    spent += amount
                summaries.append(_summary(case, input_case, amount))
                db.update_job(job_id, progress=len(cases), cases=summaries)
                if amount is None and spec.limits.stop_after_usd is not None:
                    db.update_job(job_id, state="interrupted",
                                  error="spend threshold cannot be enforced without usage")
                    break
                if case.status == "cancelled":
                    break
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
    if not cases:
        job = db.get_job(job_id)
        if job and job["state"] == "running":
            db.update_job(job_id, state="failed", error="no cases completed")
        return job_id
    artifact = ExecutionArtifact(
        artifact_id=uuid4(), suite_id=spec.suite_id, name=spec.name,
        configuration={"kind": spec.kind, "provider": spec.provider,
                       "model": spec.model, "system_prompt": spec.system_prompt,
                       "limits": spec.limits.model_dump_json()}
                      | ({} if spec.temperature is None else {"temperature": str(spec.temperature)}),
        pricing=spec.pricing, cases=cases,
    )
    artifact_id, _ = db.import_artifact(artifact)
    job = db.get_job(job_id)
    if job and job["state"] == "running":
        db.update_job(job_id, state="completed" if len(cases) == len(spec.cases) else "interrupted",
                      progress=len(cases), artifact_id=artifact_id)
    elif job:
        db.update_job(job_id, progress=len(cases), artifact_id=artifact_id)
    return job_id


def _next_job() -> bool:
    claimed = db.claim_job()
    if not claimed:
        return False
    job_id, spec = claimed
    try:
        execute_job(job_id, spec)
    except Exception as exc:
        db.update_job(job_id, state="failed", error=f"runner error: {type(exc).__name__}")
    return True


def _next_investigation() -> bool:
    investigation_id = db.claim_investigation()
    if investigation_id:
        execute_investigation(investigation_id)
    return investigation_id is not None


def work_forever() -> None:
    order = [_next_job, _next_investigation]
    while True:
        try:
            if any(step() for step in order):
                order.reverse()  # Alternate so neither queue starves the other.
                continue
        except Exception as exc:
            print(json.dumps({"event": "runner_error", "type": type(exc).__name__}), flush=True)
        time.sleep(1)


if __name__ == "__main__":
    work_forever()
