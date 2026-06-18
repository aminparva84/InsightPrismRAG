#!/usr/bin/env python3
"""
Verify the production QA user against the published PrismRAG API.

The QA user is created in Azure Postgres via:
  python tests/seed_qa_data.py --production --drop --dsn <azure-dsn>

Usage:
  python scripts/qa_setup_prod_user.py
  python scripts/qa_setup_prod_user.py --url https://prismrag.insightits.com
  python scripts/qa_setup_prod_user.py --verify-only

Credentials (stable — update seed + docs if rotated):
  Email    : qa-prod@insightits.com
  Password : QaProdPass!2026#
  User ID  : 20000000-0000-0000-0000-000000000010
"""
import argparse
import os
import sys

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

PROD_URL = "https://prismrag.insightits.com"
QA_EMAIL = os.environ.get("PRISMRAG_PROD_QA_EMAIL", "qa-prod@insightits.com")
QA_PASS = os.environ.get("PRISMRAG_PROD_QA_PASSWORD", "QaProdPass!2026#")
QA_NAME = "PrismRAG Production QA"


def login(base: str) -> str | None:
    r = requests.post(
        f"{base}/api/v1/auth/login",
        json={"email": QA_EMAIL, "password": QA_PASS},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"  [ERROR] Login failed {r.status_code}: {r.text[:300]}")
        print("  Hint: seed Azure DB with tests/seed_qa_data.py --production --drop")
        return None
    data = r.json()
    if data.get("mfa_required"):
        print("  [ERROR] QA user has MFA enabled — disable MFA on this account.")
        return None
    token = data.get("token") or data.get("access_token")
    if not token:
        print(f"  [ERROR] No token in login response: {data}")
        return None
    print(f"  Login OK — token prefix: {token[:20]}...")
    return token


def verify_me(base: str, token: str) -> bool:
    r = requests.get(
        f"{base}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"  [ERROR] /auth/me returned {r.status_code}: {r.text[:200]}")
        return False
    me = r.json()
    print(
        f"  Account confirmed: email={me.get('email')}  "
        f"plan={me.get('plan')}  active={me.get('is_active', True)}"
    )
    return True


def check_health(base: str) -> bool:
    try:
        r = requests.get(f"{base}/api/v1/prismrag/health", timeout=10)
        if r.status_code == 200:
            print(f"  API health: OK ({r.json()})")
            return True
        print(f"  [WARN] Health check returned {r.status_code}")
        return False
    except Exception as exc:
        print(f"  [WARN] Health check failed: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify PrismRAG production QA user")
    parser.add_argument(
        "--url",
        default=os.environ.get("PRISMRAG_PROD_URL", PROD_URL),
        help="Production API base URL",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Login + /me only (user must exist in Azure Postgres)",
    )
    args = parser.parse_args()

    base = args.url.rstrip("/")
    print(f"Target: {base}")
    print(f"QA user: {QA_EMAIL}\n")

    print("[1] Checking API health...")
    check_health(base)

    print("\n[2] Logging in...")
    token = login(base)
    if not token:
        sys.exit(1)

    print("\n[3] Verifying account...")
    if not verify_me(base, token):
        sys.exit(1)

    print("\n" + "=" * 60)
    print("QA prod user is READY.")
    print(f"\nAdd to your .env for prod tests:")
    print(f"  PRISMRAG_PROD_URL={base}")
    print(f"  PRISMRAG_PROD_QA_EMAIL={QA_EMAIL}")
    print(f"  PRISMRAG_PROD_QA_PASSWORD={QA_PASS}")
    print(f"  QA_SEEDED=1")
    print("=" * 60)


if __name__ == "__main__":
    main()
