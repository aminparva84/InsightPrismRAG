"""PrismRAG — FastAPI application entry point."""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from prismrag.api.routes import router
from prismrag.api.auth_routes import auth_router
from prismrag.api.billing_routes import billing_router
from prismrag.api.upload_routes import upload_router
from prismrag.api.deliberation_routes import deliberation_router
from prismrag.api.tenant_routes import tenant_router
from prismrag.api.scim_routes import router as scim_router
from prismrag.api.status_routes import status_router
from prismrag.middleware.logging import AuditMiddleware
from prismrag.middleware.versioning import LegacyApiMiddleware
from prismrag.middleware.request_id import RequestIdMiddleware
from prismrag.middleware.metrics import MetricsMiddleware, metrics_endpoint
from prismrag.db import init_schema

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

# ── Routers (v1) ──────────────────────────────────────────────────────────────
app.include_router(router)
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(upload_router)
app.include_router(deliberation_router)
app.include_router(tenant_router)
app.include_router(scim_router)
app.include_router(status_router)

app.get("/metrics", include_in_schema=False)(metrics_endpoint)

# ── Static files (web frontend) ───────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="web/static"), name="static")
app.mount("/", StaticFiles(directory="web", html=True), name="web")


@app.on_event("startup")
async def startup():
    try:
        init_schema()
        log.info("Schema initialised")
    except Exception as exc:
        log.warning("Schema init deferred (DB not ready): %s", exc)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PRISMRAG_PORT", "8001"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
