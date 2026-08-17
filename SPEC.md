# SPEC — Unstructured Data Ingestion Pipeline

**Authoritative technical specification.** `ROADMAP.md` gives the order; this gives the contents. Where
they disagree, this wins. If a requirement here looks wrong, say so and stop.

---

## 1. The claim

> Drag-and-drop normalization of messy client PDFs and CSVs into strict JSON schemas: chunked uploads to S3,
> rate-limited extraction calls, and an interactive mapping UI to verify extracted fields before commit.
> **Cut client onboarding time [XX]% in pilot use.**

Resume stack string the build must match: *Next.js, FastAPI, Claude API, AWS S3*
(`Bravim_Purohit_FDE.tex:139`).

### "In pilot use" is a claim about other people

Everything before the last sentence is an engineering claim the code can satisfy. The last sentence is
different: it asserts that real users ran this on real work and got faster. That cannot be satisfied by
writing code, and it is the one claim on these four resumes that an interviewer can probe with a single
question — *"who was the pilot?"*

Three honest resolutions, in descending order of strength:

1. **Run a real pilot.** 2–3 people, real messy documents, timed. Then "in pilot use" is literally true and
   you have a story to tell about what they struggled with.
2. **Reword to what you actually measured.** A timed head-to-head against manual entry is a *stronger*
   claim than a vague pilot, because it names its own methodology:
   > *"Cut document normalization time [XX]% in a timed comparison against manual entry (N documents, K
   > participants)."*
3. **Drop the clause** and let the engineering stand on its own.

Option 2 is usually the right answer — it is more specific, more defensible, and achievable in an afternoon.
The measurement protocol is §9, and it is designed for exactly that. **The wording decision belongs to the
user; this repo's job is to produce a number honest enough to support whichever they choose.**

## 2. Non-goals

- Not a general-purpose OCR engine. Use an existing extractor for text/layout.
- No handwriting recognition. Note it in the README as a known limitation.
- No data warehouse or destination connectors. Output is validated JSON plus an export.
- No multi-tenant billing or user management beyond simple scoping.
- No fine-tuning. Claude API with structured output.

## 3. Architecture

```
 browser (Next.js :7800)
   │  drag & drop
   │  1. POST /uploads          → presigned multipart URLs
   │  2. PUT parts DIRECTLY to S3 (never through the API)
   │  3. POST /uploads/{id}/complete
   ▼
 S3 / MinIO :7804 ──► API (FastAPI :7801)
                          │
                          ├─► partition:  PDF → pages/tables/paragraphs (with bbox)
                          │               CSV → encoding + delimiter sniff, header
                          │                     normalisation, type inference
                          ├─► extract:   Claude API, schema-constrained, per partition
                          │              token-bucket rate limited, 429/529 backoff,
                          │              cost accounted, disk-cached by content hash
                          ├─► validate:  against the target JSON Schema (strict)
                          │
                          ▼
                    Postgres :7802   documents, partitions, fields, corrections, runs
                          │
                          ▼
            mapping UI: source ◄──► extracted fields, click-to-highlight
                          │
                    operator verifies / corrects
                          ▼
                    COMMIT → validated JSON + export
```

## 4. Upload requirements

The bullet says "chunked uploads", which means presigned S3 multipart done properly:

- **Parts go browser → S3 directly.** The API issues presigned part URLs and never proxies bytes. Proxying a
  400 MB PDF through FastAPI is the mistake this design exists to avoid.
- Part size 8–16 MB (S3 minimum is 5 MB except the last part), computed from file size to stay under the
  10 000-part limit.
- **Resumable.** Persist `{upload_id, part_number, etag, size}` per part. On resume, the client asks which
  parts are already present and uploads only the gaps. A page refresh mid-upload must not restart it.
- Parallel part uploads with a concurrency cap, per-part retry with backoff.
- `CompleteMultipartUpload` with the ordered ETag list; `AbortMultipartUpload` on cancel **and** a lifecycle
  rule to abort incomplete uploads after N days. Orphaned multipart parts are billed storage nobody can see.
- Content-hash dedupe: same bytes uploaded twice reuses the existing document and its extraction.
- Validate declared content type against sniffed magic bytes; cap file size; reject archives.
- Never a public bucket. Presigned URLs only, short expiry, CORS scoped to the app origin.

## 5. Partitioning

### PDFs

Produce typed elements, not a text blob: `{kind: page|heading|paragraph|table|kv_pair, page, bbox, text}`.
Bounding boxes are **required** — the mapping UI's click-to-highlight is impossible without them, and that
UI is a named part of the claim.

Tables are the hard case and the most valuable: detect them, keep cell structure, and don't let a table get
flattened into prose before extraction.

### CSVs

"Messy" is the operative word, so handle: encoding detection (UTF-8/UTF-16/Latin-1, BOM), delimiter sniffing
(`,` `;` `\t` `|`), quoting and embedded newlines, preamble junk rows before the real header, multi-row
headers, merged/duplicate/blank column names, inconsistent row lengths, trailing summary rows, mixed date
formats, thousands separators, currency symbols, `NULL`/`N/A`/`-`/empty as distinct nulls.

