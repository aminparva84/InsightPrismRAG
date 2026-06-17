"""PrismRAG — Tenant/workspace access control (RBAC-backed)."""
from __future__ import annotations

from prismrag.auth.rbac import assert_permission


def assert_tenant_access(
    user: dict,
    tenant_id: str,
    permission: str = "read",
) -> None:
    """Ensure user has permission on workspace."""
    assert_permission(user, tenant_id, permission)  # type: ignore[arg-type]


def assert_job_access(user: dict, job_id: str, permission: str = "read") -> str:
    """Ensure job exists and user can access its tenant. Returns tenant_id."""
    from fastapi import HTTPException
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT tenant_id::text FROM prismrag.ingest_job WHERE id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    finally:
        release_conn(conn)

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    tenant_id = row[0]
    assert_tenant_access(user, tenant_id, permission)
    return tenant_id
