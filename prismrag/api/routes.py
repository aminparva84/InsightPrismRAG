"""PrismRAG — FastAPI routes."""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from prismrag.models import (
    BridgeRequest, BridgeResponse,
    FileSourceConfig, JobRequest, JobResponse, JobStatus, JobStatusResponse,
    SearchRequest, SearchResponse,
    SourceType, StrategyType,
)
from prismrag.pipeline.job import create_job, get_job, run_job
from prismrag.auth.auth import get_current_user, check_api_scope
from prismrag.auth.tenant import assert_job_access, assert_tenant_access
from prismrag.plans import get_plan_limits
from prismrag.metering.quota import check_and_record, check_feature, metered
from prismrag.validation import (
    parse_mapping_json,
    validate_file_category_hints,
    validate_file_columns,
    validate_job_submit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/prismrag", tags=["PrismRAG"])


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
    tenant_id = str(request.tenant_id)
    check_api_scope(user, "write")
    assert_tenant_access(user, tenant_id, "write")
    _check_tenant_quota(user)
    validate_job_submit(request, user, allow_file_source=False)

    job_id = create_job(tenant_id, request)
    status_url = f"/api/v1/prismrag/jobs/{job_id}"

    if _use_job_queue():
        _enqueue_job(job_id, tenant_id, request, None, user)
    else:
        background_tasks.add_task(_run_job_bg, job_id, request, None, user)

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
    mapping: str = Form(..., description="JSON mapping: {categories, rules}"),
    strategy: str = Form("rules"),
    word_column: str = Form("word"),
    text_column: str = Form("text"),
    category_column: str | None = Form(None),
    user: dict = Depends(get_current_user),
):
    """Submit a file-based ingest job (multipart/form-data)."""
    check_api_scope(user, "write")
    _ensure_tenant_exists(tenant_id)
    assert_tenant_access(user, tenant_id, "write")
    _check_tenant_quota(user)

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="tenant_id must be a valid UUID") from exc

    try:
        strategy_enum = StrategyType(strategy)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid strategy '{strategy}'. Use 'rules' or 'mlp'.",
        ) from exc

    mapping_config = parse_mapping_json(mapping)
    file_config = FileSourceConfig(
        filename=file.filename or "upload.csv",
        word_column=word_column,
        text_column=text_column,
        category_column=category_column or None,
    )

    request = JobRequest(
        tenant_id=tenant_uuid,
        source_type=SourceType.file,
        strategy=strategy_enum,
        file_config=file_config,
        mapping=mapping_config,
    )
    validate_job_submit(request, user, allow_file_source=True)

    upload_bytes = await file.read()
    if not upload_bytes:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    validate_file_columns(upload_bytes, file_config)
    validate_file_category_hints(upload_bytes, file_config, mapping_config)

    job_id     = create_job(tenant_id, request)
    status_url = f"/api/v1/prismrag/jobs/{job_id}"

    if len(upload_bytes) < 1_000_000:  # < 1 MB → sync
        try:
            run_job(
                job_id, request, upload_bytes,
                user_id=user["id"], plan=user.get("plan", "free"),
            )
            job = get_job(job_id)
            if job and job.get("status") == "failed":
                raise HTTPException(
                    status_code=422,
                    detail=job.get("errorMessage") or "Ingest job failed",
                )
            chunks = job.get("recordsWritten", 0) if job else 0
            if chunks > 0:
                check_and_record(user, "ingest_chunk", chunks, tenant_id)
            return JobResponse(
                job_id=job_id, tenant_id=tenant_id,
                status=JobStatus.completed, status_url=status_url, sync=True,
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Sync file ingest failed for job %s", job_id)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if _use_job_queue():
        _enqueue_job(job_id, tenant_id, request, upload_bytes, user)
    else:
        background_tasks.add_task(_run_job_bg, job_id, request, upload_bytes, user)
    return JobResponse(
        job_id=job_id, tenant_id=tenant_id,
        status=JobStatus.queued, status_url=status_url, sync=False,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str, user: dict = Depends(get_current_user)):
    """Poll job progress (owner only)."""
    assert_job_access(user, job_id)
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
    import time
    from prismrag.audit.results import log_search_result

    assert_tenant_access(user, str(request.tenant_id), "read")
    check_feature(user.get("plan", "free"), "graph_rag")
    t0 = time.perf_counter()
    result = retrieve(
        tenant_id=str(request.tenant_id),
        query=request.query,
        mapping_id=str(request.mapping_id) if request.mapping_id else None,
        top_k=request.top_k,
        category_filter=request.category_filter,
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    log_search_result(
        user_id=user.get("id"),
        tenant_id=str(request.tenant_id),
        mapping_id=result.get("mapping_id"),
        query_text=request.query,
        query_embedding=None,
        top_k=request.top_k,
        category_filter=request.category_filter,
        results=result,
        retrieval_mode=result.get("retrieval_mode", "direct"),
        latency_ms=latency_ms,
        plan=user.get("plan", "free"),
    )
    return SearchResponse(**result)


@router.get("/communities")
def list_communities(
    tenant_id: str,
    mapping_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List knowledge-graph communities for a workspace (owner only)."""
    assert_tenant_access(user, tenant_id, "read")
    check_feature(user.get("plan", "free"), "graph_rag")
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        query = """
            SELECT community_id, label, word_count, top_words, category_slug, mapping_id::text
            FROM prismrag.community_summary
            WHERE tenant_id = %s
        """
        params: list = [tenant_id]
        if mapping_id:
            query += " AND mapping_id = %s"
            params.append(mapping_id)
        query += " ORDER BY word_count DESC LIMIT 50"
        cur.execute(query, params)
        return [
            {
                "id": r[0],
                "label": r[1],
                "size": r[2],
                "top_words": list(r[3] or [])[:10],
                "category_slug": r[4],
                "mapping_id": r[5],
            }
            for r in cur.fetchall()
        ]
    finally:
        release_conn(conn)


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
    assert_tenant_access(user, str(request.tenant_id), "write")
    result = create_bridge_vector(
        tenant_id=str(request.tenant_id),
        mapping_id=str(request.mapping_id),
        community_a=request.community_a,
        community_b=request.community_b,
        label_override=request.label,
    )
    return BridgeResponse(**result)


@router.get("/bridge/{tenant_id}/{mapping_id}")
def list_bridges(
    tenant_id: str,
    mapping_id: str,
    user: dict = Depends(get_current_user),
):
    """List all bridge vectors for a tenant/mapping (owner only)."""
    assert_tenant_access(user, tenant_id, "read")
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

    from prismrag.auth.rbac import ensure_tenant_member
    ensure_tenant_member(tenant_id, user["id"], user.get("email", ""), "owner")

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
    limits = get_plan_limits(plan)
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


def _use_job_queue() -> bool:
    return os.getenv("PRISMRAG_USE_JOB_QUEUE", "").lower() in ("1", "true", "yes")


def _enqueue_job(
    job_id: str,
    tenant_id: str,
    request: JobRequest,
    upload_bytes: bytes | None,
    user: dict,
) -> None:
    import base64
    from prismrag.worker.job_worker import enqueue_job

    payload = request.model_dump(mode="json")
    upload_b64 = base64.b64encode(upload_bytes).decode() if upload_bytes else None
    enqueue_job(job_id, tenant_id, payload, upload_b64, user.get("id"))


def _run_job_bg(
    job_id: str,
    request: JobRequest,
    upload_bytes: bytes | None,
    user: dict | None = None,
) -> None:
    try:
        run_job(
            job_id, request, upload_bytes,
            user_id=user.get("id") if user else None,
            plan=user.get("plan", "free") if user else "free",
        )
        if user:
            job = get_job(job_id)
            chunks = job.get("recordsWritten", 0) if job else 0
            if chunks > 0:
                check_and_record(user, "ingest_chunk", chunks, str(request.tenant_id))
    except Exception:
        pass  # already logged + written to DB inside run_job
