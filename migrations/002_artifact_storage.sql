INSERT INTO storage.buckets (id, name, public, allowed_mime_types)
VALUES ('ria-artifacts', 'ria-artifacts', false, ARRAY['application/json']);

ALTER TABLE regulatory.artifact_versions ADD UNIQUE (id, sha256);

CREATE TABLE regulatory.artifact_objects (
    artifact_id uuid PRIMARY KEY,
    bucket text NOT NULL CHECK (bucket = 'ria-artifacts'),
    object_path text NOT NULL,
    sha256 text NOT NULL,
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status = 'pending'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bucket, object_path),
    FOREIGN KEY (artifact_id, sha256) REFERENCES regulatory.artifact_versions(id, sha256)
);

REVOKE ALL ON regulatory.artifact_objects FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON regulatory.artifact_objects TO regulatory_runtime;
