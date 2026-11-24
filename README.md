# Regulatory Integration Agent
Apply schema.sql once using a migration account in PostgreSQL or Supabase. Grant regulatory_runtime to a dedicated login with a strong secret and use its TLS-verified DSN as DATABASE_URL. Keep the regulatory schema private. CLIENT_TOKEN_HASHES is a JSON object mapping caller names to SHA-256 hex digests of independently generated random 32-byte bearer secrets. Set LLM_ENDPOINT to a trusted HTTPS chat-completions endpoint, LLM_MODEL and LLM_KEY through the deployment secret store. Run docker compose up --build. Terminate HTTPS at an authenticated, rate-limited ingress with a 16 MiB request cap and restricted network egress.

POST /v1/jobs/url accepts JSON with regulator (RBI or SEBI), source_url (direct official PDF) and optional expected_sha256. POST /v1/jobs/upload accepts application/pdf bytes with the same fields in the query string. Send Authorization Bearer credentials. Both endpoints run synchronously and return job_id, status and result. GET /v1/jobs/{job_id} returns the caller's stored outcome. Only completed results contain obligations. Deadlines remain in the source quotation. A potential change is a suggestion for human analysis.

Run python -m pytest after installing requirements.txt and pytest. Operational qualification requires a full container run, real official fixtures and a regulatory subject-matter review of extraction quality. Interrupted synchronous runs can remain in processing. Each conversion runs in a time-limited subprocess. Deploy behind a request deadline and supervise resource usage.

## Test console

The local console uses the `local-console` identity when `LOCAL_CONSOLE_NO_AUTH=1`. Bind the server to `127.0.0.1` and disable proxy headers for this mode. Select RBI or SEBI, enter the direct official PDF URL, and optionally attach an identical PDF. The console shows job status, obligations, citations, and the raw payload. Use the job ID to retrieve a stored result.

To preview the console in PowerShell from the repository root, run:

```powershell
$env:LOCAL_CONSOLE_NO_AUTH = "1"
.\.venv\Scripts\python.exe -m uvicorn agent.service:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

The Compose API uses client bearer authentication. Live submissions require ClamAV, a parser, PostgreSQL, and the configured model endpoint.

For a local test, install Docling in the virtual environment and start the API with `PARSER_MODE=local` and `--env-file .env`. Docling uses a time-limited subprocess without the container sandbox and caches its models in the virtual environment. Source verification and ClamAV still run first. Set `CLAMAV_HOST=127.0.0.1` when ClamAV runs on the same machine. PostgreSQL and the model endpoint are still required.
