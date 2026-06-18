"""
PineconeAdapter — prismrag-patch adapter for Pinecone.

Requirements: pip install "prismrag-patch[pinecone]"
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from prismrag_patch.core import PrismRAGPatch

log = logging.getLogger(__name__)


class PineconeAdapter:
    """
    Wraps a Pinecone Index with PrismRAG Tier-1 re-mapping.

    Parameters
    ----------
    patch : PrismRAGPatch
        Initialized PrismRAGPatch instance.
    index :
        A ``pinecone.Index`` (v3 client) object.
    namespace : str
        Pinecone namespace (default ``""``).
    """

    def __init__(
        self,
        patch: PrismRAGPatch,
        index: Any,
        namespace: str = "",
    ) -> None:
        self.patch     = patch
        self.index     = index
        self.namespace = namespace

    def upsert(
        self,
        text: str,
        vector: List[float],
        doc_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """Re-map *vector* then upsert into the index. Returns the vector id."""
        result   = self.patch.project(text, vector)
        remapped = result["vector"]
        doc_id   = doc_id or str(uuid.uuid4())
        meta     = {"text": text, **(metadata or {})}
        if result["category"]:
            meta["prismrag_category"] = result["category"].get("slug", "")
            meta["prismrag_label"]    = result["category"].get("label", "")

        self.index.upsert(
            vectors=[{"id": doc_id, "values": remapped, "metadata": meta}],
            namespace=self.namespace,
        )
        log.debug("pinecone: upserted id=%s category=%s", doc_id, result["category"])
        return doc_id

    # Alias so the API matches the other adapters
    insert = upsert

    def batch_insert(
        self,
        records: List[Dict],
    ) -> List[str]:
        """
        Upsert multiple records in one Pinecone call.

        Each record must have ``text`` and ``vector``; ``doc_id``, ``metadata``, and ``namespace`` are optional.
        Returns a list of vector IDs.
        """
        vectors = []
        ids_out = []
        for rec in records:
            result   = self.patch.project(rec["text"], rec["vector"])
            doc_id   = rec.get("doc_id") or str(uuid.uuid4())
            meta     = {"text": rec["text"], **(rec.get("metadata") or {})}
            if result["category"]:
                meta["prismrag_category"] = result["category"].get("slug", "")
                meta["prismrag_label"]    = result["category"].get("label", "")
            vectors.append({"id": doc_id, "values": result["vector"], "metadata": meta})
            ids_out.append(doc_id)

        self.index.upsert(vectors=vectors, namespace=self.namespace)
        log.debug("pinecone: batch upserted %d vectors", len(ids_out))
        return ids_out

    def search(
        self,
        query_text: str,
        query_vector: List[float],
        top_k: int = 5,
        filter: Optional[Dict] = None,
    ) -> List[Dict]:
        """Re-map *query_vector* then query the index."""
        remapped = self.patch.remap_vector(query_vector, query_text)
        kwargs: Dict[str, Any] = {
            "vector": remapped,
            "top_k": top_k,
            "include_metadata": True,
            "namespace": self.namespace,
        }
        if filter:
            kwargs["filter"] = filter

        res = self.index.query(**kwargs)
        return [
            {
                "id":       m.id,
                "text":     m.metadata.get("text", ""),
                "metadata": m.metadata,
                "score":    float(m.score),
            }
            for m in res.matches
        ]
