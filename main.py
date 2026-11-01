import os
from urllib.parse import urlparse

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from costguard import ComparisonReport, ComparisonRequest, compare_workloads, service, telemetry
from costguard.api import router
from costguard.web import router as web_router

# Local-only prototype: reject unexpected Host headers (DNS rebinding) and
# state-changing requests sent by pages from other origins.
ALLOWED_HOSTS = [host.strip() for host in
                 os.environ.get("COSTGUARD_ALLOWED_HOSTS", "localhost,127.0.0.1,app").split(",")
                 if host.strip()]

telemetry.setup("costguard-app")  # A no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set.

app = FastAPI(
    title="CostGuard API",
    description="Economic regression analysis for AI workloads",
)
app.include_router(router)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "costguard" / "static"), name="static")


# Domain errors from the service layer become HTTP statuses here; the CLI and MCP map them their own way.
for error, status in ((service.NotFound, 404), (service.Conflict, 409), (service.Unavailable, 503)):
    app.add_exception_handler(error, lambda request, exc, status=status:
                              JSONResponse({"detail": str(exc)}, status_code=status))


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if (request.method not in {"GET", "HEAD", "OPTIONS"} and origin
            and urlparse(origin).hostname not in ALLOWED_HOSTS):
        return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
    return await call_next(request)


app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)  # No icon: say so quietly instead of a 404 on every page load.


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/compare", response_model=ComparisonReport)
def compare(request: ComparisonRequest) -> ComparisonReport:
    return compare_workloads(request)
