"""PrismRAG — Billing API routes (Stripe)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from prismrag.auth.auth import get_current_user
from prismrag.billing.stripe_client import (
    create_checkout_session,
    create_portal_session,
    handle_webhook,
    process_webhook_event,
    STRIPE_PUBLISHABLE_KEY,
)

router = APIRouter(prefix="/api/v1/billing", tags=["Billing"])
billing_router = router  # alias used in main.py

BASE_URL = os.getenv("PRISMRAG_BASE_URL", "http://localhost:8001")


class CheckoutIn(BaseModel):
    plan: str  # starter | professional | enterprise


@router.get("/plans")
def list_plans():
    """Return plan details from DB and Stripe publishable key for frontend."""
    from prismrag.plans import get_all_plans

    plans_db = get_all_plans()
    def _fmt_chunks(n: int) -> str:
        if n <= 0:
            return "Unlimited chunks"
        return f"{n:,} chunks / month"

    plan_meta = {
        "free":         {"name": "Free", "price": 0, "cta": "Start Free"},
        "starter":      {"name": "Starter", "price": 4900, "cta": "Start Starter"},
        "professional": {"name": "Professional", "price": 19900, "cta": "Start Professional", "popular": True},
        "enterprise":   {"name": "Enterprise", "price": None, "cta": "Contact Sales"},
    }
    plans = []
    for pid, limits in plans_db.items():
        meta = plan_meta.get(pid, {"name": pid.title(), "price": 0, "cta": "Select"})
        features = [_fmt_chunks(limits["monthly_chunks"])]
        mt = limits["max_tenants"]
        features.append("Unlimited workspaces" if mt < 0 else f"{mt} workspace(s)")
        if limits.get("graph_rag"):
            features.append("Graph RAG retrieval")
        if limits.get("tier2_mlp"):
            features.append("Tier-2 MLP training")
        if limits.get("bridge_vectors"):
            features.append("Bridge vectors")
        plans.append({
            "id": pid,
            "name": meta["name"],
            "price": meta["price"],
            "currency": "usd",
            "interval": "month",
            "features": features,
            "cta": meta["cta"],
            "popular": meta.get("popular", False),
        })

    return {"stripePublishableKey": STRIPE_PUBLISHABLE_KEY, "plans": plans}


@router.post("/checkout")
def create_checkout(body: CheckoutIn, user: dict = Depends(get_current_user)):
    if body.plan == "enterprise":
        return {"redirect": "mailto:sales@prismrag.io?subject=Enterprise%20Inquiry"}
    if body.plan == "free":
        return {"redirect": f"{BASE_URL}/dashboard"}

    try:
        url = create_checkout_session(
            user_id=user["id"],
            email=user["email"],
            name=user.get("fullName") or user["email"],
            plan=body.plan,
            success_url=f"{BASE_URL}/dashboard?upgrade=success",
            cancel_url=f"{BASE_URL}/pricing",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"redirect": url}


@router.post("/portal")
def billing_portal(user: dict = Depends(get_current_user)):
    cid = user.get("stripeCustomerId")
    if not cid:
        raise HTTPException(
            status_code=400,
            detail="No billing account found. Subscribe to a plan first.",
        )
    url = create_portal_session(
        customer_id=cid,
        return_url=f"{BASE_URL}/dashboard",
    )
    return {"redirect": url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Stripe sends signed events here. Must be publicly reachable."""
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = handle_webhook(payload, sig_header)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    result = process_webhook_event(event)
    return {"received": True, "result": result}
