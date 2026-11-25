from uuid import uuid4
import hashlib
import json
import httpx
import pytest
from agent import service


def test_model_retries_rate_limited_batch(monkeypatch):
    calls, delays = [], []
    monkeypatch.setattr(service, "sleep", delays.append)

    def respond(request):
        calls.append(request.content)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "19"})
        return httpx.Response(200, json={"choices": []})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reply = service.request_extraction(client, "https://model.example", {}, {"model": "test"})
    assert reply.status_code == 200
    assert len(calls) == 2 and calls[0] == calls[1]
    assert delays == [19]


def test_local_parser_timeout_has_its_own_status(monkeypatch):
    monkeypatch.delenv("PARSER_SOCKET", raising=False)
    monkeypatch.setattr(service, "event", lambda *_: None)

    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 480
        raise service.subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(service.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="^PARSER_TIMEOUT$"):
        service.parse({"job": uuid4(), "data": b"%PDF-test"})


def test_local_parser_uses_credential_free_subprocess(monkeypatch):
    monkeypatch.delenv("PARSER_SOCKET", raising=False)
    monkeypatch.setenv("DATABASE_URL", "private")
    monkeypatch.setenv("LLM_KEY", "private")
    monkeypatch.setattr(service, "event", lambda *_: None)
    block = {"page": 1, "ref": "#/texts/0", "heading": None, "kind": "text",
             "text": "Banks shall report.",
             "bbox": {"l": 0, "t": 0, "r": 10, "b": 10, "coord_origin": "TOPLEFT"}}

    def run(args, **kwargs):
        assert service.Path(args[-1]).name == "pdfplumber_parser.py"
        assert kwargs["input"] == b"%PDF-test"
        assert "DATABASE_URL" not in kwargs["env"]
        assert "LLM_KEY" not in kwargs["env"]
        return service.subprocess.CompletedProcess(args, 0, json.dumps([block]).encode())

    monkeypatch.setattr(service.subprocess, "run", run)
    assert service.parse({"job": uuid4(), "data": b"%PDF-test"}) == {"blocks": [block]}


@pytest.mark.parametrize("error", [
    httpx.ConnectError("socket unavailable"),
    RuntimeError("Attempted to connect to a UNIX socket on a Windows system."),
])
def test_unavailable_parser_reports_specific_status(monkeypatch, error):
    monkeypatch.setenv("PARSER_SOCKET", "/ipc/parser.sock")
    monkeypatch.setattr(service, "event", lambda *_: None)

    def unavailable(request):
        raise error

    client = httpx.Client
    monkeypatch.setattr(service.httpx, "Client",
                        lambda **_: client(transport=httpx.MockTransport(unavailable)))
    with pytest.raises(ValueError, match="^PARSER_UNAVAILABLE$"):
        service.parse({"job": uuid4(), "data": b"%PDF-1.7"})

@pytest.mark.parametrize("blocks", [
    {"text": "Banks shall report."},
    [{"ref": "#/texts/0", "page": "1", "heading": None, "kind": "text",
      "text": "Banks shall report.", "bbox": {}}],
])
def test_invalid_parser_output_requires_review(blocks):
    with pytest.raises(ValueError,match="REVIEW_REQUIRED"):
        service.validate_blocks(blocks)

def test_failed_scan_never_reaches_parser(monkeypatch):
    stages=[]
    monkeypatch.setattr(service,"event",lambda _,stage:stages.append(stage))
    monkeypatch.setattr(service.security,"retrieve",lambda *_:b"%PDF-1.7")
    revisions = []
    monkeypatch.setattr(service, "record_revision", lambda *args: revisions.append(args))
    def deny(_):
        raise ValueError("SCAN_INCONCLUSIVE")
    monkeypatch.setattr(service.security,"scan",deny)
    with pytest.raises(ValueError,match="SCAN_INCONCLUSIVE"):
        service.pipeline.invoke({"job":uuid4(),"source":service.Source(regulator="RBI",source_url="https://rbi.org.in/a.pdf"),"upload":None})
    assert stages==["verification","scan"]
    assert len(revisions) == 1

