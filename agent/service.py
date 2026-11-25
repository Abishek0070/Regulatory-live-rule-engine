import json
import os
import subprocess
import sys
from pathlib import Path
from time import sleep
from typing import Literal, TypedDict
from uuid import UUID, uuid4
import httpx
import psycopg
from psycopg.types.json import Jsonb
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from langgraph.graph import StateGraph, START, END
from . import security
from .artifact_storage import ArtifactStorageError, upload_artifact
from .clause_extraction import NeedsReview, run
from .context import encode

app=FastAPI(title="Regulatory Integration Agent",version="1.0")
console_dir = Path(__file__).with_name("console")
app.mount("/console", StaticFiles(directory=console_dir), name="console")


@app.get("/", include_in_schema=False)
def console():
    return FileResponse(
        console_dir / "index.html",
        headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
        },
    )


class Source(BaseModel):
    regulator:Literal["RBI","SEBI"]
    source_url:str=Field(max_length=2048)
    expected_sha256:str|None=Field(default=None,pattern="^[0-9a-f]{64}$")

JOB_OWNER = "local-console"


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403)


def db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(503, "PostgreSQL is not configured. Set DATABASE_URL and start the agent stack.")
    try:
        return psycopg.connect(url, connect_timeout=5)
    except psycopg.OperationalError:
        raise HTTPException(503, "Could not connect to PostgreSQL. Check the agent's database connection.") from None

def event(job,stage):
    with db() as conn:
        conn.execute("INSERT INTO regulatory.events(job,stage) VALUES(%s,%s)",(job,stage))

class State(TypedDict,total=False):
    job:UUID
    source:Source
    upload:bytes|None
    data:bytes
    digest:str
    scanner:str
    blocks:list
    audit:dict
    obligations:list

def record_revision(job, source, digest):
    with db() as conn:
        conn.execute("SELECT regulatory.record_verified_revision(%s,%s,%s,%s)",
                     (job, source.regulator, source.source_url, digest))


def verify(s):
    event(s["job"],"verification")
    src=s["source"]
    data=security.retrieve(src.regulator,src.source_url)
    digest = security.verify(data, s["upload"], src.expected_sha256)
    record_revision(s["job"], src, digest)
    return {"data": data, "digest": digest}

def scan(s):
    event(s["job"],"scan")
    return {"scanner":security.scan(s["data"])}

class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    l: float
    t: float
    r: float
    b: float
    coord_origin: Literal["TOPLEFT", "BOTTOMLEFT"]

class ParsedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ref: str = Field(pattern=r"^#/[a-z_]+/[0-9]+(/span/[0-9]+)?$")
    page: int = Field(ge=1, le=250)
    heading: str | None
    kind: str = Field(min_length=1)
    text: str = Field(min_length=1)
    bbox: BoundingBox

parsed_blocks = TypeAdapter(list[ParsedBlock])

def validate_blocks(payload):
    try:
        blocks = parsed_blocks.validate_python(payload, strict=True)
    except ValidationError as exc:
        raise ValueError("REVIEW_REQUIRED") from exc
    if not blocks or any(not block.text.strip() for block in blocks):
        raise ValueError("REVIEW_REQUIRED")
    if len({block.ref for block in blocks}) != len(blocks):
        raise ValueError("REVIEW_REQUIRED")
    return [block.model_dump() for block in blocks]

