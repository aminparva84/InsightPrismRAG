"""
PrismRAG — Deliberation API routes.

POST   /api/deliberation/sessions              create session
POST   /api/deliberation/sessions/{id}/run     execute pipeline (sync or async)
GET    /api/deliberation/sessions/{id}         poll / get full result
GET    /api/deliberation/sessions/{id}/domains horizontal discovery results
POST   /api/deliberation/sessions/{id}/followup follow-up question
GET    /api/deliberation/health                health check

Pricing:
  Free        —  5 deliberations / month
  Starter     — 50 deliberations / month   ($29/mo)
  Professional— 500 deliberations / month  ($99/mo)
  Enterprise  — unlimited                  (custom)
  Pay-as-you-go: $0.25 per deliberation (overage or usage-based)

Token cost model (Gemini 2.0 Flash, 7 domains):
  Phase 1 horizontal : 1 call  ~600 in  +  ~400 out  = $0.000165
  Phase 2 verticals  : 7 calls ~8,400 in + ~4,900 out = $0.002100
  Phase 3 synthesis  : 1 call  ~6,000 in + ~1,200 out = $0.000810
  Total COGS         : ~$0.003 per deliberation
  Overage margin     : ~83× at $0.25 / deliberation
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from prismrag.auth.auth import get_current_user
from prismrag.auth.tenant import assert_tenant_access
from prismrag.metering.quota import check_and_record

deliberation_router = APIRouter(prefix="/api/v1/deliberation", tags=["Deliberation"])

# ── Plan limits for deliberation ──────────────────────────────────────────────
DELIBERATION_MONTHLY_LIMITS: dict[str, int] = {
    "free":         5,
    "starter":      50,
    "professional": 500,
    "enterprise":   0,   # unlimited
}
DELIBERATION_OVERAGE_PRICE_USD = 0.25  # per deliberation (~83× margin over $0.003 COGS)


def _check_deliberation_quota(user: dict) -> None:
    """Raise 402 if user has used their monthly deliberation allowance."""
    plan  = user.get("plan", "free")
    limit = DELIBERATION_MONTHLY_LIMITS.get(plan, 5)
    if limit == 0:
        return  # enterprise: unlimited
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) FROM prismrag.deliberation_session
            WHERE user_id = %s
              AND date_trunc('month', created_at) = date_trunc('month', now())
              AND status != 'failed'
            """,
            (user["id"],),
        )
        used = cur.fetchone()[0]
    finally:
        release_conn(conn)

    if used >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "error":          "deliberation_quota_exceeded",
                "used":           used,
                "limit":          limit,
                "plan":           plan,
                "overage_price":  f"${DELIBERATION_OVERAGE_PRICE_USD:.2f} per deliberation",
                "upgrade_url":    "/dashboard.html#billing",
            },
        )


# ── Request / response models ─────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    question:     str   = Field(..., min_length=10, max_length=4000)
    title:        str | None = None
    tenant_id:    str | None = None
    mapping_id:   str | None = None
    domain_count: int   = Field(7, ge=3, le=10)
    async_mode:   bool  = False   # True = fire-and-forget, poll for results


class FollowupRequest(BaseModel):
    question: str = Field(..., min_length=5, max_length=2000)


# ── Routes ────────────────────────────────────────────────────────────────────

@deliberation_router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "deliberation",
        "pipeline": ["horizontal_discovery", "vertical_queries", "synthesis"],
        "pricing": {
            "free":         {"monthly_deliberations": 5,   "price": "$0"},
            "starter":      {"monthly_deliberations": 50,  "price": "$29/mo"},
            "professional": {"monthly_deliberations": 500, "price": "$99/mo"},
            "enterprise":   {"monthly_deliberations": "unlimited", "price": "custom"},
            "pay_as_you_go": f"${DELIBERATION_OVERAGE_PRICE_USD:.2f} per deliberation",
        },
    }


@deliberation_router.post("/sessions", status_code=202)
async def create_and_run_session(
    req: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """
    Create a deliberation session and immediately start the pipeline.

    Sync (async_mode=false, default for ≤ simple questions):
      Returns the full result when done. Typically 15–40 seconds.

    Async (async_mode=true):
      Returns immediately with session_id and status=discovering.
      Poll GET /api/deliberation/sessions/{session_id} for completion.
    """
    _check_deliberation_quota(user)
    if req.tenant_id:
        assert_tenant_access(user, req.tenant_id)

    from prismrag.deliberation.engine import create_session, run_deliberation, run_deliberation_bg

    session_id = create_session(
        question=req.question,
        user_id=user["id"],
        tenant_id=req.tenant_id,
        title=req.title,
        domain_count=req.domain_count,
    )

    if req.async_mode:
        background_tasks.add_task(
            run_deliberation_bg,
            session_id=session_id,
            question=req.question,
            domain_count=req.domain_count,
            tenant_id=req.tenant_id,
            mapping_id=req.mapping_id,
        )
        return {
            "session_id": session_id,
            "status":     "discovering",
            "async":      True,
            "poll_url":   f"/api/deliberation/sessions/{session_id}",
        }

    # Synchronous — run inline (blocks until done)
    try:
        result = run_deliberation(
            session_id=session_id,
            question=req.question,
            domain_count=req.domain_count,
            tenant_id=req.tenant_id,
            mapping_id=req.mapping_id,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@deliberation_router.get("/sessions/{session_id}")
def get_session(session_id: str, user: dict = Depends(get_current_user)):
    """
    Get the current state of a deliberation session.

    Returns full result when status=done, partial data at each earlier phase:
      created      → question only
      discovering  → empty domains list (in progress)
      querying     → domains populated, verticals being filled
      synthesizing → verticals done, synthesis in progress
      done         → all fields populated
      failed       → error state
    """
    from prismrag.deliberation.engine import get_session as _get
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") and session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your session")
    return session


@deliberation_router.get("/sessions/{session_id}/domains")
def get_domains(session_id: str, user: dict = Depends(get_current_user)):
    """Return just the horizontally-discovered domains for a session."""
    from prismrag.deliberation.engine import get_session as _get
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") and session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your session")
    return {
        "session_id": session_id,
        "question":   session["question"],
        "domains":    session.get("domains", []),
        "status":     session["status"],
    }


@deliberation_router.post("/sessions/{session_id}/followup")
def followup(
    session_id: str,
    req: FollowupRequest,
    user: dict = Depends(get_current_user),
):
    """
    Ask a follow-up question against a completed deliberation.
    Does NOT consume a deliberation credit — included in all plans.
    """
    from prismrag.deliberation.engine import get_session as _get, run_followup
    session = _get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("user_id") and session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your session")
    try:
        return run_followup(session_id=session_id, followup_question=req.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@deliberation_router.get("/sessions")
def list_sessions(user: dict = Depends(get_current_user)):
    """List the current user's deliberation sessions (most recent 20)."""
    from prismrag.db import get_conn, release_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, title, status, question, domain_count, created_at, updated_at
            FROM prismrag.deliberation_session
            WHERE user_id = %s
            ORDER BY created_at DESC LIMIT 20
            """,
            (user["id"],),
        )
        return [
            {
                "session_id":   str(r[0]),
                "title":        r[1],
                "status":       r[2],
                "question":     r[3][:200],
                "domain_count": r[4],
                "created_at":   r[5].isoformat() if r[5] else None,
                "updated_at":   r[6].isoformat() if r[6] else None,
            }
            for r in cur.fetchall()
        ]
    finally:
        release_conn(conn)