def test_verified_document_produces_revision_bound_evidence(monkeypatch):
    data = b"%PDF-1.7"
    block = {
        "ref": "#/texts/0", "page": 1, "heading": None, "kind": "text",
        "text": "Banks shall report.",
        "bbox": {"l": 0, "t": 0, "r": 10, "b": 10, "coord_origin": "TOPLEFT"},
    }
    stages = []
    monkeypatch.setenv("PARSER_SOCKET", "/ipc/parser.sock")
    monkeypatch.setattr(service, "event", lambda _, stage: stages.append(stage))
    monkeypatch.setattr(service.security, "retrieve", lambda *_: data)
    monkeypatch.setattr(service, "record_revision", lambda *_: None)
    monkeypatch.setattr(service.security, "scan", lambda _: "ClamAV test")
    monkeypatch.setenv("LLM_ENDPOINT", "https://model.example/extract")
    monkeypatch.setenv("LLM_KEY", "test-model-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def respond(request):
        if request.url.host == "parser":
            assert request.content == data
            assert "Authorization" not in request.headers
            return httpx.Response(200, json=[block])
        assert request.headers["Authorization"] == "Bearer test-model-key"
        selection = {"ambiguous": False, "picks": [
            {"index": 0, "quote": block["text"], "area": "reporting"},
        ]}
        return httpx.Response(200, json={"choices": [
            {"message": {"content": json.dumps(selection)}},
        ]})

    client = httpx.Client
    monkeypatch.setattr(service.httpx, "Client",
                        lambda **_: client(transport=httpx.MockTransport(respond)))
    source = service.Source(regulator="RBI", source_url="https://rbi.org.in/a.pdf")
    result = service.pipeline.invoke({"job": uuid4(), "source": source, "upload": data})
    assert stages == ["verification", "scan", "parse", "extract"]
    obligation, = result["obligations"]
    assert obligation["potential_change"]["area"] == "reporting"
    citation = obligation["citation"]
    assert citation["document_sha256"] == hashlib.sha256(data).hexdigest()
    assert citation["quote"] == block["text"]


@pytest.mark.parametrize("empty", [False, True])
def test_local_model_selection_binds_to_server_text(monkeypatch, empty):
    monkeypatch.delenv("PARSER_SOCKET", raising=False)
    monkeypatch.setenv("LLM_ENDPOINT", "https://model.example/extract")
    monkeypatch.setenv("LLM_KEY", "test")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(service, "event", lambda *_: None)
    block = {"page": 1, "ref": "#/texts/0", "text": "Banks shall report."}

    def respond(request):
        payload = json.loads(request.content)
        assert json.loads(payload["messages"][1]["content"])[0]["index"] == 0
        selection = {"ambiguous": False, "picks": [] if empty else [
            {"index": 0, "area": "reporting", "statement": "Submit the required reports."},
            {"index": 0, "area": "reporting", "statement": "File a separate return."},
        ]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(selection)}}]})

    client = httpx.Client
    monkeypatch.setattr(service.httpx, "Client",
                        lambda **_: client(transport=httpx.MockTransport(respond)))
    state = {"job": uuid4(), "blocks": [block], "digest": "a" * 64}
    if empty:
        with pytest.raises(ValueError, match="REVIEW_REQUIRED"):
            service.extract(state)
        return
    result = service.extract(state)
    assert result["obligations"][0]["citation"]["quote"] == block["text"]
    assert [item["statement"] for item in result["obligations"]] == [
        "Submit the required reports.", "File a separate return.",
    ]
    assert len({item["obligation_id"] for item in result["obligations"]}) == 2


def test_review_reason_is_audited_without_changing_status(monkeypatch):
    from unittest.mock import MagicMock
    db = MagicMock()
    monkeypatch.setattr(service, "db", db)
    def review(_):
        raise service.ExtractionReview("MODEL_FLAGGED_AMBIGUITY")
    monkeypatch.setattr(service.pipeline, "invoke", review)
    source = service.Source(regulator="RBI", source_url="https://rbi.org.in/source.pdf")
    result = service.process(source, None, "test-owner")
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["review_reason"] == "MODEL_FLAGGED_AMBIGUITY"
    calls = db.return_value.__enter__.return_value.execute.call_args_list
    assert any(call.args[1] == (result["job_id"], "MODEL_FLAGGED_AMBIGUITY") for call in calls)
