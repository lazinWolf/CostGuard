from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="CostGuard API", description="LLMOps CI/CD Cost Estimation Gate")

# We define a strict data schema using Pydantic for input validation
class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    model: str = "gpt-4"

@app.post("/estimate")
async def estimate_cost(usage: TokenUsage):
    # Standard GPT-4 pricing: $0.03 per 1K prompt, $0.06 per 1K completion
    prompt_cost = (usage.prompt_tokens / 1000) * 0.03
    completion_cost = (usage.completion_tokens / 1000) * 0.06
    total_cost = prompt_cost + completion_cost
    
    return {
        "model": usage.model,
        "total_cost_usd": round(total_cost, 4),
        "status": "approved" if total_cost < 1.0 else "rejected"
    }