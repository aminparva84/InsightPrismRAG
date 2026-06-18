"""
WeaviateAdapter — prismrag-patch adapter for Weaviate.

Requirements: pip install "prismrag-patch[weaviate]"
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from prismrag_patch.core import PrismRAGPatch

log = logging.getLogger(__name__)


class WeaviateAdapter:
    """
    Wraps a Weaviate v4 client collection with PrismRAG Tier-1 re-mapping.

    Parameters
    ----------
    patch : PrismRAGPatch
        Initialized PrismRAGPatch instance.
    collection :
        A Weaviate v4 ``Collection`` object (``client.collections.get("MyClass")``).
    """

    def __init__(self, patch: PrismRAGPatch, collection: Any) -> None:
        self.patch      = patch
        self.collection = collection

    def insert(
        self,
        text: str,
        vector: List[float],
        doc_id: Optional[str] = None,
        properties: Optional[Dict] = None,
    ) -> str:
        """Re-map *vector* then insert into the collection. Returns the UUID."""
        result   = self.patch.project(text, vector)
        remapped = result["vector"]
        doc_uuid = doc_id or str(uuid.uuid4())
        props    = {"text": text, **(properties or {})}
        if result["category"]:
            props["prismrag_category"] = result["category"].get("slug", "")
            props["prismrag_label"]    = result["category"].get("label", "")

        self.collection.data.insert(
            properties=props,
            vector=remapped,
            uuid=doc_uuid,
        )
        log.debug("weaviate: inserted uuid=%s category=%s", doc_uuid, result["category"])
        return doc_uuid

    def batch_insert(
        self,
        records: List[Dict],
    ) -> List[str]:
        """
        Insert multiple objects using Weaviate v4 batch context manager.

        Each record must have ``text`` and ``vector``; ``doc_id`` and ``properties`` are optional.
        Returns a list of UUID strings.
        """
        ids_out = []
        with self.collection.batch.dynamic() as batch:
            for rec in records:
                result   = self.patch.project(rec["text"], rec["vector"])
                doc_uuid = rec.get("doc_id") or str(uuid.uuid4())
                props    = {"text": rec["text"], **(rec.get("properties") or {})}
                if result["category"]:
                    props["prismrag_category"] = result["category"].get("slug", "")
                    props["prismrag_label"]    = result["category"].get("label", "")
                batch.add_object(properties=props, vector=result["vector"], uuid=doc_uuid)
                ids_out.append(doc_uuid)
        log.debug("weaviate: batch inserted %d objects", len(ids_out))
        return ids_out

    def search(
        self,
        query_text: str,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[Any] = None,
    ) -> List[Dict]:
        """Re-map *query_vector* then perform a near-vector search."""
        remapped = self.patch.remap_vector(query_vector, query_text)
        query = self.collection.query.near_vector(
            near_vector=remapped,
            limit=top_k,
            return_metadata=["score", "distance"],
        )
        if filters:
            query = self.collection.query.near_vector(
                near_vector=remapped,
                limit=top_k,
                filters=filters,
                return_metadata=["score", "distance"],
            )
        response = query  # weaviate v4: near_vector returns the response directly
        results  = []
        for obj in response.objects:
            results.append({
                "id":         str(obj.uuid),
                "text":       obj.properties.get("text", ""),
                "properties": obj.properties,
                "score":      obj.metadata.score if obj.metadata else None,
            })
        return results
