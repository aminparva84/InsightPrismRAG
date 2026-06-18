#!/usr/bin/env python3
"""
Apply PrismRAG SQL schemas to Azure PostgreSQL.

Azure Flexible Server typically allowlists only specific extensions (e.g. vector).
This script substitutes gen_random_uuid() for uuid-ossp and skips disallowed extensions.

Usage:
  python scripts/init_azure_schema.py --dsn "postgresql://...@host:5432/prismrag?sslmode=require"
  PRISMRAG_AZURE_DB_DSN=... python scripts/init_azure_schema.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
SCHEMA_FILES = (
    REPO / "prismrag" / "schema.sql",
    REPO / "prismrag" / "auth_schema.sql",
    REPO / "prismrag" / "audit_schema.sql",
    REPO / "prismrag" / "enterprise_schema.sql",
    REPO / "prismrag" / "enterprise_features_schema.sql",
    REPO / "prismrag" / "deliberation_schema.sql",
    REPO / "prismrag" / "quality" / "schema.sql",
    REPO / "prismrag" / "migrations" / "001_add_user_role.sql",
)


def azure_adapt(sql: str) -> str:
    """Make schema compatible with Azure PG (vector only, built-in gen_random_uuid)."""
    lines = []
    for line in sql.splitlines():
        if 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"' in line:
            continue
        lines.append(line.replace("uuid_generate_v4()", "gen_random_uuid()"))
    return "\n".join(lines)


def execute_script(cur, sql: str) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Init PrismRAG schema on Azure Postgres")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("PRISMRAG_AZURE_DB_DSN") or os.environ.get("PRISMRAG_DB_DSN", ""),
    )
    args = parser.parse_args()
    if not args.dsn:
        print("ERROR: Provide --dsn or set PRISMRAG_AZURE_DB_DSN")
        sys.exit(1)

    host = args.dsn.split("@")[-1] if "@" in args.dsn else args.dsn
    print(f"Connecting to: {host}")

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        for path in SCHEMA_FILES:
            if not path.exists():
                print(f"  [SKIP] Missing {path.name}")
                continue
            print(f"  Applying {path.name}...")
            execute_script(cur, azure_adapt(path.read_text(encoding="utf-8")))
        conn.commit()
        print("Schema applied successfully.")
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
