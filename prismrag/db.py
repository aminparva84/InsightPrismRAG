"""PrismRAG — database connection pool and helpers."""
from __future__ import annotations

import os
import threading
from typing import Any

_pool = None
_pool_lock = threading.Lock()
_schema_ready = False
_schema_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        from psycopg2 import pool as pg_pool
        from prismrag.config import DB_POOL_MIN, DB_POOL_MAX, DATABASE_URL
        dsn = (
            DATABASE_URL
            or os.getenv("PRISMRAG_DB_DSN")
            or os.getenv("DATABASE_URL")
            or os.getenv("PRISMRAG_DATABASE_URL")
        )
        if not dsn:
            raise RuntimeError(
                "Database URL is not set. Set PRISMRAG_DB_DSN in .env "
                "(example: postgresql://prismrag:prismrag@localhost:5432/prismrag)"
            )
        _pool = pg_pool.ThreadedConnectionPool(DB_POOL_MIN, DB_POOL_MAX, dsn)
        return _pool


def get_conn():
    """Return a connection from the pool. Call release_conn() when done."""
    return _get_pool().getconn()


def release_conn(conn) -> None:
    """Return connection to pool. Safe to call even if conn is None."""
    if conn is None:
        return
    try:
        _get_pool().putconn(conn)
    except Exception:
        pass


def vector_to_pg(vec) -> str:
    """Convert a python list/array to pgvector literal '[x,y,...]'."""
    return '[' + ','.join(f'{float(x):.8f}' for x in vec) + ']'


_SCHEMA_FILES = (
    "schema.sql",
    "auth_schema.sql",
    "enterprise_schema.sql",
    "enterprise_features_schema.sql",
    "audit_schema.sql",
    "deliberation_schema.sql",
)


def _execute_sql_script(cur, sql: str) -> None:
    """Run a multi-statement SQL script (psycopg2 execute() is single-statement)."""
    statements: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    for stmt in statements:
        cur.execute(stmt)


def init_schema(force: bool = False) -> None:
    """Run all PrismRAG SQL schemas. Idempotent."""
    global _schema_ready
    if _schema_ready and not force:
        return
    with _schema_lock:
        if _schema_ready and not force:
            return
        base = os.path.dirname(__file__)
        conn = get_conn()
        try:
            cur = conn.cursor()
            for name in _SCHEMA_FILES:
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as f:
                    _execute_sql_script(cur, f.read())
            conn.commit()
            _schema_ready = True
            print("[PrismRAG] Schema ready")
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"PrismRAG schema init failed: {e}") from e
        finally:
            release_conn(conn)
