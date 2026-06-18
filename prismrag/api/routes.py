"""PrismRAG — FastAPI routes."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from prismrag.models import (
    BridgeRequest, BridgeResponse,
    FileSourceConfig, JobRequest, JobResponse, JobStatus, JobStatusResponse,
    SearchRequest, SearchResponse, SearchTaskStatusResponse, SearchTaskSubmitResponse,
    SourceType, StrategyType,
)
from prismrag.pipeline.job import create_job, get_job
from prismrag.auth.auth import get_current_user, check_api_scope
from prismrag.auth.tenant import assert_job_access, assert_tenant_access
from prismrag.plans import get_plan_limits
from prismrag.metering.quota import check_and_record, check_feature, metered
from prismrag.tasks.dispatch import (
    create_search_task,
    dispatch_ingest,
    dispatch_search_task,
    get_search_task,
)
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

@router.post("/jobs", response_model=JobResponse, status_code=202)
async def submit_job(
    request: JobRequest,
    user: dict = Depends(get_current_user),
):
    """
    Submit an ingest job. Always returns immediately with status=queued.
    Poll GET /jobs/{job_id} for progress and completion.
    """
    tenant_id = str(request.tenant_id)
    check_api_scope(user, "write")
    assert_tenant_access(user, tenant_id, "write")
    _check_tenant_quota(user)
    validate_job_submit(request, user, allow_file_source=False)

    job_id = create_job(tenant_id, request)
    status_url = f"/api/v1/prismrag/jobs/{job_id}"
    dispatch_ingest(job_id, tenant_id, request, None, user)

    return JobResponse(
        job_id=job_id,
        tenant_id=tenant_id,
        status=JobStatus.queued,
        status_url=status_url,
        sync=False,
    )


@router.post("/jobs/upload", response_model=JobResponse, status_code=202)
async def submit_job_file(
    file: UploadFile = File(...),
    tenant_id: str = Form(...),
    mapping: str = Form(..., description="JSON mapping: {categories, rules}"),
    strategy: str = Form("rules"),
    word_column: str = Form("word"),
    text_column: str = Form("text"),
    category_column: str | None = Form(None),
    user: dict = Depends(get_current_user),
):
    """Submit a file-based ingest job (multipart/form-data). Always async — poll job status."""
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

    job_id = create_job(tenant_id, request)
    status_url = f"/api/v1/prismrag/jobs/{job_id}"
    dispatch_ingest(job_id, tenant_id, request, upload_bytes, user)

    return JobResponse(
        job_id=job_id,
        tenant_id=tenant_id,
        status=JobStatus.queued,
        status_url=status_url,
        sync=False,
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

@router.post("/search")
async def search(
    request: SearchRequest,
    user: dict = Depends(metered("search")),
):
    """
    Semantic search over the re-mapped chunk store.

    Default (wait=false): returns 202 + task_id — poll GET /search/tasks/{task_id}.
    Set wait=true to receive results in the response (still runs off the event loop).
    """
    from prismrag.retrieval.search import retrieve
    from prismrag.audit.results import log_search_result

    assert_tenant_access(user, str(request.tenant_id), "read")
    check_feature(user.get("plan", "free"), "graph_rag")

    request_dict = {
        "tenant_id": str(request.tenant_id),
        "query": request.query,
        "mapping_id": str(request.mapping_id) if request.mapping_id else None,
        "top_k": request.top_k,
        "category_filter": request.category_filter,
    }

    if not request.wait:
        task_id = create_search_task(user["id"], str(request.tenant_id), request_dict)
        dispatch_search_task(task_id, request_dict, user)
        body = SearchTaskSubmitResponse(
            task_id=task_id,
            tenant_id=str(request.tenant_id),
            status="pending",
            status_url=f"/api/v1/prismrag/search/tasks/{task_id}",
        )
        return JSONResponse(status_code=202, content=body.model_dump())

    t0 = time.perf_counter()
    result = await asyncio.to_thread(
        retrieve,
        tenant_id=request_dict["tenant_id"],
        query=request.query,
        mapping_id=request_dict["mapping_id"],
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


@router.get("/search/tasks/{task_id}", response_model=SearchTaskStatusResponse)
def search_task_status(task_id: str, user: dict = Depends(get_current_user)):
    """Poll async search task status and results."""
    task = get_search_task(task_id, user["id"])
    if not task:
        raise HTTPException(status_code=404, detail="Search task not found")

    result = None
    if task.get("result"):
        result = SearchResponse(**task["result"])

    return SearchTaskStatusResponse(
        task_id=task["task_id"],
        tenant_id=task["tenant_id"],
        status=task["status"],
        request=task.get("request") or {},
        result=result,
        error_message=task.get("error_message"),
        latency_ms=task.get("latency_ms"),
        created_at=task.get("created_at"),
        finished_at=task.get("finished_at"),
    )


@router.get("/communities")
async def list_communities(
    tenant_id: str,
    mapping_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List knowledge-graph communities for a workspace."""
    assert_tenant_access(user, tenant_id, "read")
    check_feature(user.get("plan", "free"), "graph_rag")

    def _query():
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

    return await asyncio.to_thread(_query)


