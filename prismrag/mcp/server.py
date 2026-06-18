"""
PrismRAG — MCP Server

Exposes PrismRAG capabilities as MCP tools so any AI agent that speaks
Model Context Protocol can search your knowledge graph without writing
any integration code.

Transports:
  stdio (default) — for Claude Desktop and local agents
  http            — for remote/deployed agents (set PRISMRAG_MCP_TRANSPORT=http)

Usage:
  python -m prismrag.mcp.server               # stdio
  python -m prismrag.mcp.server --port 8002   # HTTP/SSE

Required env vars:
  PRISMRAG_API_BASE    Base URL of the PrismRAG REST API
                       e.g. https://api.prismrag.io  or  http://localhost:8001
  PRISMRAG_API_KEY     A prk_... API key
  PRISMRAG_TENANT_ID   Default tenant UUID (can be overridden per tool call)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

try:
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        CallToolRequest, CallToolResult,
        ListToolsRequest, ListToolsResult,
        TextContent, Tool,
    )
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

API_BASE   = os.getenv("PRISMRAG_API_BASE", "http://localhost:8001")
API_KEY    = os.getenv("PRISMRAG_API_KEY", "")
TENANT_ID  = os.getenv("PRISMRAG_TENANT_ID", "")
MCP_HTTP_TOKEN = os.getenv("PRISMRAG_MCP_HTTP_TOKEN", "")


# ── HTTP client ───────────────────────────────────────────────────────────────

def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=30.0,
    )


def _call(method: str, path: str, **kwargs) -> dict:
    with _client() as c:
        res = getattr(c, method)(path, **kwargs)
        res.raise_for_status()
        return res.json()


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "search",
        "description": (
            "Search the PrismRAG knowledge graph using a natural language query. "
            "Uses Graph RAG retrieval: community-level search → BFS graph expansion → re-rank. "
            "Returns the most relevant concepts and their community context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 10, max 50)",
                    "default": 10
                },
                "category_filter": {
                    "type": "string",
                    "description": "Optional: restrict results to one category slug"
                },
                "tenant_id": {
                    "type": "string",
                    "description": "Workspace ID (uses default if omitted)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "list_workspaces",
        "description": (
            "List all workspaces (tenants) available to the authenticated user. "
            "Each workspace has its own isolated knowledge graph and vector space."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "list_communities",
        "description": (
            "List the community clusters in a workspace. Each community is a group "
            "of related concepts identified by Louvain graph partitioning and labeled "
            "by an LLM. Useful for understanding the domain structure before searching."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Workspace ID (uses default if omitted)"
                },
                "mapping_id": {
                    "type": "string",
                    "description": "Optional: specific mapping version UUID"
                }
            }
        }
    },
    {
        "name": "get_job_status",
        "description": "Check the status and progress of an ingest job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job UUID returned by submit_job"
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "submit_job",
        "description": (
            "Submit a new ingest job with word-to-category mapping rules. "
            "The system will embed all words, build a knowledge graph, and detect communities. "
            "For large datasets (>1MB), use the REST upload API instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Workspace ID (uses default if omitted)"
                },
                "strategy": {
                    "type": "string",
                    "enum": ["rules", "mlp"],
                    "description": "rules=Tier 1 (auditable, always available), mlp=Tier 2 (Professional+)",
                    "default": "rules"
                },
                "categories": {
                    "type": "array",
                    "description": "Category definitions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slug":  { "type": "string" },
                            "label": { "type": "string" }
                        },
                        "required": ["slug", "label"]
                    }
                },
                "rules": {
                    "type": "array",
                    "description": "Word-to-category assignments",
                    "items": {
                        "type": "object",
                        "properties": {
                            "word":          { "type": "string" },
                            "category_slug": { "type": "string" },
                            "text":          { "type": "string", "description": "Optional full sentence context" }
                        },
                        "required": ["word", "category_slug"]
                    }
                }
            },
            "required": ["categories", "rules"]
        }
    },
    {
        "name": "create_bridge",
        "description": (
            "Create a synthetic bridge vector connecting two community clusters. "
            "After creation, search queries near either community can traverse to the other. "
            "Requires Professional plan or above."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id":   { "type": "string" },
                "mapping_id":  { "type": "string" },
                "community_a": { "type": "integer", "description": "Community ID (from list_communities)" },
                "community_b": { "type": "integer", "description": "Community ID to connect to" },
                "label":       { "type": "string",  "description": "Optional human-readable bridge label" }
            },
            "required": ["community_a", "community_b"]
        }
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _handle_search(args: dict) -> str:
    tenant = args.get("tenant_id") or TENANT_ID
    if not tenant:
        return "Error: tenant_id required. Set PRISMRAG_TENANT_ID env var or pass tenant_id."

    wait = args.get("wait", True)
    raw = _call("post", "/api/v1/prismrag/search", json={
        "tenant_id":       tenant,
        "query":           args["query"],
        "top_k":           args.get("top_k", 10),
        "category_filter": args.get("category_filter"),
        "wait":            wait,
    })

    result = raw
    if raw.get("task_id") and not raw.get("hits"):
        import time
        task_id = raw["task_id"]
        for _ in range(60):
            time.sleep(1)
            task = _call("get", f"/api/v1/prismrag/search/tasks/{task_id}")
            if task.get("status") == "completed" and task.get("result"):
                result = task["result"]
                break
            if task.get("status") == "failed":
                return f"Search failed: {task.get('error_message', 'unknown error')}"
        else:
            return f"Search task {task_id} timed out after 60s. Poll status_url manually."

    hits = result.get("hits", [])
    if not hits:
        return f"No results found for: {args['query']}"

    lines = [
        f"Search: '{args['query']}' — {len(hits)} results "
        f"({result.get('retrieval_mode', 'unknown')} mode)\n"
    ]
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}. [{h.get('score', 0):.3f}] {h.get('chunk_ref', '')} — {h.get('chunk_text', '')}\n"
            f"   Category: {h.get('category_slug', '?')} | "
            f"Community: {h.get('community_label', 'unassigned')}"
        )
    return "\n".join(lines)


def _handle_list_workspaces(_args: dict) -> str:
    try:
        tenants = _call("get", "/api/v1/prismrag/tenants")
        if not tenants:
            return "No workspaces yet. Create one via the dashboard or POST /api/v1/prismrag/tenants."
        lines = [f"Workspaces ({len(tenants)}):\n"]
        for t in tenants:
            lines.append(
                f"  • {t.get('name', '?')} — {t.get('tenant_id')} "
                f"({t.get('role', 'member')}, {t.get('data_region', 'us-east')})"
            )
        if TENANT_ID:
            lines.append(f"\nDefault tenant (env): {TENANT_ID}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _handle_list_communities(args: dict) -> str:
    tenant = args.get("tenant_id") or TENANT_ID
    if not tenant:
        return "Error: tenant_id required."
    mapping_id = args.get("mapping_id", "")
    try:
        params = {"tenant_id": tenant}
        if mapping_id:
            params["mapping_id"] = mapping_id
        rows = _call("get", "/api/v1/prismrag/communities", params=params)
        if not rows:
            return "No communities found. Submit an ingest job first to build the knowledge graph."

        lines = [f"Communities in workspace {tenant[:8]}... ({len(rows)} shown)\n"]
        for r in rows:
            words = (r.get("top_words") or [])[:5]
            lines.append(
                f"  [{r.get('id')}] {r.get('label')} (size={r.get('size')}) — "
                f"top: {', '.join(words)}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


def _handle_get_job_status(args: dict) -> str:
    job = _call("get", f"/api/v1/prismrag/jobs/{args['job_id']}")
    return (
        f"Job: {job.get('job_id', '?')}\n"
        f"Status: {job.get('status')}\n"
        f"Progress: {job.get('progress_pct', 0)}% "
        f"({job.get('records_written', 0)} / {job.get('records_total', '?')} records)\n"
        f"Started: {job.get('started_at', '?')}\n"
        f"Finished: {job.get('finished_at', 'not yet')}\n"
        + (f"Error: {job['error_message']}" if job.get('error_message') else "")
    )


def _handle_submit_job(args: dict) -> str:
    tenant = args.get("tenant_id") or TENANT_ID
    if not tenant:
        return "Error: tenant_id required."
    categories = args.get("categories", [])
    rules = args.get("rules", [])
    records = []
    for rule in rules:
        word = (rule.get("word") or "").strip()
        if not word:
            continue
        records.append({
            "word": word,
            "text": rule.get("text") or word.replace("_", " "),
            "category_hint": rule.get("category_slug"),
        })
    if not records:
        return "Error: at least one rule with a word is required."
    result = _call("post", "/api/v1/prismrag/jobs", json={
        "tenant_id":   tenant,
        "source_type": "inline",
        "strategy":    args.get("strategy", "rules"),
        "mapping": {
            "categories": categories,
            "rules":      rules,
        },
        "inline_config": {"records": records},
    })
    return (
        f"Job submitted: {result.get('job_id')}\n"
        f"Status: {result.get('status')}\n"
        f"Poll: GET {result.get('status_url')}"
    )


def _handle_create_bridge(args: dict) -> str:
    tenant = args.get("tenant_id") or TENANT_ID
    if not tenant:
        return "Error: tenant_id required."
    mapping_id = args.get("mapping_id", "")
    if not mapping_id:
        return "Error: mapping_id required for bridge creation."
    result = _call("post", "/api/v1/prismrag/bridge", json={
        "tenant_id":   tenant,
        "mapping_id":  mapping_id,
        "community_a": args["community_a"],
        "community_b": args["community_b"],
        "label":       args.get("label"),
    })
    return (
        f"Bridge created: {result.get('bridge_id')}\n"
        f"Label: {result.get('label')}\n"
        f"Communities: {result.get('community_a')} ↔ {result.get('community_b')}\n"
        f"Edges added: {result.get('edges_added')}"
    )


def _handle_deliberate(args: dict) -> str:
    """Run a horizontal→vertical→synthesis deliberation pipeline."""
    question = args.get("question", "").strip()
    if not question:
        return "Error: question is required."

    tenant_id  = args.get("tenant_id") or TENANT_ID
    domain_count = int(args.get("domain_count", 7))

    res = _call("post", "/api/v1/deliberation/sessions", json={
        "question":     question,
        "tenant_id":    tenant_id or None,
        "domain_count": domain_count,
        "async_mode":   False,
    })

    if res.get("status") != "done":
        return f"Deliberation failed or still running. Status: {res.get('status')}\nSession: {res.get('session_id')}"

    synth = res.get("synthesis") or {}
    domains = [d["name"] for d in (res.get("domains") or [])]
    lines = [
        f"Deliberation complete — {len(domains)} domains: {', '.join(domains)}\n",
        f"TYPE: {synth.get('synthesis_type', '?').upper()}\n",
        f"AGREEMENTS:\n{synth.get('agreements', 'None identified')}\n",
        f"CONFLICTS:\n{synth.get('conflicts', 'None identified')}\n",
        f"UNIQUE INSIGHTS:\n{synth.get('unique_insights', '')}\n",
        f"FINAL ANSWER:\n{synth.get('final_answer', '')}",
        f"\nConfidence: {synth.get('confidence', 0):.0%}",
        f"Session ID: {res.get('session_id')} (use get_deliberation_session to retrieve later)",
    ]
    return "\n".join(lines)


def _handle_get_deliberation_session(args: dict) -> str:
    session_id = args.get("session_id", "").strip()
    if not session_id:
        return "Error: session_id required."
    res = _call("get", f"/api/v1/deliberation/sessions/{session_id}")
    synth = res.get("synthesis") or {}
    return (
        f"Session: {session_id[:8]}… | Status: {res.get('status')}\n"
        f"Question: {res.get('question', '')[:200]}\n"
        f"Domains: {', '.join(d['name'] for d in res.get('domains', []))}\n"
        f"Final Answer: {synth.get('final_answer', 'Not yet available')[:800]}"
    )


def _handle_deliberation_followup(args: dict) -> str:
    session_id = args.get("session_id", "").strip()
    question   = args.get("question", "").strip()
    if not session_id or not question:
        return "Error: session_id and question are required."
    res = _call("post", f"/api/v1/deliberation/sessions/{session_id}/followup",
                json={"question": question})
    return f"Follow-up Answer:\n{res.get('answer', 'No answer returned.')}"


# ── Chunk portability + quality tool definitions ───────────────────────────────

TOOLS.extend([
    {
        "name": "append_chunks",
        "description": (
            "Tier-2: Append new raw chunks to an existing MLP mapping — no full retrain. "
            "The existing MLP generalises its learned category boundaries to the new texts. "
            "Optionally provide new mapping rules to expand the vocabulary; only the final "
            "projection layer is fine-tuned (freeze-then-finetune). "
            "Every chunk gets a quality_score (0-1). Flagged chunks (score < 0.45) can be "
            "auto-corrected via the ml_fallback parameter. "
            "Existing chunks with the same ref are updated in-place (UPSERT). "
            "Use this when you have new source data and want updated category tags and embeddings "
            "without reprocessing the whole dataset."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Workspace ID (uses default if omitted)"
                },
                "chunks": {
                    "type": "array",
                    "description": "New chunks to categorise (max 5,000)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ref":  {"type": "string", "description": "Unique chunk reference ID"},
                            "text": {"type": "string", "description": "Chunk text content"}
                        },
                        "required": ["ref", "text"]
                    }
                },
                "new_rules": {
                    "type": "array",
                    "description": "Optional new word-to-category rules to expand the mapping",
                    "items": {
                        "type": "object",
                        "properties": {
                            "word":          {"type": "string"},
                            "category_slug": {"type": "string"},
                            "weight":        {"type": "number", "default": 1.0}
                        },
                        "required": ["word", "category_slug"]
                    }
                },
                "ml_fallback": {
                    "type": "string",
                    "enum": ["auto", "always", "never"],
                    "description": (
                        "'auto' (default): correct flagged chunks via rule lookup. "
                        "'always': apply rule lookup to every chunk. "
                        "'never': trust MLP only."
                    ),
                    "default": "auto"
                },
                "include_vectors": {
                    "type": "boolean",
                    "description": "Include 256-d personal embedding in each result",
                    "default": False
                }
            },
            "required": ["chunks"]
        }
    },
    {
        "name": "export_chunks",
        "description": (
            "Export paginated remapped chunks from a workspace for use in your own pgvector. "
            "Returns chunk text, assigned category slug, and optionally the 256-d embedding. "
            "Use this to populate your own vector store and run retrieval without depending "
            "on the PrismRAG search API at runtime. "
            "Requires Starter plan or above. Metered at your plan's export quota."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Workspace ID (uses default if omitted)"
                },
                "mapping_id": {
                    "type": "string",
                    "description": "Optional mapping version UUID; defaults to active mapping"
                },
                "category": {
                    "type": "string",
                    "description": "Optional: filter to one category slug"
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (1-based, default 1)",
                    "default": 1
                },
                "page_size": {
                    "type": "integer",
                    "description": "Results per page (default 1000, max 5000)",
                    "default": 1000
                },
                "include_vectors": {
                    "type": "boolean",
                    "description": "Include the 256-d embedding in each result",
                    "default": False
                }
            }
        }
    },
    {
        "name": "score_chunk_quality",
        "description": (
            "Compute quality scores for all chunks in a mapping. "
            "Returns confidence, separation, coherence, and a combined quality_score (0-1) "
            "for each chunk, plus an aggregate summary. "
            "Chunks with quality_score < 0.45 are flagged — use append_chunks with "
            "ml_fallback='auto' to automatically reclassify them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {
                    "type": "string",
                    "description": "Workspace ID (uses default if omitted)"
                },
                "mapping_id": {
                    "type": "string",
                    "description": "Optional: specific mapping version UUID"
                }
            }
        }
    },
])


# ── New tool handlers ─────────────────────────────────────────────────────────

def _handle_append_chunks(args: dict) -> str:
    tenant = args.get("tenant_id") or TENANT_ID
    if not tenant:
        return "Error: tenant_id required. Set PRISMRAG_TENANT_ID env var or pass tenant_id."
    chunks = args.get("chunks", [])
    if not chunks:
        return "Error: chunks array is required and must not be empty."

    result = _call("post", f"/api/v1/prismrag/tenants/{tenant}/chunks/append", json={
        "chunks":          chunks,
        "new_rules":       args.get("new_rules", []),
        "ml_fallback":     args.get("ml_fallback", "auto"),
        "include_vectors": args.get("include_vectors", False),
    })

    summary = result.get("summary", {})
    appended = result.get("appended", 0)
    flagged  = summary.get("flagged", 0)
    avg_q    = summary.get("avg_quality", 0.0)

    lines = [
        f"Appended {appended} chunk(s) to workspace {tenant[:8]}...",
        f"Quality: avg={avg_q:.3f}  flagged={flagged}/{appended}",
        "",
    ]
    for c in (result.get("chunks") or [])[:20]:
        flag = " ⚠" if c.get("flagged") else ""
        lines.append(
            f"  [{c.get('quality_score', 0):.2f}]{flag} {c.get('chunk_ref')} "
            f"→ {c.get('category_slug')}  (conf={c.get('confidence', 0):.2f})"
        )
    if appended > 20:
        lines.append(f"  ... and {appended - 20} more")
    if flagged:
        lines.append(
            f"\n{flagged} chunk(s) flagged for low quality. "
            "Re-run with ml_fallback='always' to force reclassification."
        )
    return "\n".join(lines)


def _handle_export_chunks(args: dict) -> str:
    tenant = args.get("tenant_id") or TENANT_ID
    if not tenant:
        return "Error: tenant_id required."

    params: dict = {
        "page":            args.get("page", 1),
        "page_size":       args.get("page_size", 1000),
        "include_vectors": args.get("include_vectors", False),
    }
    if args.get("mapping_id"):
        params["mapping_id"] = args["mapping_id"]
    if args.get("category"):
        params["category"] = args["category"]

    result = _call("get", f"/api/v1/prismrag/tenants/{tenant}/chunks", params=params)

    total    = result.get("total", 0)
    page     = result.get("page", 1)
    has_more = result.get("has_more", False)
    chunks   = result.get("chunks", [])

    lines = [
        f"Exported {len(chunks)} chunk(s)  (page {page}, total={total}, has_more={has_more})",
        f"Mapping: {result.get('mapping_id', '?')[:8]}...",
        "",
    ]
    for c in chunks[:10]:
        lines.append(
            f"  {c.get('chunk_ref')} | {c.get('category_slug')} | "
            f"{(c.get('chunk_text') or '')[:80]}"
        )
    if len(chunks) > 10:
        lines.append(f"  ... and {len(chunks) - 10} more on this page")
    if has_more:
        lines.append(f"\nMore pages available — request page={page + 1} to continue.")
    return "\n".join(lines)


def _handle_score_chunk_quality(args: dict) -> str:
    tenant = args.get("tenant_id") or TENANT_ID
    if not tenant:
        return "Error: tenant_id required."

    params: dict = {}
    if args.get("mapping_id"):
        params["mapping_id"] = args["mapping_id"]

    result = _call("get", f"/api/v1/prismrag/tenants/{tenant}/chunks/quality", params=params)
    summary = result.get("summary", {})
    chunks  = result.get("chunks", [])

    if not chunks:
        return "No chunks found. Submit an ingest job first."

    flagged_chunks = [c for c in chunks if c.get("flagged")]
    lines = [
        f"Chunk Quality Report — workspace {tenant[:8]}...",
        f"Mapping: {result.get('mapping_id', '?')[:8]}...",
        "",
        f"  Total chunks:      {summary.get('total', 0)}",
        f"  Avg quality:       {summary.get('avg_quality', 0):.3f}",
        f"  Avg confidence:    {summary.get('avg_confidence', 0):.3f}",
        f"  Avg separation:    {summary.get('avg_separation', 0):.3f}",
        f"  Avg coherence:     {summary.get('avg_coherence', 0):.3f}",
        f"  Flagged (< 0.45):  {summary.get('flagged', 0)} ({summary.get('pct_flagged', 0):.1f}%)",
        f"  P50 quality:       {summary.get('p50_quality', 0):.3f}",
        "",
    ]
    if flagged_chunks:
        lines.append("Lowest quality chunks:")
        for c in sorted(flagged_chunks, key=lambda x: x.get("quality_score", 1))[:10]:
            lines.append(
                f"  ⚠ {c.get('chunk_ref')} | quality={c.get('quality_score', 0):.3f} "
                f"conf={c.get('confidence', 0):.3f} sep={c.get('separation', 0):.3f}"
            )
        lines.append(
            "\nTip: POST /tenants/{id}/chunks/append with ml_fallback='auto' "
            "to automatically reclassify flagged chunks."
        )
    return "\n".join(lines)


# Add deliberation tools to TOOLS list
TOOLS.extend([
    {
        "name": "deliberate",
        "description": (
            "Run a full multi-domain deliberation on a complex question. "
            "Phase 1: discovers the top relevant domains (horizontal search). "
            "Phase 2: queries each domain as a deep specialist (vertical queries). "
            "Phase 3: synthesizes agreements, conflicts, and a final answer. "
            "Best for: complex questions where multiple disciplines intersect, "
            "strategic decisions, research synthesis, risk analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question or topic to deliberate on (10–4000 chars)"
                },
                "domain_count": {
                    "type": "integer",
                    "description": "Number of domains to discover and query (3–10, default 7)",
                    "default": 7
                },
                "tenant_id": {
                    "type": "string",
                    "description": "Optional PrismRAG workspace ID to include KB context"
                }
            },
            "required": ["question"]
        }
    },
    {
        "name": "get_deliberation_session",
        "description": "Retrieve the results of a previously run deliberation session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Deliberation session UUID"}
            },
            "required": ["session_id"]
        }
    },
    {
        "name": "deliberation_followup",
        "description": (
            "Ask a follow-up question on a completed deliberation session. "
            "The Master deliberator answers using the existing panel findings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "question":   {"type": "string"}
            },
            "required": ["session_id", "question"]
        }
    },
])

_HANDLERS = {
    "search":                    _handle_search,
    "list_workspaces":           _handle_list_workspaces,
    "list_communities":          _handle_list_communities,
    "get_job_status":            _handle_get_job_status,
    "submit_job":                _handle_submit_job,
    "create_bridge":             _handle_create_bridge,
    "deliberate":                _handle_deliberate,
    "get_deliberation_session":  _handle_get_deliberation_session,
    "deliberation_followup":     _handle_deliberation_followup,
    # chunk portability + quality
    "append_chunks":             _handle_append_chunks,
    "export_chunks":             _handle_export_chunks,
    "score_chunk_quality":       _handle_score_chunk_quality,
}


# ── MCP server (stdio transport) ──────────────────────────────────────────────

async def run_stdio_server():
    if not _MCP_AVAILABLE:
        print(
            "ERROR: 'mcp' package not installed.\n"
            "Install with: pip install mcp\n"
            "Then restart the server.",
            file=sys.stderr,
        )
        sys.exit(1)

    server = Server("prismrag")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOLS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = _HANDLERS.get(name)
        if not handler:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = handler(arguments or {})
        except httpx.HTTPStatusError as exc:
            result = f"API error {exc.response.status_code}: {exc.response.text[:500]}"
        except Exception as exc:
            result = f"Error: {exc}"
        return [TextContent(type="text", text=result)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="prismrag",
                server_version="0.2.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


# ── HTTP/SSE transport ────────────────────────────────────────────────────────

def run_http_server(port: int = 8002):
    """
    Run as an HTTP server for remote agents.
    Uses FastAPI + SSE to comply with MCP HTTP transport spec.
    """
    try:
        from mcp.server.fastapi import create_mcp_app
    except ImportError:
        print(
            "HTTP transport requires mcp[server] package.\n"
            "Install with: pip install 'mcp[server]'",
            file=sys.stderr,
        )
        sys.exit(1)

    import asyncio

    async def _run():
        await run_stdio_server()  # placeholder — HTTP transport varies by mcp version

    import uvicorn

    # Build a minimal FastAPI app that wraps the tool handlers via SSE
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse, JSONResponse
    import json as _json

    http_app = FastAPI(title="PrismRAG MCP", version="1.0.0")

    def _check_mcp_auth(request: Request) -> bool:
        if not MCP_HTTP_TOKEN:
            return True
        auth = request.headers.get("authorization", "")
        return auth in (f"Bearer {MCP_HTTP_TOKEN}", MCP_HTTP_TOKEN)

    @http_app.get("/mcp/tools")
    def get_tools(request: Request):
        if not _check_mcp_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return TOOLS

    @http_app.post("/mcp/call")
    async def call_tool(request: Request):
        if not _check_mcp_auth(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        name = body.get("name")
        args = body.get("arguments", {})
        handler = _HANDLERS.get(name)
        if not handler:
            return JSONResponse({"error": f"Unknown tool: {name}"}, status_code=404)
        try:
            result = handler(args)
            return {"content": [{"type": "text", "text": result}]}
        except httpx.HTTPStatusError as exc:
            return JSONResponse(
                {"error": f"API error {exc.response.status_code}"},
                status_code=502,
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @http_app.get("/mcp/health")
    def health():
        return {"status": "ok", "server": "prismrag-mcp", "tools": len(TOOLS)}

    uvicorn.run(http_app, host="0.0.0.0", port=port)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="PrismRAG MCP Server")
    parser.add_argument("--port", type=int, default=8002,
                        help="Port for HTTP mode (ignored in stdio mode)")
    args = parser.parse_args()

    transport = os.getenv("PRISMRAG_MCP_TRANSPORT", "stdio")

    if transport == "http":
        run_http_server(port=args.port)
    else:
        asyncio.run(run_stdio_server())
