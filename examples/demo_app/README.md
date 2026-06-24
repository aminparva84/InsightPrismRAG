# PrismRAG demo app

Minimal example project that installs **`prismrag-patch` from PyPI** and verifies ingest, graph RAG search, communities, append, and quality scoring.

## Quick start

```powershell
cd examples\demo_app
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run interactive demo (writes logs/demo_<timestamp>.log)
python demo.py

# Run demo + all tests with full event log
python run_verification.py

# Run integration tests only
pytest test_integration.py -v
```

## What it does

1. Defines a small healthcare taxonomy (`mapping.py`)
2. Ingests inline records with `PrismRAG.ingest()`
3. Runs graph RAG `search()` with category enforcement
4. Lists Louvain `communities()`, optional `create_bridge()`
5. `append_chunks()` + `chunk_quality()` + `export_chunks()`

No database, API key, or license required — uses in-memory `MemoryStore` and deterministic embeddings.

## Use local package instead of PyPI

While developing the library in this repo:

```powershell
pip install -e ..\..\prismrag_patch[graph]
pytest test_integration.py -v
```

## Expected output

`demo.py` should end with `Demo OK`. All `test_integration.py` tests should pass.
