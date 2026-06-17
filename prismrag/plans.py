"""PrismRAG — Unified plan limits (DB-backed with in-memory cache)."""
from __future__ import annotations

import threading
import time
from typing import Any

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_AT = 0.0
_CACHE_TTL = 300.0
_lock = threading.Lock()

# Fallback when DB unavailable (matches enterprise_schema seed)
_DEFAULTS: dict[str, dict[str, Any]] = {
    "free": {
        "monthly_chunks": 5_000,
        "monthly_searches": 500,
        "req_per_min": 20,
        "max_tenants": 1,
        "max_mappings": 1,
        "max_file_bytes": 10_000_000,
        "log_retention_days": 7,
        "tier2_mlp": False,
        "graph_rag": False,
        "bridge_vectors": False,
        "mlp_train": False,
        "support_level": "community",
    },
    "starter": {
        "monthly_chunks": 50_000,
        "monthly_searches": 20_000,
        "req_per_min": 120,
        "max_tenants": 3,
        "max_mappings": 3,
        "max_file_bytes": 100_000_000,
        "log_retention_days": 30,
        "tier2_mlp": False,
        "graph_rag": True,
        "bridge_vectors": False,
        "mlp_train": False,
        "support_level": "email",
    },
    "professional": {
        "monthly_chunks": 500_000,
        "monthly_searches": 150_000,
        "req_per_min": 600,
        "max_tenants": 20,
        "max_mappings": 20,
        "max_file_bytes": 500_000_000,
        "log_retention_days": 30,
        "tier2_mlp": True,
        "graph_rag": True,
        "bridge_vectors": True,
        "mlp_train": True,
        "support_level": "priority",
    },
    "enterprise": {
        "monthly_chunks": 0,
        "monthly_searches": 0,
        "req_per_min": 0,
        "max_tenants": -1,
        "max_mappings": -1,
        "max_file_bytes": 0,
        "log_retention_days": 90,
        "tier2_mlp": True,
        "graph_rag": True,
        "bridge_vectors": True,
        "mlp_train": True,
        "support_level": "dedicated",
    },
}


def _row_to_limits(row: tuple) -> dict[str, Any]:
    return {
        "monthly_chunks": row[0],
        "max_tenants": row[1],
        "max_mappings": row[2],
        "tier2_mlp": row[3],
        "graph_rag": row[4],
        "bridge_vectors": row[5],
        "support_level": row[6],
        "monthly_searches": row[7] if len(row) > 7 else 500,
        "req_per_min": row[8] if len(row) > 8 else 20,
        "max_file_bytes": row[9] if len(row) > 9 else 10_000_000,
        "log_retention_days": row[10] if len(row) > 10 else 7,
        "mlp_train": row[11] if len(row) > 11 else row[3],
    }


def _load_all_from_db() -> dict[str, dict[str, Any]]:
    from prismrag.db import get_conn, release_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT plan, monthly_chunks, max_tenants, max_mappings,
                   tier2_mlp, graph_rag, bridge_vectors, support_level,
                   monthly_searches, req_per_min, max_file_bytes,
                   log_retention_days, mlp_train
            FROM prismrag.plan_quota
            """
        )
        out: dict[str, dict[str, Any]] = {}
        for row in cur.fetchall():
            out[row[0]] = _row_to_limits(row[1:])
        return out
    except Exception:
        return dict(_DEFAULTS)
    finally:
        release_conn(conn)


def get_all_plans() -> dict[str, dict[str, Any]]:
    """Return all plan limits (cached)."""
    global _CACHE, _CACHE_AT
    now = time.time()
    with _lock:
        if _CACHE and now - _CACHE_AT < _CACHE_TTL:
            return _CACHE
        loaded = _load_all_from_db()
        _CACHE = loaded or dict(_DEFAULTS)
        _CACHE_AT = now
        return _CACHE


def get_plan_limits(plan: str) -> dict[str, Any]:
    """Return limits for a plan name."""
    plans = get_all_plans()
    return plans.get(plan, plans.get("free", _DEFAULTS["free"]))


def invalidate_cache() -> None:
    global _CACHE_AT
    with _lock:
        _CACHE_AT = 0.0
