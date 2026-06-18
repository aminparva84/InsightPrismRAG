"""
prismrag-patch — hallucination-resistant retrieval for your vector database.

Quick start:
    from prismrag_patch import PrismRAGPatch
    patch = PrismRAGPatch(license_key="prlib_…", mapping=your_mapping)
"""
from prismrag_patch.core import PrismRAGPatch
from prismrag_patch.license import LicenseError, validate_license

__all__ = ["PrismRAGPatch", "LicenseError", "validate_license"]
__version__ = "0.1.0"
