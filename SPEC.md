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
- **Amended 2026-08-17:** the interactive tier's output is still validated JSON plus an export. A **batch /
  lakehouse tier** (Spark, Delta Lake, Medallion layers, Airflow, dbt) is now in scope as M8 — see §15. Still
  no third-party destination connectors (Salesforce, Snowflake, HubSpot, etc.).
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

---

## 15. Extended stack (added 2026-08-17) — the batch / lakehouse tier

This project roughly doubles. It is the honest home for the data-engineering stack because it is the only one
of the eight whose subject is moving messy data at volume — but it becomes a **two-tier system**, and the
tiers must stay genuinely separate.

### 15.1 Two tiers, one schema registry

| | Interactive tier (M1–M7) | **Batch tier (M8)** |
| --- | --- | --- |
| Unit of work | one document, a human waiting | a corpus of thousands, nobody waiting |
| Verification | operator verifies every field before commit | statistical sampling + automated quality gates |
| Latency budget | seconds | minutes to hours |
| Failure handling | surface to the operator | quarantine and continue |
| Runtime | FastAPI + Claude API | Spark + Airflow + Delta Lake |

They share the **schema registry** and the extraction prompts, and nothing else. That sharing is the design's
whole justification: the same target schema drives both a human-in-the-loop path and an unattended path, and
records normalised by either land in the same shape.

**Do not let the tiers merge.** A Spark job that calls the interactive API per document is neither tier and
will be slow and fragile. A batch tier that skips quality gates because "the interactive tier verifies
things" is unverified data with extra steps.

### 15.2 Medallion architecture on Delta Lake

```
 Bronze  raw landing — original bytes + ingest metadata, never mutated, partitioned by ingest date
    │     Delta table over Parquet; schema-on-read; one row per source document
    ▼
 Silver  partitioned + typed — one row per extracted field with provenance, confidence,
    │     partition id, and source span. Schema enforced. Deduplicated by content hash.
    │     This is where CSV pathologies and PDF layout are already resolved.
    ▼
 Gold    schema-conformed business records — one row per document conforming to the target
          JSON Schema, validated, with a quality score. dbt owns the transforms and the tests.
```

Delta Lake requirements — use the features that justify choosing it over plain Parquet, or don't claim it:

- **ACID appends and concurrent writers.** Multiple Spark jobs writing Bronze without corrupting it.
- **Schema evolution** on Silver: a new field in the target schema must not require a backfill of everything.
  Use `mergeSchema` deliberately, not as a default.
- **MERGE / upsert** for re-processed documents — reprocessing a corpus after a prompt change must update
  rows, not duplicate them. This is the operation plain Parquet cannot do and is the honest reason Delta is
  here.
- **Time travel** for reproducibility: every Gold record records the Delta version of the Silver it derived
  from, so a result can be reproduced exactly after a re-extraction.
- `OPTIMIZE` / `ZORDER` on the columns actually filtered, and `VACUUM` with a retention policy — a lakehouse
  with no compaction story becomes a small-file problem, and knowing that is the point.

### 15.3 Spark (PySpark)

The batch tier's compute. Where it genuinely helps:

- **Partitioning and layout** — Bronze → Silver is embarrassingly parallel across documents.
- **Rate-limited external calls from executors** is the interesting hard part: the Claude API has a global
  rate limit, and naive `mapPartitions` with N executors will blow through it and get throttled. Solutions to
  choose between and *write up*: a bounded executor pool with a per-executor token bucket sized to
  `global_limit / num_executors`, or a two-stage design where Spark prepares batches and a separate
  concurrency-controlled async worker makes the calls. Pick one, explain the trade in `docs/BATCH.md`.
- **Skew handling** — a 400-page PDF beside 2-page CSVs is textbook partition skew. Salting or size-based
  repartitioning, with the before/after stage timings shown.
- Local mode for dev (`local[4]`) with a memory cap; Databricks for the scale run.

Explicitly not for: the interactive tier, or datasets small enough for pandas. A Spark job over 30 documents
is a demonstration of Spark, not a use of it — so the batch corpus must be large enough (≥ 5 000 documents,
synthesised by mutating a seed corpus if needed) that Spark is the honest choice. State the corpus size.

### 15.4 Databricks

The managed platform for the scale run: notebooks for exploration, Jobs for scheduled runs, Unity Catalog for
the three layers if available on the tier. Free/Community tier or a trial is enough; the repo must also run
end-to-end on **local Spark + local Delta** so a reader without a Databricks account can execute it. Note
which results came from which environment.

### 15.5 Airflow + dbt

- **Airflow** orchestrates the batch DAG: `land → validate → partition → extract → conform → quality_gate →
  publish`. Real requirements, not a hello-world DAG: idempotent tasks (a re-run must not duplicate), sensors
  on new Bronze partitions, per-task retries with backoff, SLA misses alerting, backfill over a date range,
  and **task-level cost accounting** so an expensive extraction day is attributable.
  Airflow's own scheduler is not the parallelism — Spark is. Airflow submits and tracks.
- **dbt** owns Silver → Gold: models as SQL, `schema.yml` tests (`not_null`, `accepted_values`, uniqueness,
  relationship tests), snapshots for slowly-changing reference data, and generated docs committed. dbt tests
  become the **quality gate** — a failing test blocks publish to Gold, which is the mechanism that replaces
  the interactive tier's human verification.

### 15.6 Kafka for ingest events

Document arrivals publish to a Kafka topic (`documents.landed`) with a Protobuf-encoded event. The batch tier
consumes it to trigger micro-batches; the interactive tier ignores it. This decouples arrival from
processing, and gives the Airflow sensor something real to sense. Single-broker KRaft mode locally.

