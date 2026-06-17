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
from prismrag.middleware.logging import AuditMiddleware
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
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware (order matters: outermost = first to run) ──────────────────────
# CORS first so OPTIONS pre-flight never hits the audit log
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Audit logger wraps every request after CORS
app.add_middleware(AuditMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(router)
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(upload_router)
app.include_router(deliberation_router)

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
