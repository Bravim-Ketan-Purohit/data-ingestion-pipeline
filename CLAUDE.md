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
