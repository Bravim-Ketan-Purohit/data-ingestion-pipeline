# CLAUDE.md — Unstructured Data Ingestion Pipeline

Operating instructions for a Claude Code session in this repo. Read `SPEC.md` before writing code —
especially §1 (the "in pilot use" problem) and §9 (the manual baseline is a deliverable). `ROADMAP.md` has
the order.

## What this is

Drag-and-drop normalisation of messy client PDFs and CSVs into strict JSON schemas: resumable presigned S3
multipart uploads, rate-limited Claude extraction with per-field provenance, and a mapping UI where an
operator verifies every field before commit. It exists to prove one resume bullet, quoted in `SPEC.md` §1.

## Hard rules

1. **Stay inside this directory.** Independent git repo; the parent is deliberately not a repo and seven
   sibling projects sit beside it. Never read, write, or `git` above `data-ingestion-pipeline/`.
2. **File bytes never pass through the API.** Presigned multipart, browser → S3 direct. Proxying uploads is
   the exact mistake this design exists to avoid.
3. **Commit gate is enforced server-side.** A document with an unverified required field or a validation
   error returns 409. A disabled button in the UI is not the gate.
4. **Every extracted field carries provenance** (page + bbox, or row + column) and a confidence value. No
   provenance means no verification, which means the mapping UI can't do its job.
5. **Never invent a measurement.** The `[XX]%` comes from a committed run in `eval/results/` with a real
   timed manual arm. There is no way to compute this number without someone doing the work by hand.
6. **Never touch the resume.** Different repo. Don't edit the `.tex`, don't uncomment the GitHub link, and
   don't rewrite the "in pilot use" clause — surface the options and numbers; the user decides.
7. **Keys in `.env` only**, `.env.example` committed empty. Never log an API key or a presigned URL (a
   presigned URL *is* a credential). Never make a bucket public.
8. **Cache extraction calls.** Disk cache keyed by content + schema + model. Re-running the corpus during
   development must not re-bill.

## Environment (this machine: arm64 macOS, 11 cores, 18 GB)

`python3` on the PATH is **3.8.10 and unusable here**. Use `uv` (0.12 installed):

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Frontend: Node 22 / npm 10 installed. Next.js App Router + Tailwind + shadcn/ui.

Services:

```bash
docker compose -f docker-compose.dev.yml up -d      # Postgres + MinIO
alembic upgrade head
```

**MinIO is the dev S3.** It speaks the S3 API including multipart and presigned URLs, so develop against it
and switch by endpoint URL only. Do not let MinIO-specific behaviour leak into the code — and exercise real
AWS S3 at least once (M7), because presigned CORS and multipart limits differ in small ways that only show
up against the real thing.

`.env`: `ANTHROPIC_API_KEY`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.

PDF tooling on arm64: prefer wheels that ship arm64 binaries (`pypdfium2`, `pymupdf`). If a dependency needs
`poppler` or `tesseract`, install via `brew` and record it in the README prerequisites — a reader on Linux
needs to know.

## Ports — this project owns 7800–7899

Up to eight sibling projects may run at once. Never bind outside this block; never bind :3000, :5432,
:8000, :9000, :9001.

| Port | Use |
| --- | --- |
| 7800 | `web/` Next.js app (`next dev -p 7800`) |
| 7801 | API (FastAPI) |
| 7802 | Postgres (→ 5432) |
| 7803 | reserved |
| 7804 | MinIO S3 API (→ 9000) |
| 7805 | MinIO console (→ 9001) |

MinIO CORS must allow `http://localhost:7800` for `PUT` with `ETag` exposed, or browser multipart uploads
fail with an opaque error. Configure it in the compose setup, not by hand.

## Commands

```bash
uvicorn pipeline.api.app:app --reload --port 7801
cd web && npm run dev -- -p 7800
python -m eval.run --corpus eval/corpus --arm tool          # timed tool arm
python -m eval.report --corpus eval/corpus                  # manual vs tool comparison
pytest -q
pytest -q tests/partition/csv                               # the messy-CSV fixture suite
```

## Conventions

- Python 3.12, full type hints, `mypy --strict` on `pipeline/uploads`, `pipeline/schemas`,
  `pipeline/partition`, `pipeline/limits`. Ruff for lint + format.
- Pydantic v2 for API schemas. Target document schemas are **JSON Schema**, not Pydantic models — users
  supply them at runtime, so validation is data-driven.
- One fixture file per messy-CSV pathology listed in `SPEC.md` §5. Add the fixture before the fix, so each
  one is a named regression test.
- The rate limiter is a single component every Claude call goes through — no direct SDK calls scattered
  across modules, or the limits become unenforceable.
- Frontend: server components for reads, route handlers for mutations. shadcn/ui components. The PDF viewer
  with bbox overlays is the highest-risk UI piece — prototype it early, in M2, before the extraction work
  depends on it.
