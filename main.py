"""PrismRAG — FastAPI application entry point."""
import logging
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from prismrag.api.routes import router
from prismrag.api.auth_routes import auth_router
from prismrag.api.billing_routes import billing_router
from prismrag.api.upload_routes import upload_router
from prismrag.api.deliberation_routes import deliberation_router
from prismrag.api.tenant_routes import tenant_router
from prismrag.api.scim_routes import router as scim_router
from prismrag.api.status_routes import status_router
from prismrag.api.admin_routes import router as admin_router
from prismrag.api.dashboard_routes import router as dashboard_router
from prismrag.api.playground_routes import router as playground_router
from prismrag.api.security_routes import security_router
from prismrag.api.lib_license_routes import router as lib_license_router
from prismrag.middleware.logging import AuditMiddleware
from prismrag.middleware.versioning import LegacyApiMiddleware
from prismrag.middleware.request_id import RequestIdMiddleware
from prismrag.middleware.metrics import MetricsMiddleware, metrics_endpoint
from prismrag.middleware.mfa_enforcement import MFAEnforcementMiddleware
from prismrag.middleware.rate_limit_headers import RateLimitHeadersMiddleware
from prismrag.middleware.ip_allowlist import IPAllowlistMiddleware
from prismrag.db import init_schema
from prismrag.alerting import alert_admin, ErrorSeverity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(
    title="PrismRAG",
    description=(
        "Enterprise semantic re-mapping engine. "
        "Replaces Graph RAG's statistical relationship derivation with "
        "client-defined mapping strategies — your domain expertise defines "
        "the knowledge graph, not document co-occurrence statistics."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware (order matters: last added = first to run on request) ──────────
_cors_raw = os.getenv("PRISMRAG_CORS_ORIGINS", "*").strip()
if _cors_raw == "*":
    _cors_origins = ["*"]
    _cors_credentials = False
else:
    _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    _cors_credentials = os.getenv("PRISMRAG_CORS_CREDENTIALS", "true").lower() in (
        "1", "true", "yes",
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(LegacyApiMiddleware)
app.add_middleware(MFAEnforcementMiddleware)
app.add_middleware(RateLimitHeadersMiddleware)
app.add_middleware(IPAllowlistMiddleware)

# ── Routers (v1) ──────────────────────────────────────────────────────────────
app.include_router(router)
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(upload_router)
app.include_router(deliberation_router)
app.include_router(tenant_router)
app.include_router(scim_router)
app.include_router(status_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(playground_router)
app.include_router(security_router)
app.include_router(lib_license_router)

app.get("/metrics", include_in_schema=False)(metrics_endpoint)

# ── Static files (web frontend) ───────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="web/static"), name="static")
app.mount("/", StaticFiles(directory="web", html=True), name="web")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all for any unhandled exception.
    - Returns a clean JSON error to the client (no stack traces exposed)
    - Emails all admins with full traceback + request context
    - Optionally emails the authenticated user a polite apology
    """
    ref = str(uuid.uuid4())[:8].upper()
    log.exception("Unhandled error [ref=%s] %s %s", ref, request.method, request.url.path)

    # Determine user context if available
    user_email = None
    user_name  = None
    try:
        # Request state is populated by RequestIdMiddleware / auth dependency
        user_email = getattr(request.state, "user_email", None)
        user_name  = getattr(request.state, "user_name",  None)
    except Exception:
        pass

    alert_admin(
        subject=f"Unhandled error on {request.method} {request.url.path}",
        message=str(exc),
        severity=ErrorSeverity.ERROR,
        exc=exc,
        context={
            "ref":        ref,
            "method":     request.method,
            "path":       request.url.path,
            "user_email": user_email or "unknown",
            "client_ip":  request.client.host if request.client else "unknown",
        },
    )

    if user_email:
        from prismrag.alerting import alert_client
        alert_client(
            to=user_email,
            user_name=user_name or "",
            operation=f"{request.method} {request.url.path}",
            support_ref=ref,
        )

    return JSONResponse(
        status_code=500,
        content={
            "error": "An unexpected error occurred. Our team has been notified.",
            "ref":   ref,
            "support": "prismrag@insightits.com",
        },
    )


@app.on_event("startup")
async def startup():
    try:
        init_schema()
        log.info("Schema initialised")
    except Exception as exc:
        log.warning("Schema init deferred (DB not ready): %s", exc)
        alert_admin(
            subject="Startup: schema initialisation failed",
            message=str(exc),
            severity=ErrorSeverity.CRITICAL,
            exc=exc,
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PRISMRAG_PORT", "8001"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
