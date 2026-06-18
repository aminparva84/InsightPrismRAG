"""
ChromaAdapter — prismrag-patch adapter for ChromaDB.

Requirements: pip install "prismrag-patch[chroma]"
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from prismrag_patch.core import PrismRAGPatch

log = logging.getLogger(__name__)


class ChromaAdapter:
    """
    Wraps a ChromaDB collection with PrismRAG Tier-1 re-mapping.

    Parameters
    ----------
    patch : PrismRAGPatch
        Initialized PrismRAGPatch instance.
    collection :
        A ``chromadb.Collection`` (or compatible) object.
    """

    def __init__(self, patch: PrismRAGPatch, collection: Any) -> None:
        self.patch      = patch
        self.collection = collection

    def insert(
        self,
        text: str,
        vector: List[float],
        doc_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """Re-map *vector* then add to the collection. Returns the document id."""
        result   = self.patch.project(text, vector)
        remapped = result["vector"]
        doc_id   = doc_id or str(uuid.uuid4())
        meta     = {**(metadata or {})}
        if result["category"]:
            meta["prismrag_category"] = result["category"].get("slug", "")
            meta["prismrag_label"]    = result["category"].get("label", "")

        self.collection.add(
            ids=[doc_id],
            embeddings=[remapped],
            documents=[text],
            metadatas=[meta],
        )
        log.debug("chroma: inserted id=%s category=%s", doc_id, result["category"])
        return doc_id

    def batch_insert(
        self,
        records: List[Dict],
    ) -> List[str]:
        """
        Insert multiple records in one ChromaDB call.

        Each record must have ``text`` and ``vector``; ``doc_id`` and ``metadata`` are optional.
        Returns a list of document IDs.
        """
        ids_out, embeddings, documents, metadatas = [], [], [], []
        for rec in records:
            result   = self.patch.project(rec["text"], rec["vector"])
            doc_id   = rec.get("doc_id") or str(uuid.uuid4())
            meta     = {**(rec.get("metadata") or {})}
            if result["category"]:
                meta["prismrag_category"] = result["category"].get("slug", "")
                meta["prismrag_label"]    = result["category"].get("label", "")
            ids_out.append(doc_id)
            embeddings.append(result["vector"])
            documents.append(rec["text"])
            metadatas.append(meta)

        self.collection.add(ids=ids_out, embeddings=embeddings, documents=documents, metadatas=metadatas)
        log.debug("chroma: batch inserted %d docs", len(ids_out))
        return ids_out

    def search(
        self,
        query_text: str,
        query_vector: List[float],
        top_k: int = 5,
        where: Optional[Dict] = None,
    ) -> List[Dict]:
        """Re-map *query_vector* then query the collection."""
        remapped = self.patch.remap_vector(query_vector, query_text)
        kwargs: Dict[str, Any] = {
            "query_embeddings": [remapped],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        res = self.collection.query(**kwargs)
        results = []
        for i, doc_id in enumerate(res["ids"][0]):
            results.append({
                "id":       doc_id,
                "text":     res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "score":    1.0 - float(res["distances"][0][i]),
            })
        return results
