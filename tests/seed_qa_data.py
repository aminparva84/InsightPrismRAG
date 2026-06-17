#!/usr/bin/env python3
"""
Seed QA test data into Postgres.

Usage:
  python tests/seed_qa_data.py                          # uses PRISMRAG_DB_DSN env
  python tests/seed_qa_data.py --dsn "postgresql://..."
  python tests/seed_qa_data.py --domain healthcare      # seed one domain only
  python tests/seed_qa_data.py --drop                   # drop QA data first

Domains seeded:
  healthcare  — clinical notes, diagnosis, treatment, lab results
  pharmacy    — drug monographs, interactions, PK data
  finance     — analyst reports, risk/valuation/liquidity
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


def connect(dsn: str):
    return psycopg2.connect(dsn)


def drop_qa_data(cur):
    print("Dropping existing QA data...")
    for tid in QA_TENANT_IDS.values():
        cur.execute("DELETE FROM prismrag.chunk             WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.mapping_rule      WHERE mapping_id IN ("
                    "  SELECT id FROM prismrag.mapping WHERE tenant_id = %s)", (tid,))
        cur.execute("DELETE FROM prismrag.mapping_category  WHERE mapping_id IN ("
                    "  SELECT id FROM prismrag.mapping WHERE tenant_id = %s)", (tid,))
        cur.execute("DELETE FROM prismrag.mapping           WHERE tenant_id = %s", (tid,))
        cur.execute("DELETE FROM prismrag.tenant            WHERE id = %s", (tid,))
    print("  QA data dropped.")


def seed_domain(cur, domain: str):
    sql_file = FIXTURES / f"{domain}_seed.sql"
    if not sql_file.exists():
        print(f"  [WARN] Fixture not found: {sql_file}")
        return
    sql = sql_file.read_text(encoding="utf-8")
    cur.execute(sql)
    print(f"  Seeded {domain} domain.")


def main():
    parser = argparse.ArgumentParser(description="Seed PrismRAG QA test data")
    parser.add_argument("--dsn", default=os.environ.get("PRISMRAG_DB_DSN", ""),
                        help="PostgreSQL DSN (default: PRISMRAG_DB_DSN env var)")
    parser.add_argument("--domain", choices=["healthcare", "pharmacy", "finance", "all"],
                        default="all", help="Which domain to seed (default: all)")
    parser.add_argument("--drop", action="store_true",
                        help="Drop existing QA data before seeding")
    args = parser.parse_args()

    if not args.dsn:
        print("ERROR: Provide --dsn or set PRISMRAG_DB_DSN environment variable")
        sys.exit(1)

    print(f"Connecting to: {args.dsn.split('@')[-1]}")  # don't log credentials
    conn = connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        if args.drop:
            drop_qa_data(cur)

        domains = ["healthcare", "pharmacy", "finance"] if args.domain == "all" else [args.domain]
        for domain in domains:
            print(f"Seeding {domain}...")
            seed_domain(cur, domain)

        conn.commit()
        print("\nQA data seeded successfully.")
        print("\nTenant IDs for test config:")
        for domain, tid in QA_TENANT_IDS.items():
            if domain in domains:
                print(f"  {domain:12s}: {tid}")

    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