# ── AP001.2: Bridge vector injection ─────────────────────────────────────────

@router.post("/bridge", response_model=BridgeResponse)
async def create_bridge(
    request: BridgeRequest,
    user: dict = Depends(metered("bridge_create", feature="bridge_vectors")),
):
    """Create a synthetic bridge vector between two communities."""
    from prismrag.retrieval.bridge import create_bridge_vector

    assert_tenant_access(user, str(request.tenant_id), "write")
    result = await asyncio.to_thread(
        create_bridge_vector,
        tenant_id=str(request.tenant_id),
        mapping_id=str(request.mapping_id),
        community_a=request.community_a,
        community_b=request.community_b,
        label_override=request.label,
    )
    return BridgeResponse(**result)


@router.get("/bridge/{tenant_id}/{mapping_id}")
async def list_bridges(
    tenant_id: str,
    mapping_id: str,
    user: dict = Depends(get_current_user),
):
    """List all bridge vectors for a tenant/mapping."""
    assert_tenant_access(user, tenant_id, "read")

    def _query():
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

    return await asyncio.to_thread(_query)


# ── Chunk export (data portability) ──────────────────────────────────────────

@router.get("/tenants/{tenant_id}/chunks")
async def export_chunks(
    tenant_id: str,
    mapping_id: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 1000,
    include_vectors: bool = False,
    user: dict = Depends(get_current_user),
):
    """
    Export categorised chunks for use in your own pgvector / RAG pipeline.

    Returns the text, assigned category, chunk reference, and optionally the
    256-d personal embedding vector. Each exported chunk is metered against
    your monthly export quota ($0.50 per 1,000 chunks over plan limit).

    - page / page_size: cursor-style pagination (page_size max 5,000)
    - include_vectors: set true to receive the 256-d embedding array
    - category: filter to a single category slug
    - mapping_id: defaults to tenant's active mapping

    Use the returned chunks to INSERT into your own pgvector table:
      INSERT INTO your_table (text, category, embedding)
      VALUES (%s, %s, %s::vector)
    """
    from prismrag.db import get_conn, release_conn
    from prismrag.metering.quota import check_and_record, EXPORT_INCLUDED_CHUNKS

    assert_tenant_access(user, tenant_id, "read")

    plan = user.get("plan", "free")
    if plan == "free":
        raise HTTPException(
            status_code=403,
            detail="Chunk export requires a Starter plan or higher. "
                   "Upgrade at /dashboard.html#billing",
        )

    page_size = min(page_size, 5_000)
    offset    = (max(page, 1) - 1) * page_size

    def _query():
        conn = get_conn()
        try:
            cur = conn.cursor()

            # Resolve active mapping if not specified
            mid = mapping_id
            if not mid:
                cur.execute(
                    "SELECT id FROM prismrag.mapping_version "
                    "WHERE tenant_id = %s AND status = 'active' "
                    "ORDER BY version DESC LIMIT 1",
                    (tenant_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"chunks": [], "mapping_id": None,
                            "page": page, "page_size": page_size, "total": 0, "has_more": False}
                mid = str(row[0])

            # Count total for this filter
            count_sql = """
                SELECT COUNT(*) FROM prismrag.chunk_embedding
                WHERE tenant_id = %s AND mapping_id = %s
            """
            params: list = [tenant_id, mid]
            if category:
                count_sql += " AND category_slug = %s"
                params.append(category)
            cur.execute(count_sql, params)
            total = int(cur.fetchone()[0])

            # Fetch page
            vec_col = ", embedding::text" if include_vectors else ""
            fetch_sql = f"""
                SELECT chunk_ref, chunk_text, category_slug,
                       mapping_id::text{vec_col}
                FROM prismrag.chunk_embedding
                WHERE tenant_id = %s AND mapping_id = %s
                {("AND category_slug = %s" if category else "")}
                ORDER BY chunk_ref
                LIMIT %s OFFSET %s
            """
            fetch_params = [tenant_id, mid] + ([category] if category else []) + [page_size, offset]
            cur.execute(fetch_sql, fetch_params)
            rows = cur.fetchall()

            chunks = []
            for row in rows:
                item: dict = {
                    "chunk_ref":    row[0],
                    "chunk_text":   row[1],
                    "category_slug": row[2],
                    "mapping_id":   row[3],
                }
                if include_vectors and len(row) > 4 and row[4]:
                    import json
                    item["embedding"] = json.loads(row[4])
                chunks.append(item)

            return {
                "tenant_id":  tenant_id,
                "mapping_id": mid,
                "page":       page,
                "page_size":  page_size,
                "total":      total,
                "has_more":   (offset + len(chunks)) < total,
                "chunks":     chunks,
            }
        finally:
            release_conn(conn)

    result = await asyncio.to_thread(_query)

    # Meter the export — record chunks returned this call
    exported = len(result.get("chunks", []))
    if exported > 0:
        check_and_record(user, "chunk_export", units=exported, tenant_id=tenant_id)

    return result


# ── Tenant management ─────────────────────────────────────────────────────────

@router.get("/tenants")
def list_tenants(user: dict = Depends(get_current_user)):
    """List workspaces the user can access (owner or member)."""
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT t.id, t.name, t.tier, t.data_region, t.created_at, tm.role
            FROM prismrag.tenant t
            JOIN prismrag.tenant_member tm ON tm.tenant_id = t.id
            WHERE tm.user_id = %s
            ORDER BY t.created_at DESC
            """,
            (user["id"],),
        )
        return [
            {
                "tenant_id": str(r[0]),
                "name": r[1],
                "tier": r[2],
                "data_region": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "role": r[5],
            }
            for r in cur.fetchall()
        ]
    finally:
        release_conn(conn)


@router.post("/tenants")
def create_tenant(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    _check_tenant_quota(user)
    from prismrag.db import get_conn, release_conn
    from prismrag.regions import validate_region, DEFAULT_REGION

    name  = payload.get("name", "Untitled")
    tier  = payload.get("tier", "tier1")
    data_region = payload.get("data_region", DEFAULT_REGION)
    validate_region(data_region)

    tenant_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO prismrag.tenant (id, name, owner_email, tier, data_region)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (tenant_id, name, user.get("email", ""), tier, data_region),
        )
        conn.commit()
    finally:
        release_conn(conn)

    from prismrag.auth.rbac import ensure_tenant_member
    ensure_tenant_member(tenant_id, user["id"], user.get("email", ""), "owner")

    return {
        "tenant_id": tenant_id, "name": name, "tier": tier,
        "data_region": data_region,
        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
    }


# ── Quality summary endpoints ─────────────────────────────────────────────────

@router.get("/quality/search")
def search_quality_summary(
    tenant_id: str,
    days: int = 7,
    user: dict = Depends(get_current_user),
):
    """Aggregated search quality metrics for a tenant over the last N days."""
    from prismrag.quality.metrics import search_quality_summary as _summary
    assert_tenant_access(user, tenant_id, "read")
    return _summary(tenant_id=tenant_id, days=days)


@router.get("/quality/deliberation")
def deliberation_quality_summary(
    days: int = 7,
    user: dict = Depends(get_current_user),
):
    """Aggregated deliberation quality metrics for the authenticated user."""
    from prismrag.quality.metrics import deliberation_quality_summary as _summary
    return _summary(user_id=user["id"], days=days)


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
    if max_t < 0:
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
