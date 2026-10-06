"""Deterministic, OpenAI-shaped provider for demos and CI."""

import json

from fastapi import FastAPI, HTTPException

app = FastAPI(title="CostGuard mock provider")


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": model, "object": "model"} for model in ("echo", "analyst")]}


def analyst_message(messages: list) -> dict:
    """A scripted fixture for exercising tools; it does not emulate LLM reasoning."""
    outputs = [json.loads(message["content"]) for message in messages if message.get("role") == "tool"]
    overview = next((item for item in outputs if item.get("evidence_ref") == "comparison"), None)

    def call(name, arguments, index=1):
        return {"id": f"mock-{len(outputs)}-{index}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}

    if overview is None:
        return {"role": "assistant", "content": None, "tool_calls": [call("inspect_comparison", {})]}
    if len(outputs) == 1:
        calls = []
        if overview["case_ids"]:
            calls.append(call("inspect_case", {"case_id": overview["case_ids"][0]}))
        prompt = overview["configuration"]["baseline"]["system_prompt"] or "Answer accurately and briefly."
        if (overview["can_propose_prompt_experiment"]
                and prompt != overview["configuration"]["candidate"]["system_prompt"]):
            calls.append(call("propose_experiment", {"system_prompt": prompt,
                "rationale": "Measure whether returning to the baseline instructions removes additional input cost while preserving the explicit case evaluator."}, 2))
        if calls:
            return {"role": "assistant", "content": None, "tool_calls": calls}
    metrics = overview.get("metrics")
    text = (f"Measured cost per request changed by {metrics['cost_per_request_usd']['delta_percent']}%. "
            f"The calculated policy status is {overview['policy']['status']}.") if metrics else "The comparison is inconclusive; inspect the evidence notes before deciding."
    return {"role": "assistant", "content": None, "tool_calls": [call("finish_investigation", {
        "summary": "Scripted demo investigation using the supplied economic evidence.",
        "findings": [{"text": text, "evidence": ["comparison"]}],
        "hypotheses": ["A prompt change can alter input usage; the proposed run is needed to measure its impact."],
        "next_steps": ["Run the proposed experiment if available, compare it against the original baseline, and inspect paired quality."],
    })]}


@app.post("/v1/chat/completions")
def completion(body: dict):
    messages = body.get("messages", [])
    prompt = next((m.get("content", "") for m in reversed(messages)
                   if m.get("role") == "user"), "")
    if "[fail]" in prompt:
        raise HTTPException(503, "controlled failure")
    if body.get("model", "").split("/")[-1] == "analyst":
        message = analyst_message(messages)
        input_tokens = sum(len(str(m.get("content", "")).split()) for m in messages)
        return {"id": "mock-analyst-completion", "object": "chat.completion", "model": "analyst",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": 100,
                          "total_tokens": input_tokens + 100}}
    answer = prompt.split("\nWord count:", 1)[0]
    input_tokens = sum(len(str(m.get("content", "")).split()) for m in messages)
    output_tokens = len(answer.split())
    return {"id": "mock-completion", "object": "chat.completion", "model": "echo",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                      "total_tokens": input_tokens + output_tokens}}
