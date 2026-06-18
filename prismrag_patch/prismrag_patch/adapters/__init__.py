"""
Adapter registry for prismrag-patch.

Each adapter wraps a specific vector database and applies PrismRAG
Tier-1 re-mapping on insert and search automatically.

Available adapters
------------------
- pgvector  : prismrag_patch.adapters.pgvector.PgvectorAdapter
- chroma    : prismrag_patch.adapters.chroma.ChromaAdapter
- pinecone  : prismrag_patch.adapters.pinecone.PineconeAdapter
- weaviate  : prismrag_patch.adapters.weaviate.WeaviateAdapter
"""