def parse(s):
    event(s["job"],"parse")
    if not os.environ.get("PARSER_SOCKET"):
        env = {key: os.environ[key] for key in (
            "SYSTEMROOT", "PATH", "TEMP", "TMP", "LOCALAPPDATA", "USERPROFILE",
        ) if key in os.environ}
        try:
            parsed = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(__file__).with_name("pdfplumber_parser.py"))],
                input=s["data"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, timeout=480, check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("PARSER_TIMEOUT") from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("REVIEW_REQUIRED") from exc
        if len(parsed.stdout) > 800000:
            raise ValueError("REVIEW_REQUIRED")
        return {"blocks": validate_blocks(json.loads(parsed.stdout))}
    transport=httpx.HTTPTransport(uds=os.environ["PARSER_SOCKET"])
    try:
        with httpx.Client(transport=transport,timeout=180) as client:
            r=client.post("http://parser/parse",content=s["data"])
    except httpx.ConnectError as exc:
        raise ValueError("PARSER_UNAVAILABLE") from exc
    except RuntimeError as exc:
        if str(exc) != "Attempted to connect to a UNIX socket on a Windows system.":
            raise
        raise ValueError("PARSER_UNAVAILABLE") from exc
    if r.status_code == 422:
        raise ValueError("REVIEW_REQUIRED")
    r.raise_for_status()
    if len(r.content)>800000:
        raise ValueError("REVIEW_REQUIRED")
    return {"blocks":validate_blocks(r.json())}

def request_extraction(client, endpoint, headers, payload):
    for attempt in range(3):
        response = client.post(endpoint, headers=headers, json=payload)
        if response.status_code == 400:
            try:
                code = response.json()["error"]["code"]
            except (ValueError, KeyError, TypeError):
                code = None
            if code == "json_validate_failed":
                if attempt == 2:
                    raise ExtractionReview("MODEL_INVALID_OUTPUT")
                payload = {**payload, "messages": [*payload["messages"], {
                    "role": "user", "content": "Return one JSON object following the system response schema exactly."
                }]}
                continue
        if response.status_code != 429:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After", "")
        delay = int(retry_after) if retry_after.isascii() and retry_after.isdecimal() and len(retry_after) <= 8 else 30
        if attempt == 2 or delay > 60:
            raise ValueError("MODEL_RATE_LIMITED")
        sleep(max(1, delay))


class ExtractionReview(ValueError):
    def __init__(self, reason):
        super().__init__("REVIEW_REQUIRED")
        self.reason = reason


def chunk_checkpoint(job, key, value):
    with db() as conn:
        if value is None:
            row = conn.execute("SELECT response FROM regulatory.extraction_chunks WHERE job_id=%s AND chunk_key=%s",
                               (job,key)).fetchone()
            return row[0] if row else None
        conn.execute("INSERT INTO regulatory.extraction_chunks(job_id,chunk_key,response) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                     (job,key,value))
        return conn.execute("SELECT response FROM regulatory.extraction_chunks WHERE job_id=%s AND chunk_key=%s", (job,key)).fetchone()[0]


def extract(s):
    event(s["job"], "extract")
    model = os.environ["LLM_MODEL"]
    with httpx.Client(timeout=60, trust_env=False) as client:
        def call(prompt, entries, limit):
            options = {"reasoning_effort":"none"} if model == "qwen/qwen3.8-27b" else {}
            response = request_extraction(client, os.environ["LLM_ENDPOINT"],
                {"Authorization":"Bearer "+os.environ["LLM_KEY"]},
                {"model":model, **options, "temperature":0, "max_completion_tokens":limit,
                 "response_format":{"type":"json_object"}, "messages":[
                    {"role":"system","content":prompt},
                    {"role":"user","content":encode(entries)}]})
            try:
                choice = response.json()["choices"][0]
                if choice.get("finish_reason","stop") != "stop":
                    return ""
                return choice["message"]["content"] or ""
            except (KeyError, IndexError, TypeError, ValueError):
                return ""
        try:
            return run(s["blocks"],s["digest"],s["source"].regulator,model,call,
                       lambda key,value: chunk_checkpoint(s["job"],key,value))
        except NeedsReview as exc:
            raise ExtractionReview(str(exc)) from exc

graph=StateGraph(State)
prior=START
for node in [verify,scan,parse,extract]:
    graph.add_node(node.__name__,node)
    graph.add_edge(prior,node.__name__)
    prior=node.__name__
graph.add_edge(prior,END)
pipeline=graph.compile()

def write_final_status(conn, job, status, result):
    conn.execute("UPDATE regulatory.jobs SET status=%s,result=%s WHERE id=%s",
                 (status, Jsonb(result), job))
    conn.execute("INSERT INTO regulatory.events(job,stage) VALUES(%s,%s)", (job, status))


def finish_job(job, status, result):
    try:
        with db() as conn:
            write_final_status(conn, job, status, result)
            if status == "completed":
                artifact_id, version, data = conn.execute("""
                    SELECT id, version, convert_to(payload::text, 'UTF8')
                    FROM regulatory.artifact_versions WHERE job_id=%s
                """, (job,)).fetchone()
                bucket, path, digest = upload_artifact(job, version, data)
                conn.execute("""
                    INSERT INTO regulatory.artifact_objects(artifact_id,bucket,object_path,sha256)
                    VALUES(%s,%s,%s,%s)
                """, (artifact_id, bucket, path, digest))
    except ArtifactStorageError:
        status, result = "ARTIFACT_STORAGE_FAILED", None
        with db() as conn:
            write_final_status(conn, job, status, result)
    return status, result


def process(source,upload,identity):
    job=uuid4()
    with db() as c:
        c.execute(
            "INSERT INTO regulatory.jobs(id,owner,status,source_regulator,source_url) "
            "VALUES(%s,%s,'processing',%s,%s)",
            (job, identity, source.regulator, source.source_url),
        )
        c.execute("INSERT INTO regulatory.events(job,stage) VALUES(%s,'intake')",(job,))
    result=None
    review_reason = None
    try:
        s=pipeline.invoke({"job":job,"source":source,"upload":upload})
        result={"schema_version":"1.1","job_id":str(job),"document":source.model_dump(),
                "sha256":s["digest"],"scanner":s["scanner"],"model":os.environ["LLM_MODEL"],
                "obligations":s["obligations"],"audit":s["audit"]}
        status="completed"
    except HTTPException:
        raise
    except ExtractionReview as exc:
        status = "REVIEW_REQUIRED"
        review_reason = exc.reason
        event(job, review_reason)
    except ValueError as exc:
        allowed={"REVIEW_REQUIRED","INVALID_EVIDENCE","OFFICIAL_SOURCE_UNVERIFIED","UNSAFE_ADDRESS",
                 "HASH_MISMATCH","UNSUPPORTED_DOCUMENT","MALWARE_DETECTED","SCAN_INCONCLUSIVE",
                 "PARSER_UNAVAILABLE","PARSER_TIMEOUT","MODEL_RATE_LIMITED"}
        status=str(exc) if str(exc) in allowed else "INCONCLUSIVE"
    except Exception:
        status="INCONCLUSIVE"
    status, result = finish_job(job, status, result)
    response = {"job_id":job,"status":status,"result":result}
    if review_reason:
        response["review_reason"] = review_reason
    return response

@app.post("/v1/jobs/url", dependencies=[Depends(check_origin)])
def from_url(source:Source):
    return process(source,None,JOB_OWNER)

@app.post("/v1/jobs/upload", dependencies=[Depends(check_origin)])
async def upload(request:Request,regulator:Literal["RBI","SEBI"],source_url:str,expected_sha256:str|None=None):
    from starlette.concurrency import run_in_threadpool
    try:
        source=Source(regulator=regulator,source_url=source_url,expected_sha256=expected_sha256)
    except ValueError:
        raise HTTPException(422)
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>security.LIMIT:
            raise HTTPException(413)
        data.extend(chunk)
    return await run_in_threadpool(process,source,bytes(data),JOB_OWNER)

@app.get("/v1/jobs/{job}")
def read(job:UUID):
    with db() as c:
        row=c.execute("""
            SELECT j.status, j.result, (
                SELECT e.stage FROM regulatory.events e
                WHERE e.job=j.id AND e.stage IN ('MODEL_FLAGGED_AMBIGUITY', 'MODEL_FOUND_NO_OBLIGATIONS', 'MODEL_INVALID_OUTPUT', 'UNCERTAIN_COVERAGE', 'UNRESOLVED_CONTEXT', 'CONFLICTING_EXTRACTION', 'MODEL_BUDGET_INVALID', 'INVALID_EVIDENCE')
                ORDER BY e.id DESC LIMIT 1
            )
            FROM regulatory.jobs j WHERE j.id=%s AND j.owner=%s
        """,(job,JOB_OWNER)).fetchone()
    if row is None:
        raise HTTPException(404)
    response = {"job_id":job,"status":row[0],"result":row[1]}
    if row[2]:
        response["review_reason"] = row[2]
    return response
