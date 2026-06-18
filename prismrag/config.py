"""PrismRAG — global configuration."""
from __future__ import annotations

import os

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv("PRISMRAG_DB_DSN") or os.getenv("PRISMRAG_DATABASE_URL", "")
DB_POOL_MIN: int = int(os.getenv("PRISMRAG_DB_POOL_MIN", "2"))
DB_POOL_MAX: int = int(os.getenv("PRISMRAG_DB_POOL_MAX", "10"))

# ── Embedding ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY") or "").strip()
GEMINI_EMBED_MODEL: str = os.getenv("PRISMRAG_EMBED_MODEL", "gemini-embedding-001")
GEMINI_LLM_MODEL: str = os.getenv("PRISMRAG_LLM_MODEL", "gemini-2.0-flash")
EMBED_DIM_SEMANTIC: int = 768    # Gemini output dimension
EMBED_DIM_PERSONAL: int = 256    # Personal MLP output dimension

# ── MLP (Tier 2) ──────────────────────────────────────────────────────────────
MLP_HIDDEN_DIM: int = 512
MLP_TRAIN_EPOCHS: int = 180
MLP_TRAIN_LR: float = 0.003
MLP_TEMPERATURE: float = 0.20
MLP_REPULSION_WEIGHT: float = 0.35
MLP_RECALL_TARGET: float = 0.85
MLP_VAL_HOLDOUT: int = 2

# ── Pipeline ──────────────────────────────────────────────────────────────────
INGEST_BATCH_SIZE: int = int(os.getenv("PRISMRAG_INGEST_BATCH_SIZE", "256"))
EMBED_BATCH_SIZE: int = int(os.getenv("PRISMRAG_EMBED_BATCH_SIZE", "64"))
JOB_TIMEOUT_SECONDS: int = int(os.getenv("PRISMRAG_JOB_TIMEOUT_SEC", "7200"))
SYNC_MAX_RECORDS: int = int(os.getenv("PRISMRAG_SYNC_MAX_RECORDS", "5000"))

# ── Graph / Community ─────────────────────────────────────────────────────────
SEM_EDGE_THRESHOLD: float = 0.70   # cosine threshold for semantic edges
COMMUNITY_LABEL_WORKERS: int = 8   # parallel LLM label threads

# ── Retrieval ─────────────────────────────────────────────────────────────────
RETRIEVAL_TOP_K: int = 32
RETRIEVAL_TOP_COMMUNITIES: int = 3
BFS_MAX_HOPS: int = 2
BFS_MAX_WORDS: int = 60

# ── HNSW index parameters ─────────────────────────────────────────────────────
HNSW_M: int = 16
HNSW_EF_CONSTRUCTION: int = 64
