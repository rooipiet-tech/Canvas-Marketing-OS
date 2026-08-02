"""analytics_ingest.blob_writer — uploads the validated Fabric export to blob storage.

STORAGE_ACCOUNT_NAME / BLOB_CONTAINER_NAME are read from os.environ only —
no hardcoded storage FQDN anywhere in this module (AC-30). Authenticates
via managed identity (DefaultAzureCredential) — no account key.
"""

from __future__ import annotations

import json
import os
from typing import Any


def _blob_service_url() -> str:
    account_name = os.environ["STORAGE_ACCOUNT_NAME"]
    return f"https://{account_name}.blob.core.windows.net"


def _container_name() -> str:
    return os.environ.get("BLOB_CONTAINER_NAME", "analytics-fabric-export")


async def upload(export_json: dict[str, Any], day: str) -> str:
    """Upload the validated Fabric export JSON for `day`. Returns the blob name."""
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient

    blob_name = f"{day}.json"
    credential = DefaultAzureCredential()
    try:
        async with BlobServiceClient(
            account_url=_blob_service_url(), credential=credential
        ) as service_client:
            container_client = service_client.get_container_client(_container_name())
            blob_client = container_client.get_blob_client(blob_name)
            await blob_client.upload_blob(
                json.dumps(export_json, sort_keys=True),
                overwrite=True,
                content_type="application/json",
            )
    finally:
        await credential.close()

    return blob_name
