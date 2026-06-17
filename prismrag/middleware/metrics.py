"""PrismRAG — Prometheus metrics."""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    _PROM = True
except ImportError:
    _PROM = False

if _PROM:
    HTTP_REQUESTS = Counter(
        "prismrag_http_requests_total",
        "Total HTTP requests",
        ["method", "path_template", "status"],
    )
    HTTP_LATENCY = Histogram(
        "prismrag_http_request_duration_seconds",
        "HTTP request latency",
        ["method", "path_template"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    )
    JOBS_COMPLETED = Counter(
        "prismrag_jobs_completed_total",
        "Ingest jobs completed",
        ["status"],
    )


def _path_template(path: str) -> str:
    """Collapse UUIDs and numeric IDs for low-cardinality labels."""
    parts = path.split("/")
    out = []
    for p in parts:
        if len(p) == 36 and p.count("-") == 4:
            out.append("{id}")
        elif p.isdigit():
            out.append("{id}")
        else:
            out.append(p)
    return "/".join(out)


class MetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        if not _PROM:
            return await call_next(request)
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - t0
        template = _path_template(request.url.path)
        status = str(response.status_code)
        HTTP_REQUESTS.labels(method=method, path_template=template, status=status).inc()
        HTTP_LATENCY.labels(method=method, path_template=template).observe(elapsed)
        return response


def metrics_endpoint():
    """FastAPI route handler for GET /metrics."""
    if not _PROM:
        return "prometheus_client not installed", 503
    from fastapi import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def record_job_completion(status: str) -> None:
    if _PROM:
        JOBS_COMPLETED.labels(status=status).inc()
