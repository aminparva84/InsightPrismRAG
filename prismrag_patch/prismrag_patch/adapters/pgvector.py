"""
PgvectorAdapter — prismrag-patch adapter for PostgreSQL + pgvector.

Requirements: pip install "prismrag-patch[pgvector]"
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from prismrag_patch.core import PrismRAGPatch

log = logging.getLogger(__name__)


class PgvectorAdapter:
    """
    Wraps a psycopg2 connection and a pgvector table with PrismRAG re-mapping.

    Parameters
    ----------
    patch : PrismRAGPatch
        Initialized PrismRAGPatch instance (validates license on init).
    connection :
        A psycopg2 connection (or compatible) object.
    table : str
        Table name. Expected columns: id SERIAL, text TEXT, vector vector(N), metadata JSONB.
    """

    def __init__(
        self,
        patch: PrismRAGPatch,
        connection: Any,
        table: str = "prismrag_chunks",
    ) -> None:
        self.patch = patch
        self.conn  = connection
        self.table = table

    def ensure_table(self, dim: int = 1536) -> None:
        """Create the table and HNSW index if they do not exist."""
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    id       SERIAL PRIMARY KEY,
                    text     TEXT NOT NULL,
                    vector   vector({dim}) NOT NULL,
                    metadata JSONB DEFAULT '{{}}'::jsonb
                )
            """)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS {self.table}_vector_idx
                ON {self.table} USING hnsw (vector vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
        self.conn.commit()

    def insert(
        self,
        text: str,
        vector: List[float],
        metadata: Optional[Dict] = None,
    ) -> int:
        """Re-map *vector*, then insert into the table. Returns new row id."""
        result = self.patch.project(text, vector)
        remapped = result["vector"]
        meta = {**(metadata or {})}
        if result["category"]:
            meta["prismrag_category"] = result["category"]

        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self.table} (text, vector, metadata) VALUES (%s, %s, %s) RETURNING id",
                (text, remapped, json.dumps(meta)),
            )
            row_id = cur.fetchone()[0]
        self.conn.commit()
        log.debug("pgvector: inserted id=%s category=%s", row_id, result["category"])
        return row_id

    def batch_insert(
        self,
        records: List[Dict],
    ) -> List[int]:
        """
        Insert multiple records in a single transaction.

        Each record must have ``text`` and ``vector`` keys; ``metadata`` is optional.
        Returns a list of inserted row IDs in the same order as *records*.
        """
        ids = []
        with self.conn.cursor() as cur:
            for rec in records:
                result   = self.patch.project(rec["text"], rec["vector"])
                remapped = result["vector"]
                meta     = {**(rec.get("metadata") or {})}
                if result["category"]:
                    meta["prismrag_category"] = result["category"]
                cur.execute(
                    f"INSERT INTO {self.table} (text, vector, metadata) VALUES (%s, %s, %s) RETURNING id",
                    (rec["text"], remapped, json.dumps(meta)),
                )
                ids.append(cur.fetchone()[0])
        self.conn.commit()
        log.debug("pgvector: batch inserted %d rows", len(ids))
        return ids

    def search(
        self,
        query_text: str,
        query_vector: List[float],
        top_k: int = 5,
    ) -> List[Dict]:
        """Re-map *query_vector* then do cosine similarity search."""
        remapped = self.patch.remap_vector(query_vector, query_text)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, text, metadata,
                       1 - (vector <=> %s::vector) AS score
                FROM {self.table}
                ORDER BY vector <=> %s::vector
                LIMIT %s
                """,
                (remapped, remapped, top_k),
            )
            rows = cur.fetchall()
        return [
            {"id": r[0], "text": r[1], "metadata": r[2], "score": float(r[3])}
            for r in rows
        ]