- Structured logs with `document_id`, `partition_id`, `field_path`. Never a presigned URL, never document
  contents (they're client data by premise).
- Commits: imperative, ≤ 72 chars, scoped — `partition: repair duplicate csv headers before typing`.
- Git identity is already set for this repo (`bravimpurohit1305@gmail.com`). Leave it.

## Definition of done, and when to stop

Milestones per `SPEC.md` §13. CI green on push.

**Stop and ask the user** when:

- It's time to settle the **"in pilot use"** wording (`SPEC.md` §1). Do this early — it determines whether
  the deliverable is a real pilot with recruited participants or a timed self-comparison, and that changes
  the schedule. Present the three options with what each requires.
- The manual arm needs participants. Recruiting is the user's call; note that K < 3 must be disclosed in
  the README.
- The corpus needs real client-like documents. Never use actual client data from the user's employer — if
  realistic documents are needed, ask, and prefer public/synthetic sources.
- Real AWS S3 and a bucket are needed (M7) — that's an account action.
- A `SPEC.md` requirement looks wrong, or you want a dependency it doesn't name.

Report honestly, with conditions attached: "32 documents, 2 participants counterbalanced: manual median
14 m 20 s at 94.1 % field accuracy, tool median 3 m 05 s at 97.8 % — 78 % reduction" is the deliverable.
"Cut onboarding time 78 %" with no arms, no N, and no accuracy is not.

---

## Extended stack additions (2026-08-17)

See `SPEC.md` §15–16. This project roughly doubles: it gains a **batch / lakehouse tier** — **Spark
(PySpark)**, **Delta Lake** with Bronze/Silver/Gold, **Databricks**, **Airflow**, **dbt**, **Kafka** arrival
events, **Parquet** — plus **Kubernetes + Helm** with **Nginx**, **AWS KMS**, **GitLab CI + Jenkins**,
**COMPLIANCE.md**, **OpenTelemetry**.

It is now the largest of the eight and probably should not be the first one you start.

**New ports** (same 7800–7899 block): `7806` Spark master UI · `7807` Airflow web · `7808` Kafka (KRaft) ·
`7809` Jenkins · `7810` Jaeger UI · `7811` OTel Collector gRPC · `7812` Spark worker UI.

**New prerequisites:** `pyspark`, `delta-spark`, `apache-airflow`, `dbt-core` + adapter, `aiokafka`,
`pyarrow`, `boto3`, `opentelemetry-sdk`. For M9: `kind` + `helm`. For M10: local Jenkins via
`docker-compose.jenkins.yml`. Spark local mode with a memory cap (`local[4]`, ~2 GB); Airflow and Kafka are
1–2 GB each — put every batch-tier service behind a `--profile batch` so the interactive tier stays light.

**New hard rules:**

9. **Keep the two tiers separate.** A Spark job that calls the interactive API per document is neither tier and
   will be slow and fragile. A batch tier that skips quality gates because "the interactive tier verifies
   things" is unverified data with extra steps. They share the schema registry and extraction prompts, nothing
   else.
10. **Spark must be the honest choice.** A Spark job over 30 documents demonstrates Spark rather than using it.
    The batch corpus is ≥ 5 000 documents (synthesise by mutating a seed corpus if needed) and the size is
    stated in the results.
11. **Solve executor-side rate limiting deliberately.** Naive `mapPartitions` with N executors will blow
    through the Claude API's global limit. Pick a strategy — per-executor token bucket sized to
    `global_limit / num_executors`, or a two-stage prepare-then-call design — and write the trade up in
    `docs/BATCH.md`.
12. **Use the Delta features that justify Delta.** MERGE-based reprocessing (a prompt change updates rows, it
    does not duplicate them), schema evolution on Silver, time-travel provenance on Gold, and an
    OPTIMIZE/VACUUM policy. Without those it's a folder of Parquet with extra dependencies.
13. **Kafka carries events, never file bytes.** Documents move through S3 and Delta. Putting document bytes in
    Kafka is a common and expensive mistake — say so in the README.
14. **Airflow tasks are idempotent.** A re-run or a backfill must not duplicate rows. Airflow submits and
    tracks; Spark is the parallelism.
15. **dbt tests are the quality gate.** A failing test blocks publish to Gold. That gate is what replaces the
    interactive tier's human verification, so it cannot be advisory.
16. **The repo must run end-to-end on local Spark + local Delta**, with no Databricks account. Record which
    environment produced which result.
17. **Never write "SOC 2 compliant" or "HIPAA compliant"** — anywhere, in any phrasing. `COMPLIANCE.md` is
    framed as *"designed against the SOC 2 / HIPAA control boundaries"* with a "not claimed" section. Also
    document the genuine deletion-vs-time-travel tension: `VACUUM` retention bounds how long deleted data
    stays reachable. Stating that trade-off is a stronger signal than pretending it isn't there.
18. **Never use real client data from the user's employer.** Public or synthetic corpora only. No document
    contents in span attributes or logs.
19. **GitLab CI and Jenkins must actually run**, with evidence committed (a linked passing pipeline, a build
    log). An unexercised `Jenkinsfile` is visibly decorative — **delete it rather than ship it**. This is the
    lowest value-per-hour item here; do it last or not at all.
20. **No secrets in `values.yaml`.** Client-side KMS envelope encryption happens **before** S3 upload, so the
    bytes are unreadable even with bucket access, plus SSE-KMS on the bucket.

**New stop-and-ask:** before creating a Databricks account or real AWS resources; before the ≥ 5 000-document
corpus if it needs sourcing decisions; before running the batch stack alongside sibling projects on 18 GB.