Each of these deserves a fixture file in `tests/fixtures/csv/`. This is where "messy" is either true or
marketing.

## 6. Extraction

- Claude API with schema-constrained structured output (tool-use JSON schema). Never regex over prose,
  never "return JSON" in a prompt without schema enforcement.
- **Per-field provenance and confidence.** Every extracted field carries where it came from — page + bbox for
  PDFs, row + column for CSVs — and a confidence signal. Fields below threshold are flagged for review, and
  the UI sorts by that. An extraction you can't trace to a source span can't be verified, only trusted.
- **Rate limiting**, since it's in the bullet: token bucket over requests/min *and* tokens/min, honouring
  `retry-after`, exponential backoff with jitter on 429 and 529, a global concurrency cap, and a per-run
  cost ceiling. Log the limiter's decisions so the dashboard can show throttling as it happens.
- Disk-cache responses by `sha256(model, prompt, schema, partition_content)`. Re-running the pipeline over
  the same corpus during development must not re-bill.
- Long documents: extract per partition, then merge. Merge conflicts (two partitions proposing different
  values for one field) are surfaced to the operator, never silently resolved by last-write-wins.

## 7. Schemas, validation, and commit

- Target schemas are user-supplied JSON Schema (draft 2020-12), stored and versioned. Field-level metadata:
  description, examples, required, format.
- Validation is **strict**: `additionalProperties: false`, type coercion only where explicitly configured
  (e.g. `"1,234.50"` → `1234.50` when the field is a number with a declared locale).
- **A document cannot be committed while any required field is unverified or any validation error stands.**
  That's what "verify extracted fields before commit" means, and it must be enforced server-side, not just
  disabled-button-in-the-UI.
- Operator corrections are persisted with the original value, the corrected value, and who changed it. That
  correction log is both an audit trail and the beginnings of an eval set — it tells you which fields the
  extractor is actually bad at.
- Export: validated JSON per document, plus NDJSON for a batch.

## 8. Data model

```sql
CREATE TYPE doc_state AS ENUM
  ('uploading','uploaded','partitioning','extracting','review','committed','failed');

CREATE TABLE documents (
  id UUID PRIMARY KEY,
  filename TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
  mime TEXT NOT NULL, size_bytes BIGINT NOT NULL,
  s3_key TEXT NOT NULL, schema_id UUID NOT NULL REFERENCES schemas(id),
  state doc_state NOT NULL DEFAULT 'uploading',
  cost_usd NUMERIC(10,4) DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), committed_at TIMESTAMPTZ
);

CREATE TABLE upload_parts (
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  upload_id TEXT NOT NULL, part_number INT NOT NULL,
  etag TEXT, size_bytes INT, uploaded_at TIMESTAMPTZ,
  PRIMARY KEY (document_id, part_number)
);

CREATE TABLE partitions (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal INT NOT NULL, kind TEXT NOT NULL,
  page INT, bbox JSONB, row_range INT4RANGE, content TEXT NOT NULL
);

CREATE TABLE fields (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  path TEXT NOT NULL,                 -- JSON pointer into the target schema
  value JSONB, confidence REAL,
  source_partition_id UUID REFERENCES partitions(id),
  source_span JSONB,                  -- bbox or row/col — powers click-to-highlight
  verified BOOLEAN NOT NULL DEFAULT false,
  corrected_from JSONB, corrected_by TEXT, corrected_at TIMESTAMPTZ,
  validation_error TEXT,
  UNIQUE (document_id, path)
);

CREATE TABLE timings (                -- the onboarding-time measurement lives here
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id),
  participant TEXT NOT NULL, arm TEXT NOT NULL,     -- manual | tool
  started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
  active_seconds INT,                                -- excludes idle
  fields_corrected INT, accuracy REAL
);
```

## 9. Measurement protocol

### The manual baseline is a deliverable

There is no percentage without a measured manual time. Build the timing harness (`timings` table +
instrumentation) in M1, not at the end.

### Protocol

- **Corpus:** ≥ 30 documents across ≥ 3 messy real-world shapes (a multi-page PDF with tables, a CSV with
  preamble junk and duplicate headers, a scanned/low-quality PDF). Ground truth transcribed once, carefully,
  and frozen with a checksum.
- **Two matched sets, A and B**, of equal difficulty. Same participant does A manually and B with the tool,
  then the assignment is **counterbalanced** across participants so order and learning effects cancel. Never
  let the same person do the same document twice — the second pass measures memory.
- **Manual arm:** target JSON Schema in front of them, a text editor or spreadsheet, and the source document.
  Timed, active time only (pause the clock on interruptions).
- **Tool arm:** same documents, upload → review → correct → commit. The tool times itself.
- **Accuracy is measured in both arms** against ground truth. A speedup with worse accuracy is not a win,
  and manual transcription errors are common enough that the tool may win on both axes — which is a much
  better story than speed alone.
- Report: median and total time per arm, per-document distribution, field-level accuracy per arm, and the
  relative reduction. Include participant count. With K < 3 participants, say so explicitly and report
  per-participant numbers rather than a pooled average.

