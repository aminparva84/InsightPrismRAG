"""PrismRAG — FastAPI routes."""
from __future__ import annotations

import threading
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from prismrag.models import (
    BridgeRequest, BridgeResponse,
    JobRequest, JobResponse, JobStatus, JobStatusResponse,
    SearchRequest, SearchResponse,
    StrategyType,
)
from prismrag.pipeline.job import create_job, get_job, run_job
from prismrag.auth.auth import get_current_user
from prismrag.metering.quota import check_and_record, check_feature, metered, PLAN_LIMITS

router = APIRouter(prefix="/api/prismrag", tags=["PrismRAG"])


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
def health():
    return {"status": "ok", "service": "PrismRAG"}


# ── Intake: submit a mapping job ──────────────────────────────────────────────

@router.post("/jobs", response_model=JobResponse)
async def submit_job(
    request: JobRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Submit an ingest job.

    For small datasets (≤ SYNC_MAX_RECORDS) the job runs inline and returns
    status=completed immediately. For large datasets it queues and returns
    status=queued with a status_url to poll.
    """
    from prismrag.config import SYNC_MAX_RECORDS

    tenant_id = str(request.tenant_id)
    _ensure_tenant_exists(tenant_id)
    _check_tenant_quota(user)

    if not request.mapping or not request.mapping.rules:
        raise HTTPException(
            status_code=422,
            detail="mapping.rules is required. Provide at least one word→category rule.",
        )

    job_id = create_job(tenant_id, request)
    status_url = f"/api/prismrag/jobs/{job_id}"

    # Inline sync for small datasets
    estimated = len(request.mapping.rules)
    if estimated <= SYNC_MAX_RECORDS and request.source_type.value == "file":
        # File uploads need to be handled separately (no bytes here)
        pass

    # Default: background
    background_tasks.add_task(_run_job_bg, job_id, request, None)

    return JobResponse(
        job_id=job_id,
        tenant_id=tenant_id,
        status=JobStatus.queued,
        status_url=status_url,
        sync=False,
    )


@router.post("/jobs/upload", response_model=JobResponse)
async def submit_job_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    tenant_id: str = Form(...),
    strategy: str = Form("rules"),
    source_type: str = Form("file"),
    user: dict = Depends(get_current_user),
):
    """Submit a file-based ingest job (multipart/form-data)."""
    from prismrag.models import FileSourceConfig, MappingConfigIn, SourceType
    _ensure_tenant_exists(tenant_id)
    _check_tenant_quota(user)
    request = JobRequest(
        tenant_id=uuid.UUID(tenant_id),
        source_type=SourceType(source_type),
        strategy=strategy,
        file_config=FileSourceConfig(filename=file.filename or "upload.csv"),
        mapping=MappingConfigIn(categories=[], rules=[]),
    )

    upload_bytes = await file.read()
    job_id     = create_job(tenant_id, request)
    status_url = f"/api/prismrag/jobs/{job_id}"

    if len(upload_bytes) < 1_000_000:  # < 1 MB → sync
        try:
            run_job(job_id, request, upload_bytes)
            # Record ingest usage after completion (chunk count comes from job)
            job = get_job(job_id)
            chunks = job.get("recordsWritten", 0) if job else 0
            if chunks > 0:
                check_and_record(user, "ingest_chunk", chunks, tenant_id)
            return JobResponse(
                job_id=job_id, tenant_id=tenant_id,
                status=JobStatus.completed, status_url=status_url, sync=True,
            )
        except HTTPException:
            raise
        except Exception:
            pass

    background_tasks.add_task(_run_job_bg, job_id, request, upload_bytes, user)
    return JobResponse(
        job_id=job_id, tenant_id=tenant_id,
        status=JobStatus.queued, status_url=status_url, sync=False,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str):
    """Poll job progress."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job["jobId"],
        tenant_id=job["tenantId"],
        status=JobStatus(job["status"]),
        records_total=job.get("recordsTotal"),
        records_written=job.get("recordsWritten", 0),
        progress_pct=job.get("progressPct", 0),
        error_message=job.get("errorMessage"),
        started_at=job.get("startedAt"),
        finished_at=job.get("finishedAt"),
    )


