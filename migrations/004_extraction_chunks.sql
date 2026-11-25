BEGIN;
CREATE TABLE regulatory.extraction_chunks (
    job_id uuid NOT NULL REFERENCES regulatory.jobs(id),
    chunk_key text NOT NULL CHECK (length(chunk_key) = 64),
    response text NOT NULL,
    PRIMARY KEY(job_id, chunk_key)
);
REVOKE ALL ON regulatory.extraction_chunks FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON regulatory.extraction_chunks TO regulatory_runtime;

CREATE FUNCTION regulatory.persist_supporting_spans() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    item jsonb;
    span jsonb;
    obligation_key uuid;
BEGIN
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.payload->'obligations') LOOP
        SELECT id INTO STRICT obligation_key FROM regulatory.obligations
          WHERE artifact_id = NEW.id AND source_obligation_id = (item->>'obligation_id')::uuid;
        FOR span IN SELECT value FROM jsonb_array_elements(item->'citations') WITH ORDINALITY AS c(value,n) WHERE n > 1 LOOP
            IF span->>'document_sha256' IS DISTINCT FROM NEW.payload->>'sha256' THEN
                RAISE EXCEPTION 'Supporting evidence revision mismatch';
            END IF;
            INSERT INTO regulatory.evidence_spans(obligation_id, revision_id, page, parser_ref, source_text,
                                                 quote, start_offset, end_offset, bounding_box)
            VALUES(obligation_key, NEW.revision_id, (span->>'page')::integer, span->>'ref', span->>'text',
                   span->>'quote', (span->>'start')::integer, (span->>'end')::integer, span->'bbox');
        END LOOP;
    END LOOP;
    RETURN NULL;
END;
$$;
REVOKE ALL ON FUNCTION regulatory.persist_supporting_spans() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION regulatory.persist_supporting_spans() TO regulatory_runtime;
CREATE CONSTRAINT TRIGGER persist_supporting_spans AFTER INSERT ON regulatory.artifact_versions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION regulatory.persist_supporting_spans();
COMMIT;
