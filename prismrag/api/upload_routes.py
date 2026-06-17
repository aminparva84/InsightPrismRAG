"""
PrismRAG — Large file upload routes.

Flow:
  1. POST /api/prismrag/upload/presign   → get SAS URL, upload_id
  2. Client PUTs file directly to Azure Blob Storage
  3. POST /api/prismrag/upload/confirm   → enqueue job, get job_id
  4. GET  /api/prismrag/jobs/{job_id}    → poll progress (existing route)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from prismrag.auth.auth import get_current_user
from prismrag.auth.tenant import assert_tenant_access
from prismrag.models import (
    FileSourceConfig, JobRequest, MappingConfigIn, SourceType, StrategyType,
)
from prismrag.validation import validate_job_submit

upload_router = APIRouter(prefix="/api/v1/prismrag/upload", tags=["Upload"])


class PresignRequest(BaseModel):
    tenant_id:       str
    original_name:   str
    file_size_bytes: int | None = None


class PresignResponse(BaseModel):
    upload_id:    str
    upload_url:   str
    blob_name:    str
    expires_at:   str
    max_size_bytes: int
    instructions: str


class ConfirmRequest(BaseModel):
    upload_id: str
    tenant_id: str
    strategy:  str = "rules"
    mapping:   dict = Field(
        ...,
        description="Required mapping object with categories and rules",
    )


@upload_router.post("/presign", response_model=PresignResponse)
def presign_upload(body: PresignRequest, user: dict = Depends(get_current_user)):
    """
    Step 1: Get a write-only SAS URL valid for 4 hours.
    The client PUTs the file directly to Azure Blob — never routes through the API server.
    This means even a 10 GB file doesn't touch our compute at upload time.
    """
    assert_tenant_access(user, body.tenant_id)
    from prismrag.plans import get_plan_limits

    size = body.file_size_bytes or 0
    plan_max = get_plan_limits(user["plan"]).get("max_file_bytes", 10_000_000)
    if plan_max > 0 and size > plan_max:
        raise HTTPException(
            status_code=413,
            detail=f"File too large for {user['plan']} plan "
                   f"({size:,} bytes > {plan_max:,} max). Upgrade to upload larger files.",
        )

    from prismrag.worker.large_file import generate_upload_url
    result = generate_upload_url(
        user_id=user["id"],
        tenant_id=body.tenant_id,
        original_name=body.original_name,
        file_size_bytes=size,
    )
    return PresignResponse(**result)


@upload_router.post("/confirm")
def confirm_upload(body: ConfirmRequest, user: dict = Depends(get_current_user)):
    """
    Step 2: Client confirms the PUT completed.
    We verify the blob exists then enqueue the ingest job.
    """
    assert_tenant_access(user, body.tenant_id)
    try:
        mapping = MappingConfigIn.model_validate(body.mapping)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        strategy = StrategyType(body.strategy)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid strategy '{body.strategy}'. Use 'rules' or 'mlp'.",
        ) from exc

    import uuid as _uuid
    try:
        tenant_uuid = _uuid.UUID(body.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="tenant_id must be a valid UUID") from exc

    stub = JobRequest(
        tenant_id=tenant_uuid,
        source_type=SourceType.file,
        strategy=strategy,
        file_config=FileSourceConfig(filename="large_upload.csv"),
        mapping=mapping,
    )
    validate_job_submit(stub, user, allow_file_source=True)

    from prismrag.worker.large_file import enqueue_large_job
    try:
        job_id = enqueue_large_job(
            upload_id=body.upload_id,
            tenant_id=body.tenant_id,
            strategy=body.strategy,
            user_id=user["id"],
            mapping=mapping.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "job_id":     job_id,
        "status":     "queued",
        "status_url": f"/api/v1/prismrag/jobs/{job_id}",
        "message":    "File queued for processing. Poll status_url for progress.",
    }
