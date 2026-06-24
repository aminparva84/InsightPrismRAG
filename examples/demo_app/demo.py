#!/usr/bin/env python3
"""Runnable demo — ingest, search, communities, append (uses prismrag-patch from PyPI)."""
from __future__ import annotations

import logging
import sys
from typing import Optional

from mapping import DEMO_MAPPING, demo_records
from prismrag_patch import PrismRAG

from event_log import init_event_log, log_event


def main(logger: Optional[logging.Logger] = None) -> int:
    own_logger = logger is None
    if own_logger:
        logger, _ = init_event_log("demo")

    log_event(logger, "session.init", tenant_id="demo-app", rule_count=len(DEMO_MAPPING["rules"]))
    print("PrismRAG demo — prismrag-patch from PyPI\n")

    rag = PrismRAG(mapping=DEMO_MAPPING, tenant_id="demo-app")
    log_event(logger, "client.created", store="MemoryStore")

    records = demo_records()
    log_event(logger, "ingest.request", record_count=len(records), records=records)
    job = rag.ingest(records=records)
    log_event(
        logger,
        "ingest.complete",
        job_id=job.get("job_id"),
        status=job["status"],
        records_written=job["records_written"],
        community_count=job.get("community_count"),
        edge_count=job.get("edge_count"),
    )

    print("Ingest job:")
    print(
        f"  status={job['status']} records={job['records_written']} "
        f"communities={job.get('community_count', 0)} edges={job.get('edge_count', 0)}"
    )

    query = "What medications are used for diabetes management?"
    log_event(logger, "search.request", query=query, top_k=3)
    search = rag.search(query, top_k=3)
    log_event(
        logger,
        "search.complete",
        mode=search["retrieval_mode"],
        hit_count=len(search["results"]),
        results=[
            {
                "rank": i,
                "category": h.get("category_slug"),
                "ref": h.get("chunk_ref"),
                "score": h.get("score"),
            }
            for i, h in enumerate(search["results"], 1)
        ],
    )

    print(f"\nSearch: {query!r}")
    print(f"  mode={search['retrieval_mode']} hits={len(search['results'])}")
    for i, hit in enumerate(search["results"], 1):
        print(
            f"  {i}. [{hit['category_slug']}] {hit.get('chunk_ref', '')} "
            f"score={hit.get('score', 0):.4f}"
        )

    comms = rag.list_communities()
    log_event(
        logger,
        "communities.list",
        count=len(comms),
        communities=[
            {
                "id": c.get("community_id"),
                "label": c.get("label"),
                "top_words": c.get("top_words", [])[:5],
            }
            for c in comms
        ],
    )
    print(f"\nCommunities: {len(comms)}")
    for c in comms[:3]:
        print(f"  - {c.get('label', c['community_id'])} ({len(c.get('top_words', []))} words)")

    if len(comms) >= 2:
        log_event(
            logger,
            "bridge.request",
            community_a=comms[0]["community_id"],
            community_b=comms[1]["community_id"],
        )
        bridge = rag.create_bridge(
            comms[0]["community_id"],
            comms[1]["community_id"],
            bridge_label="demo-bridge",
        )
        log_event(logger, "bridge.complete", bridge=bridge)
        print(f"\nBridge created: {bridge.get('bridge_id', bridge)}")

    log_event(
        logger,
        "append.request",
        chunk_ref="nausea",
        category="symptoms",
    )
    append = rag.append_chunks(
        chunks=[{"ref": "nausea", "text": "patient reports nausea after medication"}],
        new_rules=[{"word": "nausea", "category_slug": "symptoms", "weight": 1.0}],
    )
    log_event(logger, "append.complete", results=append)
    print(f"\nAppend: ref={append[0]['chunk_ref']} quality={append[0]['quality_score']:.3f}")

    quality = rag.chunk_quality()
    log_event(logger, "quality.report", summary=quality["summary"])
    print(
        f"Quality summary: total={quality['summary']['total']} "
        f"avg={quality['summary']['avg_quality']:.3f}"
    )

    chunks = rag.export_chunks()
    log_event(
        logger,
        "export.chunks",
        count=len(chunks),
        sample_refs=[c.get("chunk_ref") for c in chunks[:5]],
    )
    print(f"Exported chunks: {len(chunks)} (256-d + 768-d vectors each)")

    log_event(logger, "session.complete", status="OK")
    print("\nDemo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
