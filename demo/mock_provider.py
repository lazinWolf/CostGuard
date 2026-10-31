"""Deterministic, OpenAI-shaped provider for demos and CI."""

import json

from fastapi import FastAPI, HTTPException

app = FastAPI(title="CostGuard mock provider")


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": model, "object": "model"} for model in ("echo", "analyst", "judge")]}


DIGEST_HEADER = "Evidence from CostGuard (JSON):\n"


def analyst_message(messages: list) -> dict:
    """A scripted fixture for exercising tools; it does not emulate LLM reasoning.

    It reads the evidence CostGuard put in the first user message, as a real analyst does."""
    first = next(message["content"] for message in messages if message.get("role") == "user")
    digest = json.loads(first.split(DIGEST_HEADER, 1)[1])
    outputs = [json.loads(message["content"]) for message in messages if message.get("role") == "tool"]

    def call(name, arguments, index=1):
        return {"id": f"mock-{len(outputs)}-{index}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}

    if not outputs:
        calls = []
        if digest["changed_cases"]:
            calls.append(call("inspect_case", {"case_id": digest["changed_cases"][0]["case_id"]}))
        prompt = digest["configuration"]["baseline"]["system_prompt"] or "Answer accurately and briefly."
        if (digest["can_propose_prompt_experiment"]
                and prompt != digest["configuration"]["candidate"]["system_prompt"]):
            calls.append(call("propose_fixes", {"fixes": [
                {"hypothesis": "Returning to the baseline instructions removes the extra input cost.", "system_prompt": prompt},
                {"hypothesis": "The baseline instructions plus a brevity reminder keep answers short.",
                 "system_prompt": prompt + " Be brief."}]}, 2))
        if calls:
            return {"role": "assistant", "content": None, "tool_calls": calls}
    metrics = digest.get("metrics")
    text = (f"Measured cost per request changed by {metrics['cost_per_request_usd']['delta_percent']}%. "
            f"The calculated policy status is {digest['policy']['status']}.") if metrics else "The comparison is inconclusive; inspect the evidence notes before deciding."
    return {"role": "assistant", "content": None, "tool_calls": [call("finish_investigation", {
        "summary": "Scripted demo investigation using the supplied economic evidence.",
        "findings": [{"text": text, "evidence": ["comparison"]}],
        "hypotheses": ["A prompt change can alter input usage; the proposed runs are needed to measure its impact."],
        "next_steps": ["Test the proposed fixes if available, compare them against the original baseline, and inspect paired quality."],
    })]}


@app.post("/v1/chat/completions")
def completion(body: dict):
    messages = body.get("messages", [])
    prompt = next((m.get("content", "") for m in reversed(messages)
                   if m.get("role") == "user"), "")
    if "[fail]" in prompt:
        raise HTTPException(503, "controlled failure")
    if body.get("model", "").split("/")[-1] == "judge":
        words = len(json.loads(prompt)["answer"].split())  # Passes any answer of at most 20 words.
        verdict = json.dumps({"pass": words <= 20, "reason": f"The answer has {words} words."})
        return {"id": "mock-judgement", "object": "chat.completion", "model": "judge",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": verdict}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55}}
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
