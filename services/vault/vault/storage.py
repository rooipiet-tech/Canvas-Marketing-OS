"""Content-addressed blob storage for assets (AC-005, AC-006).

Blob name = sha256 hex digest of the asset bytes. Re-uploading identical
bytes therefore always resolves to the same blob name — `exists()` is
checked before any write, so no duplicate blob is ever written for the
same content. Uses the container's managed identity (DefaultAzureCredential)
against the shared cmos-dev storage account's `vault-assets` container
(infra/modules/vault/blob-container.bicep) — no storage account key, no
connection string.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from .config import get_settings


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@lru_cache
def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


@lru_cache
def _blob_service_client() -> BlobServiceClient:
    settings = get_settings()
    if not settings.storage_account_name:
        raise RuntimeError("STORAGE_ACCOUNT_NAME is not configured")
    account_url = f"https://{settings.storage_account_name}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=_credential())


def storage_uri_for(digest: str) -> str:
    settings = get_settings()
    return (
        f"https://{settings.storage_account_name}.blob.core.windows.net/"
        f"{settings.blob_container_name}/{digest}"
    )


def store_content_addressed(data: bytes) -> tuple[str, str, bool]:
    """Uploads `data` if (and only if) no blob with its content hash
    already exists. Returns (content_hash, storage_uri, deduplicated)."""
    digest = sha256_hex(data)
    settings = get_settings()
    client = _blob_service_client().get_blob_client(
        container=settings.blob_container_name, blob=digest
    )
    if client.exists():
        return digest, storage_uri_for(digest), True
    client.upload_blob(data, overwrite=False)
    return digest, storage_uri_for(digest), False


def read_content(digest: str) -> bytes:
    settings = get_settings()
    client = _blob_service_client().get_blob_client(
        container=settings.blob_container_name, blob=digest
    )
    return client.download_blob().readall()


def delete_content_if_unreferenced(digest: str, *, still_referenced: bool) -> None:
    """Deletes the blob for `digest` unless another live asset still
    references the same content hash (dedup-safe deletion for the
    retention-expiry job — see vault/retention.py)."""
    if still_referenced:
        return
    settings = get_settings()
    client = _blob_service_client().get_blob_client(
        container=settings.blob_container_name, blob=digest
    )
    if client.exists():
        client.delete_blob()
