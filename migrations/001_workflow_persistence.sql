CREATE TABLE regulatory.tenants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, owner)
);

CREATE TABLE regulatory.cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES regulatory.tenants,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id)
);

CREATE TABLE regulatory.workflows (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    case_id uuid NOT NULL,
    version text NOT NULL DEFAULT '1.0',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, case_id) REFERENCES regulatory.cases(tenant_id, id)
);

CREATE TABLE regulatory.tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    workflow_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'RIA',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, workflow_id) REFERENCES regulatory.workflows(tenant_id, id)
);

ALTER TABLE regulatory.jobs
    ADD COLUMN tenant_id uuid,
    ADD COLUMN task_id uuid,
    ADD COLUMN attempt_number integer NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
    ADD COLUMN finished_at timestamptz,
    ADD COLUMN failure_code text,
    ADD COLUMN source_regulator text,
    ADD COLUMN source_url text,
    ADD FOREIGN KEY (tenant_id, owner) REFERENCES regulatory.tenants(id, owner),
    ADD FOREIGN KEY (tenant_id, task_id) REFERENCES regulatory.tasks(tenant_id, id),
    ADD UNIQUE (task_id, attempt_number),
    ADD UNIQUE (id, task_id);

CREATE TABLE regulatory.documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    regulator text NOT NULL CHECK (regulator IN ('RBI', 'SEBI')),
    source_url text NOT NULL,
    UNIQUE (regulator, source_url)
);

CREATE TABLE regulatory.document_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES regulatory.documents,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    effective_date date,
    lifecycle_status text NOT NULL DEFAULT 'observed',
    UNIQUE (document_id, sha256)
);

CREATE TABLE regulatory.job_revisions (
    job_id uuid PRIMARY KEY REFERENCES regulatory.jobs,
    revision_id uuid NOT NULL REFERENCES regulatory.document_revisions,
    UNIQUE (job_id, revision_id)
);

CREATE TABLE regulatory.artifact_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL UNIQUE,
    task_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    schema_version text NOT NULL,
    payload jsonb NOT NULL,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, version),
    UNIQUE (id, revision_id),
    FOREIGN KEY (job_id, task_id) REFERENCES regulatory.jobs(id, task_id),
    FOREIGN KEY (job_id, revision_id) REFERENCES regulatory.job_revisions(job_id, revision_id)
);

CREATE TABLE regulatory.obligations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    source_obligation_id uuid NOT NULL,
    statement text NOT NULL,
    impact_category text NOT NULL,
    UNIQUE (artifact_id, source_obligation_id),
    UNIQUE (id, revision_id),
    FOREIGN KEY (artifact_id, revision_id) REFERENCES regulatory.artifact_versions(id, revision_id)
);

CREATE TABLE regulatory.evidence_spans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    obligation_id uuid NOT NULL,
    revision_id uuid NOT NULL,
    page integer NOT NULL CHECK (page > 0),
    parser_ref text NOT NULL,
    source_text text NOT NULL,
    quote text NOT NULL,
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL,
    bounding_box jsonb,
    FOREIGN KEY (obligation_id, revision_id) REFERENCES regulatory.obligations(id, revision_id),
    CHECK (end_offset > start_offset AND end_offset <= char_length(source_text)
           AND quote = substring(source_text FROM start_offset + 1 FOR end_offset - start_offset))
);

CREATE INDEX evidence_spans_obligation_idx ON regulatory.evidence_spans(obligation_id);
CREATE INDEX cases_tenant_idx ON regulatory.cases(tenant_id);
CREATE INDEX workflows_case_idx ON regulatory.workflows(tenant_id, case_id);
CREATE INDEX tasks_workflow_idx ON regulatory.tasks(tenant_id, workflow_id);

CREATE FUNCTION regulatory.assign_job_context() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    case_key uuid;
    workflow_key uuid;
BEGIN
    INSERT INTO regulatory.tenants(owner, created_at) VALUES (NEW.owner, NEW.created_at)
        ON CONFLICT (owner) DO NOTHING;
    SELECT id INTO STRICT NEW.tenant_id FROM regulatory.tenants WHERE owner = NEW.owner;
    IF NEW.task_id IS NULL THEN
        INSERT INTO regulatory.cases(tenant_id, created_at) VALUES (NEW.tenant_id, NEW.created_at)
            RETURNING id INTO case_key;
        INSERT INTO regulatory.workflows(tenant_id, case_id, created_at)
            VALUES (NEW.tenant_id, case_key, NEW.created_at) RETURNING id INTO workflow_key;
        INSERT INTO regulatory.tasks(tenant_id, workflow_id, created_at)
            VALUES (NEW.tenant_id, workflow_key, NEW.created_at) RETURNING id INTO NEW.task_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION regulatory.record_verified_revision(
    job_key uuid, regulator_name text, official_url text, digest text
) RETURNS uuid LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    document_key uuid;
    revision_key uuid;
    bound_revision uuid;
BEGIN
    INSERT INTO regulatory.documents(regulator, source_url) VALUES (regulator_name, official_url)
        ON CONFLICT (regulator, source_url) DO NOTHING;
    SELECT id INTO STRICT document_key FROM regulatory.documents
        WHERE regulator = regulator_name AND source_url = official_url;
    INSERT INTO regulatory.document_revisions(document_id, sha256)
        SELECT document_key, digest FROM regulatory.jobs WHERE id = job_key
        ON CONFLICT (document_id, sha256) DO NOTHING;
    SELECT id INTO STRICT revision_key FROM regulatory.document_revisions
        WHERE document_id = document_key AND sha256 = digest;
    INSERT INTO regulatory.job_revisions(job_id, revision_id) VALUES (job_key, revision_key)
        ON CONFLICT (job_id) DO NOTHING;
    SELECT revision_id INTO STRICT bound_revision FROM regulatory.job_revisions WHERE job_id = job_key;
    IF bound_revision <> revision_key THEN
        RAISE EXCEPTION 'Job already bound to a different document revision';
    END IF;
    RETURN revision_key;
