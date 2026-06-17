"""PrismRAG — Customer-managed encryption keys (Azure Key Vault)."""
from __future__ import annotations

import os
from typing import Any


def configure_cmek(
    organization_id: str,
    vault_url: str,
    key_name: str,
) -> dict[str, Any]:
    """
    Register CMEK for an organization. Actual encryption uses Azure Key Vault
    wrap/unwrap when PRISMRAG_CMEK_ENABLED=true.
    """
    from prismrag.db import get_conn, release_conn

    key_id = f"{vault_url.rstrip('/')}/keys/{key_name}"
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE prismrag.organization
            SET cmek_enabled = TRUE, cmek_key_id = %s, cmek_vault_url = %s, updated_at = now()
            WHERE id = %s
            """,
            (key_id, vault_url, organization_id),
        )
        if cur.rowcount == 0:
            raise ValueError("Organization not found")
        conn.commit()
    finally:
        release_conn(conn)

    return {
        "enabled": True,
        "key_id": key_id,
        "vault_url": vault_url,
        "provider": "azure_key_vault",
    }


def cmek_enabled_globally() -> bool:
    return os.getenv("PRISMRAG_CMEK_ENABLED", "").lower() in ("1", "true", "yes")


def wrap_dek(dek: bytes, key_id: str) -> bytes:
    """Wrap a data encryption key with the customer KEK (requires azure-identity + azure-keyvault-keys)."""
    if not cmek_enabled_globally():
        return dek
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm
    except ImportError as exc:
        raise RuntimeError("azure-identity and azure-keyvault-keys required for CMEK") from exc

    credential = DefaultAzureCredential()
    client = CryptographyClient(key_id, credential)
    result = client.encrypt(EncryptionAlgorithm.rsa_oaep_256, dek)
    return result.ciphertext