Keep it honest: Kafka here is an **event notification bus**, not the data path — documents move through S3 and
Delta, never through Kafka. Say so, because putting file bytes in Kafka is a common and costly mistake.

### 15.7 Kubernetes + Helm, Nginx, KMS

Same forward-deployed rationale as the sibling helpdesk project: the deliverable is something installable in a
customer's environment.

```
deploy/helm/data-ingestion-pipeline/     api, web, worker, ingress (Nginx), HPA, secrets, ServiceMonitor
```

`helm lint` + `helm template` in CI; installs on **kind** with the smoke suite green; no secrets in
`values.yaml`. **AWS KMS** for document and field encryption at rest (client-side envelope encryption before
S3 upload, so bytes are unreadable even with bucket access) plus SSE-KMS on the bucket. `infra/` Terraform for
the bucket, CMK, IAM, and VPC endpoints.

### 15.8 CI portability: GitLab CI and Jenkins

Enterprise customers run neither GitHub Actions nor, often, anything you'd choose. Demonstrating pipeline
portability is a real FDE signal — with one condition.

**Both must actually run.** A `Jenkinsfile` or `.gitlab-ci.yml` sitting unexercised in a repo is visibly
decorative and worse than its absence:

- **GitLab CI** — mirror the repo to gitlab.com (free tier) and let the pipeline run there. Link the passing
  pipeline in the README.
- **Jenkins** — a `Jenkinsfile` (declarative, multi-stage: lint → unit → integration → helm lint → build)
  exercised against a **local Jenkins in Docker**, with `docker-compose.jenkins.yml` and the setup documented
  so a reader can reproduce it. Commit a screenshot or the console log of a green build.

If either can't be genuinely exercised, **delete that file** rather than shipping it. This is the lowest
value-per-hour item in the extended stack; treat it as optional and do it last.

### 15.9 COMPLIANCE.md

Same rule as the sibling project, and it matters here because this tier handles bulk client documents:
data classification, client-data handling and the prohibition on real customer data in the repo, encryption
in transit and at rest (KMS envelope + SSE-KMS + TLS), the correction log as audit trail, retention and
deletion including Delta `VACUUM` implications for "right to be forgotten", access control, and known gaps.

Framed as **"designed against SOC 2 and HIPAA control boundaries"** — never "SOC 2 compliant" or "HIPAA
compliant". Those are audit outcomes, not code properties, and asserting one you don't have is a
misrepresentation that ends healthcare-adjacent hiring processes. Include a "not claimed" section naming what
real certification would require.

Note the genuine tension worth writing about: Delta time travel and a deletion request are in conflict.
`VACUUM` retention bounds how long deleted data is still reachable, and stating that trade-off is a stronger
signal than pretending it doesn't exist.

### 15.10 Parquet + OpenTelemetry

- **Parquet** underlies the Delta layers, and eval/timing results move to it too — per-field extraction
  records across 5 000 documents are millions of rows, queryable with `duckdb`.
- **OpenTelemetry** spans: `upload_part`, `complete`, `partition`, `extract_call`, `merge`, `validate`,
  `commit`, and batch spans `spark_stage`, `dbt_model`, `quality_gate`. Rate-limiter decisions as span
  events, so throttling is visible in a trace. Airflow task instances carry the trace id, so one trace spans
  the DAG. **No document contents in attributes** — the data is client data by premise.

## 16. Additional milestones

- **M8 Batch tier.** ≥ 5 000-document corpus; Bronze/Silver/Gold on Delta Lake with MERGE-based
  reprocessing and time-travel provenance; Spark job with a solved executor-side rate-limit strategy and a
  skew fix, both written up in `docs/BATCH.md`; Airflow DAG idempotent with backfill; dbt models + tests
  gating publish to Gold; Kafka arrival events; runs end-to-end on local Spark **and** on Databricks.
- **M9 Deployable.** Helm chart installing on kind with the smoke suite green; Nginx ingress; KMS envelope
  encryption before upload + SSE-KMS; Terraform `fmt`/`validate` in CI.
- **M10 CI portability (optional, last).** GitLab pipeline green on a mirror and linked; Jenkinsfile green
  against local Jenkins with evidence committed — or both files deleted.
- **M11 Compliance + observability.** `COMPLIANCE.md` including the deletion-vs-time-travel tension and the
  "not claimed" section; OTel across interactive and batch tiers with a no-content-in-attributes test.

### Honest-claims additions

| Claim | Status | Backed by |
| --- | --- | --- |
| handles volume, not just documents | ☐ | ≥ 5 000-doc corpus through Spark; stage timings before/after skew fix |
| lakehouse, not a folder of Parquet | ☐ | Delta MERGE reprocessing, schema evolution, time-travel provenance, OPTIMIZE/VACUUM policy |
| orchestrated, not scripted | ☐ | idempotent Airflow DAG with backfill and SLA alerting |
| data quality is enforced | ☐ | dbt tests blocking publish to Gold |
| rate limits respected at scale | ☐ | executor-side strategy documented in `docs/BATCH.md`; no throttling in the run |
| deployable into a customer cluster | ☐ | Helm chart installs on kind; smoke suite green |
| CI portable across platforms | ☐ | GitLab pipeline linked **and** Jenkins build evidenced — or files removed |
| encrypted at rest with managed keys | ☐ | client-side KMS envelope + SSE-KMS |
| control boundary documented | ☐ | `COMPLIANCE.md`, *designed against*, never *compliant* |
