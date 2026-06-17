"""PrismRAG — Multi-region routing and CMEK configuration."""
from __future__ import annotations

import os
from typing import Any

# Supported deployment regions (extend as you add regional Postgres replicas)
REGIONS: dict[str, dict[str, Any]] = {
    "us-east": {
        "label": "US East (Virginia)",
        "dsn_env": "PRISMRAG_DB_DSN",
        "default": True,
    },
    "eu-west": {
        "label": "EU West (Ireland)",
        "dsn_env": "PRISMRAG_DB_DSN_EU",
        "default": False,
    },
    "ap-southeast": {
        "label": "Asia Pacific (Singapore)",
        "dsn_env": "PRISMRAG_DB_DSN_AP",
        "default": False,
    },
}

DEFAULT_REGION = os.getenv("PRISMRAG_DEFAULT_REGION", "us-east")


def list_regions() -> list[dict]:
    return [
        {"id": rid, "label": meta["label"], "available": _region_available(rid)}
        for rid, meta in REGIONS.items()
    ]


def _region_available(region_id: str) -> bool:
    meta = REGIONS.get(region_id)
    if not meta:
        return False
    if region_id == DEFAULT_REGION:
        return bool(os.getenv(meta["dsn_env"]) or os.getenv("PRISMRAG_DB_DSN"))
    return bool(os.getenv(meta["dsn_env"]))


def get_tenant_region(tenant_id: str) -> str:
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT data_region FROM prismrag.tenant WHERE id = %s", (tenant_id,))
        row = cur.fetchone()
        return row[0] if row else DEFAULT_REGION
    finally:
        release_conn(conn)


def get_cmek_config(tenant_id: str) -> dict:
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT t.data_region, t.cmek_key_id, t.cmek_vault_url,
                   o.cmek_enabled, o.cmek_key_id, o.cmek_vault_url
            FROM prismrag.tenant t
            LEFT JOIN prismrag.organization o ON o.id = t.organization_id
            WHERE t.id = %s
            """,
            (tenant_id,),
        )
        row = cur.fetchone()
    finally:
        release_conn(conn)

    if not row:
        return {"enabled": False}
    tenant_key, tenant_vault = row[1], row[2]
    org_enabled, org_key, org_vault = row[3], row[4], row[5]
    enabled = bool(org_enabled and (tenant_key or org_key))
    return {
        "enabled": enabled,
        "region": row[0],
        "key_id": tenant_key or org_key,
        "vault_url": tenant_vault or org_vault,
        "provider": "azure_key_vault" if enabled else None,
    }


def validate_region(region_id: str) -> None:
    from fastapi import HTTPException

    if region_id not in REGIONS:
        raise HTTPException(status_code=422, detail=f"Unknown region '{region_id}'")
    if not _region_available(region_id):
        raise HTTPException(
            status_code=503,
            detail=f"Region '{region_id}' is not deployed yet. Contact sales for multi-region.",
        )