END;
$$;

CREATE FUNCTION regulatory.persist_job_output() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    revision_key uuid;
    artifact_key uuid;
    obligation_key uuid;
    item jsonb;
    citation jsonb;
BEGIN
    IF NEW.status = 'processing' THEN
        RETURN NEW;
    END IF;
    NEW.finished_at := coalesce(NEW.finished_at, now());
    IF NEW.status <> 'completed' THEN
        NEW.failure_code := NEW.status;
        RETURN NEW;
    END IF;
    NEW.failure_code := NULL;
    revision_key := regulatory.record_verified_revision(
        NEW.id, NEW.result #>> '{document,regulator}',
        NEW.result #>> '{document,source_url}', NEW.result->>'sha256');
    INSERT INTO regulatory.artifact_versions(job_id, task_id, revision_id, version, schema_version,
                                            payload, sha256, created_at)
        VALUES (NEW.id, NEW.task_id, revision_key, NEW.attempt_number, NEW.result->>'schema_version',
                NEW.result, encode(sha256(convert_to(NEW.result::text, 'UTF8')), 'hex'), NEW.finished_at)
        ON CONFLICT (job_id) DO NOTHING RETURNING id INTO artifact_key;
    IF artifact_key IS NULL THEN
        IF NOT EXISTS (SELECT 1 FROM regulatory.artifact_versions
                       WHERE job_id = NEW.id AND payload = NEW.result) THEN
            RAISE EXCEPTION 'Completed output is immutable';
        END IF;
        RETURN NEW;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.result->'obligations') LOOP
        citation := item->'citation';
        IF citation ? 'document_sha256' AND
           citation->>'document_sha256' IS DISTINCT FROM NEW.result->>'sha256' THEN
            RAISE EXCEPTION 'Citation revision does not match job revision';
        END IF;
        INSERT INTO regulatory.obligations(artifact_id, revision_id, source_obligation_id, statement, impact_category)
            VALUES (artifact_key, revision_key, (item->>'obligation_id')::uuid,
                    item->>'statement', item #>> '{potential_change,area}') RETURNING id INTO obligation_key;
        INSERT INTO regulatory.evidence_spans(obligation_id, revision_id, page, parser_ref, source_text,
                                             quote, start_offset, end_offset, bounding_box)
            VALUES (obligation_key, revision_key, (citation->>'page')::integer, citation->>'ref',
                    citation->>'text', citation->>'quote', (citation->>'start')::integer,
                    (citation->>'end')::integer, nullif(citation->'bbox', 'null'::jsonb));
    END LOOP;
    RETURN NEW;
END;
$$;

-- Populate legacy attempts before enforcing the new parent references.
DO $$
DECLARE
    attempt record;
    tenant_key uuid;
    case_key uuid;
    workflow_key uuid;
    task_key uuid;
BEGIN
    FOR attempt IN SELECT * FROM regulatory.jobs ORDER BY created_at, id LOOP
        INSERT INTO regulatory.tenants(owner, created_at) VALUES (attempt.owner, attempt.created_at)
            ON CONFLICT (owner) DO NOTHING;
        SELECT id INTO STRICT tenant_key FROM regulatory.tenants WHERE owner = attempt.owner;
        INSERT INTO regulatory.cases(tenant_id, created_at) VALUES (tenant_key, attempt.created_at)
            RETURNING id INTO case_key;
        INSERT INTO regulatory.workflows(tenant_id, case_id, created_at)
            VALUES (tenant_key, case_key, attempt.created_at) RETURNING id INTO workflow_key;
        INSERT INTO regulatory.tasks(tenant_id, workflow_id, created_at)
            VALUES (tenant_key, workflow_key, attempt.created_at) RETURNING id INTO task_key;
        UPDATE regulatory.jobs SET tenant_id = tenant_key, task_id = task_key,
            source_regulator = attempt.result #>> '{document,regulator}',
            source_url = attempt.result #>> '{document,source_url}',
            finished_at = (SELECT max(at) FROM regulatory.events WHERE job = attempt.id AND stage = attempt.status)
            WHERE id = attempt.id;
    END LOOP;
END;
$$;

ALTER TABLE regulatory.jobs ALTER COLUMN tenant_id SET NOT NULL, ALTER COLUMN task_id SET NOT NULL;
CREATE TRIGGER assign_job_context BEFORE INSERT ON regulatory.jobs
    FOR EACH ROW EXECUTE FUNCTION regulatory.assign_job_context();
CREATE TRIGGER persist_job_output BEFORE UPDATE OF status, result ON regulatory.jobs
    FOR EACH ROW EXECUTE FUNCTION regulatory.persist_job_output();
UPDATE regulatory.jobs SET status = status WHERE status <> 'processing';

REVOKE ALL ON ALL TABLES IN SCHEMA regulatory FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA regulatory FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON regulatory.tenants, regulatory.cases, regulatory.workflows, regulatory.tasks,
    regulatory.documents, regulatory.document_revisions, regulatory.job_revisions,
    regulatory.artifact_versions, regulatory.obligations, regulatory.evidence_spans TO regulatory_runtime;
GRANT EXECUTE ON FUNCTION regulatory.assign_job_context(), regulatory.persist_job_output(),
    regulatory.record_verified_revision(uuid, text, text, text) TO regulatory_runtime;