# ── Serve: query the re-mapped chunk store ────────────────────────────────────

@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    user: dict = Depends(metered("search")),
):
    """
    Semantic search over the re-mapped chunk store.

    Uses Graph RAG retrieval (community centroid → BFS expand → MLP re-rank)
    when the graph is built, falls back to direct HNSW cosine search otherwise.
    """
    from prismrag.retrieval.search import retrieve
    result = retrieve(
        tenant_id=str(request.tenant_id),
        query=request.query,
        mapping_id=str(request.mapping_id) if request.mapping_id else None,
        top_k=request.top_k,
        category_filter=request.category_filter,
    )
    return SearchResponse(**result)


# ── AP001.2: Bridge vector injection ─────────────────────────────────────────

@router.post("/bridge", response_model=BridgeResponse)
def create_bridge(
    request: BridgeRequest,
    user: dict = Depends(metered("bridge_create", feature="bridge_vectors")),
):
    """
    Create a synthetic bridge vector between two existing communities.

    The bridge vector is computed as:
        bridge = normalize(centroid_A + centroid_B) / 2
    and stored in prismrag.bridge_vector so retrieval queries from either
    community find a path to the other.
    """
    from prismrag.retrieval.bridge import create_bridge_vector
    result = create_bridge_vector(
        tenant_id=str(request.tenant_id),
        mapping_id=str(request.mapping_id),
        community_a=request.community_a,
        community_b=request.community_b,
        label_override=request.label,
    )
    return BridgeResponse(**result)


@router.get("/bridge/{tenant_id}/{mapping_id}")
def list_bridges(tenant_id: str, mapping_id: str):
    """List all bridge vectors for a tenant/mapping."""
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, community_a, community_b, label, created_at "
            "FROM prismrag.bridge_vector WHERE tenant_id = %s AND mapping_id = %s",
            (tenant_id, mapping_id),
        )
        return [
            {"bridgeId": r[0], "communityA": r[1], "communityB": r[2],
             "label": r[3], "createdAt": r[4].isoformat() if r[4] else None}
            for r in cur.fetchall()
        ]
    finally:
        release_conn(conn)


# ── Tenant management ─────────────────────────────────────────────────────────

@router.post("/tenants")
def create_tenant(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    _check_tenant_quota(user)
    from prismrag.db import get_conn, release_conn
    name  = payload.get("name", "Untitled")
    tier  = payload.get("tier", "tier1")
    tenant_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO prismrag.tenant (id, name, owner_email, tier) VALUES (%s, %s, %s, %s)",
            (tenant_id, name, user.get("email", ""), tier),
        )
        conn.commit()
    finally:
        release_conn(conn)
    return {
        "tenant_id": tenant_id, "name": name, "tier": tier,
        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_tenant_exists(tenant_id: str) -> None:
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM prismrag.tenant WHERE id = %s", (tenant_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found")
    finally:
        release_conn(conn)


def _check_tenant_quota(user: dict) -> None:
    """Raise 403 if user has reached their max-tenants limit."""
    plan   = user.get("plan", "free")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    max_t  = limits["max_tenants"]
    if max_t < 0:  # -1 = unlimited (enterprise)
        return
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM prismrag.tenant WHERE owner_email = %s",
            (user.get("email", ""),),
        )
        count = cur.fetchone()[0]
        if count >= max_t:
            raise HTTPException(
                status_code=403,
                detail=f"Your {plan} plan allows {max_t} workspace(s). "
                       f"Upgrade to create more.",
            )
    finally:
        release_conn(conn)


def _run_job_bg(
    job_id: str,
    request: JobRequest,
    upload_bytes: bytes | None,
    user: dict | None = None,
) -> None:
    try:
        run_job(job_id, request, upload_bytes)
        if user:
            job = get_job(job_id)
            chunks = job.get("recordsWritten", 0) if job else 0
            if chunks > 0:
                check_and_record(user, "ingest_chunk", chunks, str(request.tenant_id))
    except Exception:
        pass  # already logged + written to DB inside run_job
