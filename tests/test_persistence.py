import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb


@pytest.mark.skipif(not os.getenv("RIA_TEST_DATABASE_URL"), reason="Requires migrated PostgreSQL")
def test_persistence_transaction():
    job, obligation = uuid4(), uuid4()
    source = {"regulator": "RBI", "source_url": f"https://rbi.org.in/{job}.pdf"}
    digest = "a" * 64
    citation = {"page": 1, "ref": "#/texts/0", "text": "Banks shall report.",
                "quote": "shall report", "start": 6, "end": 18}
    result = {"schema_version": "1.0", "document": source, "sha256": digest,
              "obligations": [{"obligation_id": str(obligation), "statement": "Report.",
                               "potential_change": {"area": "reporting"}, "citation": citation}]}
    with psycopg.connect(os.environ["RIA_TEST_DATABASE_URL"]) as conn:
        conn.execute("INSERT INTO regulatory.jobs(id,owner,status) VALUES(%s,%s,'processing')",
                     (job, "integration-test"))
        conn.execute("SELECT regulatory.record_verified_revision(%s,%s,%s,%s)",
                     (job, source["regulator"], source["source_url"], digest))
        conn.execute("UPDATE regulatory.jobs SET status='completed',result=%s WHERE id=%s", (Jsonb(result), job))
        row = conn.execute("""
            SELECT a.sha256, e.quote, r.sha256, j.status
            FROM regulatory.jobs j
            JOIN regulatory.artifact_versions a ON a.job_id=j.id
            JOIN regulatory.obligations o ON o.artifact_id=a.id
            JOIN regulatory.evidence_spans e ON e.obligation_id=o.id
            JOIN regulatory.document_revisions r ON r.id=e.revision_id
            WHERE j.id=%s
        """, (job,)).fetchone()
        assert row[1:] == ("shall report", digest, "completed")
        assert len(row[0]) == 64
        stored = conn.execute("""
            INSERT INTO regulatory.artifact_objects(artifact_id,bucket,object_path,sha256)
            SELECT id,'ria-artifacts',%s,sha256 FROM regulatory.artifact_versions WHERE job_id=%s
            RETURNING review_status
        """, (f"{job}/v1/{row[0]}.json", job)).fetchone()
        assert stored == ("pending",)

        failed, revised = uuid4(), uuid4()
        for other, owner, sha in [(failed, "integration-test", digest),
                                  (revised, "another-test-owner", "b" * 64)]:
            conn.execute("INSERT INTO regulatory.jobs(id,owner,status) VALUES(%s,%s,'processing')",
                         (other, owner))
            conn.execute("SELECT regulatory.record_verified_revision(%s,%s,%s,%s)",
                         (other, source["regulator"], source["source_url"], sha))
        conn.execute("UPDATE regulatory.jobs SET status='PARSER_TIMEOUT' WHERE id=%s", (failed,))
        assert conn.execute("""
            SELECT count(DISTINCT r.revision_id), count(DISTINCT j.tenant_id)
            FROM regulatory.job_revisions r JOIN regulatory.jobs j ON j.id=r.job_id
            WHERE j.id = ANY(%s)
        """, ([job, failed, revised],)).fetchone() == (2, 2)
        assert conn.execute("""
            SELECT j.failure_code, a.id FROM regulatory.jobs j
            LEFT JOIN regulatory.artifact_versions a ON a.job_id=j.id WHERE j.id=%s
        """, (failed,)).fetchone() == ("PARSER_TIMEOUT", None)
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                invalid = dict(result, sha256="b" * 64)
                invalid["obligations"][0]["citation"]["quote"] = "incorrect"
                conn.execute("UPDATE regulatory.jobs SET status='completed',result=%s WHERE id=%s",
                             (Jsonb(invalid), revised))
        assert conn.execute("SELECT status FROM regulatory.jobs WHERE id=%s", (revised,)).fetchone() == ("processing",)
        conn.rollback()
