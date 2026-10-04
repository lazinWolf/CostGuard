"""Deterministic, OpenAI-shaped provider for demos and CI."""

from fastapi import FastAPI, HTTPException

app = FastAPI(title="CostGuard mock provider")


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "echo", "object": "model"}]}


@app.post("/v1/chat/completions")
def completion(body: dict):
    messages = body.get("messages", [])
    prompt = next((m.get("content", "") for m in reversed(messages)
                   if m.get("role") == "user"), "")
    if "[fail]" in prompt:
        raise HTTPException(503, "controlled failure")
    answer = prompt.split("\nWord count:", 1)[0]
    input_tokens = sum(len(str(m.get("content", "")).split()) for m in messages)
    output_tokens = len(answer.split())
    return {"id": "mock-completion", "object": "chat.completion", "model": "echo",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                      "total_tokens": input_tokens + output_tokens}}
