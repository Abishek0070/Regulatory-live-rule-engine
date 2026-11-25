# Regulatory Integration Agent
For a new Supabase database, apply `schema.sql` and then the files in `migrations/` in order using the migration account. For an existing database, apply only migrations it has not received. Deploy the updated API after the migrations succeed.

Set `SUPABASE_URL` to the project URL and `SUPABASE_STORAGE_KEY` to the project's server-side `service_role` JWT. Keep both in the API environment and store the key in `.env` locally or the deployment secret store. The API uploads completed extraction JSON to the private `ria-artifacts` bucket and records its path, version link, exact SHA-256 and pending status in PostgreSQL. Existing artifacts saved before the Storage migration remain readable from their job records.

Apply schema.sql once using a migration account in PostgreSQL or Supabase. Grant regulatory_runtime to a dedicated login with a strong secret and use its TLS-verified DSN as DATABASE_URL. Keep the regulatory schema private. Set LLM_ENDPOINT to a trusted HTTPS chat-completions endpoint, LLM_MODEL and LLM_KEY through the deployment secret store. Run docker compose up --build. Terminate HTTPS at an authenticated, rate-limited ingress with a 16 MiB request cap and restricted network egress.

POST /v1/jobs/url accepts JSON with regulator (RBI or SEBI), source_url (direct official PDF) and optional expected_sha256. POST /v1/jobs/upload accepts application/pdf bytes with the same fields in the query string. Both endpoints run synchronously and return job_id, status and result. GET /v1/jobs/{job_id} returns the stored console outcome. Only completed results contain obligations. Deadlines remain in the source quotation. A potential change is a suggestion for human analysis.

Run python -m pytest after installing requirements.txt and pytest. Operational qualification requires a full container run, real official fixtures and a regulatory subject-matter review of extraction quality. Interrupted synchronous runs can remain in processing. Each conversion runs in a time-limited subprocess. Deploy behind a request deadline and supervise resource usage.

## Test console

The local console uses the `local-console` identity. Bind the server to `127.0.0.1` and disable proxy headers for this mode. Select RBI or SEBI, enter the direct official PDF URL, and optionally attach an identical PDF. The console shows job status, obligations, citations, and the raw payload. Use the job ID to retrieve a stored result.

To preview the console in PowerShell from the repository root, run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn agent.service:app --env-file .env --host 127.0.0.1 --port 8000 --no-proxy-headers
```

The job API currently allows unauthenticated access. Keep it on loopback or behind an authenticated ingress. Live submissions require ClamAV, a parser, PostgreSQL, and the configured model endpoint.

The agent uses pdfplumber to extract embedded text and page coordinates. Local runs use a time-limited subprocess. Compose runs the same parser in a network-isolated container. Image-only pages require OCR and stop the job for review. Set `CLAMAV_HOST=127.0.0.1` when ClamAV runs on the same machine.
