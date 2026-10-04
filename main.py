from fastapi import FastAPI

from costguard import ComparisonReport, ComparisonRequest, compare_workloads
from costguard.api import router
from costguard.web import router as web_router

app = FastAPI(
    title="CostGuard API",
    description="Economic regression analysis for AI workloads",
)
app.include_router(router)
app.include_router(web_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/compare", response_model=ComparisonReport)
def compare(request: ComparisonRequest) -> ComparisonReport:
    return compare_workloads(request)
