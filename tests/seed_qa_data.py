#!/usr/bin/env python3
"""
Seed QA test data into Postgres.

Usage:
  python tests/seed_qa_data.py                          # uses PRISMRAG_DB_DSN env
  python tests/seed_qa_data.py --dsn "postgresql://..."
  python tests/seed_qa_data.py --domain healthcare      # seed one domain only
  python tests/seed_qa_data.py --drop                   # drop QA data first, then seed

Domains seeded:
  healthcare  — clinical notes, diagnosis, treatment, lab results
  pharmacy    — drug monographs, interactions, PK data
  finance     — analyst reports, risk/valuation/liquidity

Fixed tenant IDs (stable across runs):
  healthcare : 10000000-0000-0000-0000-000000000001
  pharmacy   : 10000000-0000-0000-0000-000000000002
  finance    : 10000000-0000-0000-0000-000000000003
  qa user    : 20000000-0000-0000-0000-000000000001  (qa-local@test.prismrag.io / QaTestPass!123)
  qa-prod    : 20000000-0000-0000-0000-000000000010  (qa-prod@insightits.com / QaProdPass!2026#)
"""
import argparse
import os
import sys
from pathlib import Path

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

FIXTURES = Path(__file__).parent / "fixtures"

QA_TENANT_IDS = {
    "healthcare": "10000000-0000-0000-0000-000000000001",
    "pharmacy":   "10000000-0000-0000-0000-000000000002",
    "finance":    "10000000-0000-0000-0000-000000000003",
}
QA_USER_ID = "20000000-0000-0000-0000-000000000001"
QA_PROD_USER_ID = "20000000-0000-0000-0000-000000000010"
QA_PROD_EMAIL = "qa-prod@insightits.com"
QA_MAPPING_IDS = {
    "healthcare": "30000000-0000-0000-0000-000000000001",
    "pharmacy":   "30000000-0000-0000-0000-000000000002",
    "finance":    "30000000-0000-0000-0000-000000000003",
}


def connect(dsn: str):
    return psycopg2.connect(dsn)


def drop_qa_data(cur, *, production: bool = False):
    print("Dropping existing QA data...")
    user_id = QA_PROD_USER_ID if production else QA_USER_ID
    for domain, tid in QA_TENANT_IDS.items():
        mid = QA_MAPPING_IDS[domain]
        cur.execute("DELETE FROM prismrag.bridge_vector      WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.community_summary  WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.community_member   WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.word_graph_edge    WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.chunk_embedding    WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.ingest_job         WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.mapping_rule       WHERE mapping_id = %s", (mid,))
        cur.execute("DELETE FROM prismrag.mapping_category   WHERE mapping_id = %s", (mid,))
        cur.execute("DELETE FROM prismrag.mapping_version    WHERE id = %s", (mid,))
        cur.execute("DELETE FROM prismrag.tenant_member      WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.tenant             WHERE id = %s", (tid,))
    cur.execute("DELETE FROM prismrag.user_account WHERE id = %s", (user_id,))
    print("  QA data dropped.")


def _link_production_owner(cur):
    """Point seeded tenants at the production QA user."""
    cur.execute(
        """
        UPDATE prismrag.tenant
        SET owner_email = %s
        WHERE id IN %s
        """,
        (QA_PROD_EMAIL, tuple(QA_TENANT_IDS.values())),
    )
    for tid in QA_TENANT_IDS.values():
        cur.execute(
            """
            INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
            VALUES (%s, %s, 'owner')
            ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = 'owner'
            """,
            (tid, QA_PROD_USER_ID),
        )


def run_sql_file(cur, path: Path):
    if not path.exists():
        print(f"  [WARN] Fixture not found: {path}")
        return False
    sql = path.read_text(encoding="utf-8")
    cur.execute(sql)
    return True


def _link_tenant_owner(cur, domain: str, user_id: str):
    tid = QA_TENANT_IDS[domain]
    cur.execute(
        """
        INSERT INTO prismrag.tenant_member (tenant_id, user_id, role)
        VALUES (%s, %s, 'owner')
        ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = 'owner'
        """,
        (tid, user_id),
    )


def seed_domain(cur, domain: str, *, owner_user_id: str | None = None):
    ok = run_sql_file(cur, FIXTURES / f"{domain}_seed.sql")
    if ok:
        if owner_user_id:
            _link_tenant_owner(cur, domain, owner_user_id)
        print(f"  Seeded {domain} domain.")


def main():
    parser = argparse.ArgumentParser(description="Seed PrismRAG QA test data")
    parser.add_argument("--dsn", default=os.environ.get("PRISMRAG_DB_DSN", ""),
                        help="PostgreSQL DSN (default: PRISMRAG_DB_DSN env var)")
    parser.add_argument("--domain", choices=["healthcare", "pharmacy", "finance", "all"],
                        default="all", help="Which domain to seed (default: all)")
    parser.add_argument("--drop", action="store_true",
                        help="Drop existing QA data before seeding")
    parser.add_argument("--production", action="store_true",
                        help="Seed production QA user (qa-prod@insightits.com) for Azure")
    args = parser.parse_args()

    if not args.dsn:
        print("ERROR: Provide --dsn or set PRISMRAG_DB_DSN environment variable")
        sys.exit(1)

    host_part = args.dsn.split("@")[-1] if "@" in args.dsn else args.dsn
    print(f"Connecting to: {host_part}")
    conn = connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        if args.drop:
            drop_qa_data(cur, production=args.production)

        user_fixture = "qa_production_user_seed.sql" if args.production else "qa_user_seed.sql"
        owner_id = QA_PROD_USER_ID if args.production else QA_USER_ID
        print(f"Seeding QA user account ({user_fixture})...")
        if args.production:
            from prismrag.auth.auth import hash_password
            sql = (FIXTURES / user_fixture).read_text(encoding="utf-8")
            pwd_hash = hash_password("QaProdPass!2026#")
            sql = sql.replace(
                "$2b$12$5ujfNNN224cHcuoizB1Qcue570qlSzAxwmbe9XIaXltDcvBI.XK.K",
                pwd_hash,
            )
            cur.execute(sql)
        else:
            run_sql_file(cur, FIXTURES / user_fixture)

        domains = ["healthcare", "pharmacy", "finance"] if args.domain == "all" else [args.domain]
        for domain in domains:
            print(f"Seeding {domain}...")
            seed_domain(cur, domain, owner_user_id=owner_id)

        if args.production:
            _link_production_owner(cur)

        conn.commit()
        print("\nQA data seeded successfully.")
        print("\nFixed IDs for test config:")
        if args.production:
            print(f"  QA prod user: {QA_PROD_USER_ID}  ({QA_PROD_EMAIL} / QaProdPass!2026#)")
        else:
            print(f"  QA user    : {QA_USER_ID}  (qa-local@test.prismrag.io / QaTestPass!123)")
        for domain, tid in QA_TENANT_IDS.items():
            if domain in domains:
                mid = QA_MAPPING_IDS[domain]
                print(f"  {domain:12s}: tenant={tid}  mapping={mid}")

    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
