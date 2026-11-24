import hashlib
from uuid import uuid4
from unittest.mock import MagicMock

import httpx
import pytest

from agent import service
from agent.artifact_storage import ArtifactStorageError, upload_artifact


def test_private_upload_uses_content_address_and_disables_overwrite(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_STORAGE_KEY", "test-key")
    data = b'{"obligations": []}'
    digest = hashlib.sha256(data).hexdigest()
    job = uuid4()

    def respond(request):
        assert request.method == "POST"
        assert request.url.path == f"/storage/v1/object/ria-artifacts/{job}/v1/{digest}.json"
        assert request.headers["x-upsert"] == "false"
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.content == data
        return httpx.Response(200)

    assert upload_artifact(job, 1, data, transport=httpx.MockTransport(respond)) == (
        "ria-artifacts", f"{job}/v1/{digest}.json", digest,
    )


def test_storage_failure_does_not_commit_completed_job(monkeypatch):
    completing = MagicMock()
    failing = MagicMock()
    conn = completing.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = (uuid4(), 1, b"{}")
    contexts = iter([completing, failing])
    monkeypatch.setattr(service, "db", lambda: next(contexts))

    def fail(*_):
        raise ArtifactStorageError()

    monkeypatch.setattr(service, "upload_artifact", fail)
    assert service.finish_job(uuid4(), "completed", {"obligations": []}) == (
        "ARTIFACT_STORAGE_FAILED", None,
    )
    assert completing.__exit__.call_args.args[0] is ArtifactStorageError
    assert failing.__enter__.return_value.execute.call_args.args[1][1] == "ARTIFACT_STORAGE_FAILED"


def test_missing_storage_key_does_not_attempt_upload(monkeypatch):
    monkeypatch.delenv("SUPABASE_STORAGE_KEY", raising=False)
    with pytest.raises(ArtifactStorageError, match="not configured"):
        upload_artifact(uuid4(), 1, b"{}")