### Also report (these make the engineering legible)

Per-field extraction precision/recall against ground truth, correction rate by field type, throughput
(documents/hour under rate limits), cost per document, and p95 end-to-end latency by document size.

### Filling `[XX]`

`[XX]%` = `(manual_median - tool_median) / manual_median × 100`, computed from `eval/results/`. State N, K,
and the corpus composition next to it in the README.

## 10. Module layout

```
pipeline/
  uploads/     presigned multipart, part tracking, resume, dedupe
  partition/   pdf/ (layout + tables + bbox), csv/ (sniffing, header repair, types)
  extract/     Claude adapter, schema-constrained calls, merge, provenance
  limits/      token bucket, backoff, cost ceiling
  schemas/     registry, versioning, strict validation, coercion rules
  review/      verification state machine, corrections, commit gate
  export/      JSON / NDJSON
  api/         FastAPI
  timing/      measurement instrumentation
eval/          corpus manifest, ground truth, protocol, results/
web/           Next.js app
tests/fixtures/csv/   one file per messy-CSV pathology
```

## 11. API

```
POST /api/uploads                {filename, size, mime, schema_id} → {document_id, upload_id, parts[]}
GET  /api/uploads/{id}/parts     → which parts S3 already has (resume support)
POST /api/uploads/{id}/complete  {parts[]} → starts partition + extract
POST /api/uploads/{id}/abort
GET  /api/documents/{id}         → state, partitions, fields with provenance + confidence
GET  /api/documents/{id}/source  → presigned GET for the viewer
PATCH /api/documents/{id}/fields/{path}  {value} → correction, re-validates
POST /api/documents/{id}/verify  {paths[]} → mark verified
POST /api/documents/{id}/commit  → 409 with field errors if anything unverified or invalid
GET  /api/documents/{id}/export?format=json|ndjson
POST /api/schemas                {name, json_schema} → versioned
GET  /api/runs                   → throughput, throttling, cost
GET  /api/events/{id}            → SSE progress for the upload/extract pipeline
```

## 12. Web app (`web/`)

Next.js (App Router) + TypeScript + Tailwind + shadcn/ui.

1. **Drop zone.** Multi-file drag & drop, per-file progress with part-level detail, pause/resume, and a
   visible resume-after-refresh path. Cheap to show, and it demonstrates the multipart work directly.
2. **Mapping / verify view** — the screen the bullet is about. Split pane: rendered source (PDF page with
   bbox overlays, or CSV grid) on one side, target-schema fields on the other. Clicking a field highlights
   its source span; clicking the source jumps to the field. Low-confidence fields sorted first, validation
   errors inline, keyboard-driven accept/correct.
3. **Schema editor.** JSON Schema with validation and field descriptions.
4. **Runs.** Throughput, rate-limit throttling events, cost per document, failures with reasons.
5. **Timing.** Manual-vs-tool comparison rendered from `timings` — the resume number, on a page.

## 13. Milestone acceptance criteria

- **M1 Uploads + timing harness.** Presigned multipart against MinIO, resumable across a page refresh,
  dedupe by hash, abort + lifecycle rule. `timings` instrumentation in place.
- **M2 Partitioning.** PDF elements with bboxes; CSV sniffing and header repair with a fixture per pathology
  in §5, all green.
- **M3 Extraction.** Schema-constrained Claude calls, per-field provenance + confidence, rate limiter with
  backoff, disk cache, cost accounting, partition merge with conflict surfacing.
- **M4 Verify + commit.** Strict validation, server-enforced commit gate, correction log, export.
- **M5 UI.** Drop zone with resume, mapping view with click-to-highlight in both directions, schema editor.
- **M6 Measurement.** ≥ 30-document corpus with frozen ground truth; counterbalanced manual-vs-tool runs;
  accuracy in both arms; **README Benchmarks table filled**; N and K stated.
- **M7 Presentable.** Real AWS S3 (not just MinIO) exercised once; README diagram accurate; CI green.

## 14. Honest-claims register

| Claim | Status | Backed by |
| --- | --- | --- |
| drag-and-drop | ☐ | multi-file drop zone with per-part progress |
| chunked uploads to S3 | ☐ | presigned multipart, resumable, real S3 exercised |
| messy PDFs **and** CSVs | ☐ | fixture per pathology in §5; table extraction working |
| strict JSON schemas | ☐ | draft 2020-12, `additionalProperties: false`, server-side commit gate |
| rate-limited extraction calls | ☐ | token bucket + backoff, throttling visible in the dashboard |
| interactive mapping UI to verify before commit | ☐ | bidirectional click-to-highlight; commit blocked until verified |
| cut onboarding time `[XX]%` | ☐ | `eval/results/`, counterbalanced arms, accuracy in both, N + K stated |
| **"in pilot use"** | ☐ | real participants — or the clause reworded per §1 (user's call) |

Any unchecked row ⇒ `Bravim_Purohit_FDE.tex:142` stays commented and `[XX]` stays bracketed. The last row is
the one to settle early, because it changes the resume text and not just the code.
