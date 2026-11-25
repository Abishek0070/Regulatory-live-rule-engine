# Circular extraction

FastAPI accepts an official RBI or SEBI PDF URL and an optional matching upload. LangGraph verifies the URL and bytes, records the SHA-256 revision, streams the PDF to ClamAV, then invokes pdfplumber. Local and container transports return page-linked blocks to the same extraction code.

## Structure and context

The parser groups text lines by numbering, spacing, indentation and typography. Ruled-table coordinates identify header and body lines. Each block retains its text, page and bounding box. The hierarchy builder indexes headings, clause labels, nested markers and annexures across pages. Extractive patterns collect source excerpts for dates, applicability, definitions and qualifiers. Missing metadata is omitted. Pattern matches are candidates for interpretation.

## Planning and extraction

Small inputs fit in one request. Larger inputs are grouped by clause with some space reserved for supporting material. Oversized excerpts split into overlapping character ranges of the original block. Target and supporting entries have distinct roles. Chunk IDs bind the input revision and exact serialized material.

Set CHUNK_TARGET_TOKENS (default 3200), LLM_CONTEXT_TOKENS (32768) and LLM_OUTPUT_TOKENS (4096) to fit the deployed model. The planner reserves output capacity and prompt overhead. By default it uses a conservative byte estimate. An optional LLM_TOKENIZER_FILE can point to the model's compatible local tokenizer JSON when the tokenizers package is installed.

The extraction call returns structured obligations, indexed quotes and target coverage. The backend resolves offsets against the original text, rejects invalid indexes or unsupported numeric values and assembles citations. A second model call checks for missed duties, incomplete scope and unsupported interpretations. A failed chunk gets one targeted retry. Reconciliation checks conflicting overlapping extracts, unresolved references and unlinked exceptions. A review outcome prevents publication.

## Persistence and operation

Apply migrations/004_extraction_chunks.sql after migration 003 before running the updated backend. The private checkpoint table records validated responses by job and content-derived key. Re-executing the same group for the same job can reuse its response. The public submission API creates a new job each time. Automatic recovery scheduling is future work.

Version 1.1 artifacts retain the primary citation field and add citations and a document audit. Coverage maps parser references to classifications and obligation IDs. Source text backing global facts is included. A deferred database trigger stores additional evidence spans. The existing artifact hash, private bucket and pending review workflow apply.

Hierarchy and table reconstruction are heuristic. Unruled tables, multi-column layouts, unusual footnotes, implicit cross-references and implicit duties need evaluation on representative circulars. Parsing uses embedded text only. A passing audit is a consistency check, not a measured recall guarantee.
