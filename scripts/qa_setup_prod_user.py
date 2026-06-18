#!/usr/bin/env python3
"""
Create and verify the dedicated QA test user in the production Azure environment.

Usage:
  python scripts/qa_setup_prod_user.py
  python scripts/qa_setup_prod_user.py --url https://prismrag-api.delightfuldesert-fc8896c5.eastus2.azurecontainerapps.io

What it does:
  1. Attempts to register qa-prod@test.prismrag.io against the prod API
  2. Logs in to verify credentials work
  3. Calls /auth/me to confirm the account is active
  4. Prints the bearer token so you can paste it into .env or run tests immediately

The QA prod user credentials (stable, never rotate without updating CI):
  Email    : qa-prod@test.prismrag.io
  Password : QaProd!2024Secure
"""
import argparse
import os
import sys

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

PROD_URL  = "https://prismrag-api.delightfuldesert-fc8896c5.eastus2.azurecontainerapps.io"
QA_EMAIL  = os.environ.get("PRISMRAG_PROD_QA_EMAIL",    "qa-prod@test.prismrag.io")
QA_PASS   = os.environ.get("PRISMRAG_PROD_QA_PASSWORD", "QaProd!2024Secure")
QA_NAME   = "PrismRAG QA Prod User"


def register(base: str) -> bool:
    r = requests.post(f"{base}/api/v1/auth/register", json={
        "email":     QA_EMAIL,
        "password":  QA_PASS,
        "full_name": QA_NAME,
    }, timeout=15)
    if r.status_code == 409:
        print("  User already exists — skipping registration.")
        return True
    if r.status_code in (200, 201):
        print(f"  Registered: {QA_EMAIL}")
        return True
    print(f"  [WARN] Register returned {r.status_code}: {r.text[:200]}")
    return False


def login(base: str) -> str | None:
    r = requests.post(f"{base}/api/v1/auth/login", json={
        "email":    QA_EMAIL,
        "password": QA_PASS,
    }, timeout=15)
    if r.status_code != 200:
        print(f"  [ERROR] Login failed {r.status_code}: {r.text[:300]}")
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
    r = requests.get(f"{base}/api/v1/auth/me",
                     headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if r.status_code != 200:
        print(f"  [ERROR] /auth/me returned {r.status_code}: {r.text[:200]}")
        return False
    me = r.json()
    print(f"  Account confirmed: email={me.get('email')}  plan={me.get('plan')}  active={me.get('is_active')}")
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
    parser = argparse.ArgumentParser(description="Set up PrismRAG QA prod user")
    parser.add_argument("--url", default=os.environ.get("PRISMRAG_PROD_URL", PROD_URL),
                        help="Production API base URL")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    print(f"Target: {base}")
    print(f"QA user: {QA_EMAIL}\n")

    print("[1] Checking API health...")
    check_health(base)

    print("\n[2] Registering QA user...")
    if not register(base):
        sys.exit(1)

    print("\n[3] Logging in...")
    token = login(base)
    if not token:
        sys.exit(1)

    print("\n[4] Verifying account...")
    if not verify_me(base, token):
        sys.exit(1)

    print("\n" + "=" * 60)
    print("QA prod user is READY.")
    print(f"\nAdd to your .env for prod tests:")
    print(f"  PRISMRAG_TEST_URL={base}")
    print(f"  PRISMRAG_TEST_EMAIL={QA_EMAIL}")
    print(f"  PRISMRAG_TEST_PASSWORD={QA_PASS}")
    print(f"\nBearer token (valid 72h):")
    print(f"  {token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
