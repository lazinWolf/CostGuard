import os
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from costguard import ComparisonReport, ComparisonRequest, compare_workloads
from costguard.api import router
from costguard.web import router as web_router

# Local-only prototype: reject unexpected Host headers (DNS rebinding) and
# state-changing requests sent by pages from other origins.
ALLOWED_HOSTS = [host.strip() for host in
                 os.environ.get("COSTGUARD_ALLOWED_HOSTS", "localhost,127.0.0.1,app").split(",")
                 if host.strip()]

app = FastAPI(
    title="CostGuard API",
    description="Economic regression analysis for AI workloads",
)
app.include_router(router)
app.include_router(web_router)


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if (request.method not in {"GET", "HEAD", "OPTIONS"} and origin
            and urlparse(origin).hostname not in ALLOWED_HOSTS):
        return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
    return await call_next(request)


app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/compare", response_model=ComparisonReport)
def compare(request: ComparisonRequest) -> ComparisonReport:
    return compare_workloads(request)
