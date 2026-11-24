import hashlib
import os
from urllib.parse import urlsplit

import httpx


BUCKET = "ria-artifacts"


class ArtifactStorageError(Exception):
    pass


def upload_artifact(job_id, version, data, *, transport=None):
    base_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_STORAGE_KEY")
    origin = urlsplit(base_url)
    if not key or origin.scheme != "https" or not origin.hostname or origin.username or origin.path or origin.query or origin.fragment:
        raise ArtifactStorageError("Supabase Storage is not configured")

    digest = hashlib.sha256(data).hexdigest()
    path = f"{job_id}/v{version}/{digest}.json"
    url = f"{base_url}/storage/v1/object/{BUCKET}/{path}"
    headers = {"Authorization": f"Bearer {key}", "apikey": key}
    try:
        with httpx.Client(timeout=30, trust_env=False, transport=transport) as client:
            response = client.post(url, content=data, headers={
                **headers, "Content-Type": "application/json", "x-upsert": "false",
            })
            if response.status_code in (400, 409):
                existing = client.get(f"{base_url}/storage/v1/object/authenticated/{BUCKET}/{path}",
                                      headers=headers)
                if existing.is_success and existing.content == data:
                    return BUCKET, path, digest
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ArtifactStorageError("Could not store the extraction artifact") from exc
    return BUCKET, path, digest
