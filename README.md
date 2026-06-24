# PrismRAG — pip-only OSS library

**PrismRAG** is a free, Apache-2.0 Python library on PyPI: [`prismrag-patch` **0.2.1**](https://pypi.org/project/prismrag-patch/0.2.1/) (published).

📄 **[INFO.md](INFO.md)** — full product overview, landing-page copy, FAQ, architecture summary.

The Azure SaaS deployment was retired. All core RAG features run locally or on your Postgres.

## Install

```bash
pip install "prismrag-patch[graph]"
```

**Example project:** [`examples/demo_app`](examples/demo_app) — install from PyPI, run demo + integration tests.

## Quick start

```python
from prismrag_patch import PrismRAG

rag = PrismRAG(mapping={"categories": [...], "rules": [...]}, tenant_id="demo")
rag.ingest(records=[{"word": "diabetes", "text": "diabetes management"}])
print(rag.search("insulin medication", top_k=5))
```

See [`prismrag_patch/README.md`](prismrag_patch/README.md) for Postgres store, adapters, and tests.

## Repo layout

| Path | Purpose |
|------|---------|
| `prismrag_patch/` | **PyPI package** — ship this |
| `prismrag/` | Legacy SaaS API (archived, not deployed) |
| `tests/test_lib_*.py` | Library parity tests |
| `.github/workflows/ci.yml` | Library CI + build |
| `.github/workflows/publish-pypi.yml` | PyPI publish on `v*` tag |

## Publish to PyPI

1. Add GitHub secret `PYPI_API_TOKEN` (pypi.org → Account settings → API tokens).
2. Tag and push:

```bash
git tag v0.2.1
git push origin v0.2.1
```

Or locally:

```bash
cd prismrag_patch
pip install build twine
python -m build
twine upload dist/*
```

## Azure teardown

SaaS infra lived in resource group `prismrag-rg`. To remove all Azure cost:

```powershell
az group delete --name prismrag-rg --yes --no-wait
```

See [`DOC/azure-teardown.md`](DOC/azure-teardown.md).

## License

Apache-2.0 — see [`prismrag_patch/LICENSE`](prismrag_patch/LICENSE).
