# Unstructured Data Ingestion Pipeline

Drag-and-drop normalization of messy client PDFs and CSVs into **strict JSON schemas**: chunked uploads
to S3, rate-limited extraction calls, and an **interactive mapping UI** to verify extracted fields before
commit.

**Stack:** Next.js · FastAPI · Claude API · AWS S3
**Resume target:** `Bravim_Purohit_FDE.tex` → Projects & Publications
**Role:** Forward Deployed Engineer

---

## The claim this repo must prove

> Drag-and-drop normalization of messy client PDFs and CSVs into strict JSON schemas: chunked uploads to
> S3, rate-limited extraction calls, and an interactive mapping UI to verify extracted fields before
> commit. Cut client onboarding time **[XX]%** in pilot use.

## Benchmarks this repo owes the resume

| Metric | Resume placeholder | Manual baseline | With pipeline | Method |
| --- | --- | --- | --- | --- |
| Client onboarding time | `[XX]%` reduction | — | — | TBD |

"Pilot use" implies real users, which most side projects don't have. Options, in descending order of
credibility:

1. **Real pilot** — even two or three people onboarding a real document set. Timed. Best evidence by far.
2. **Timed self-comparison** — do the same onboarding manually, stopwatch running, then through the
   pipeline. Document the corpus and the task. Legitimate if you say plainly that it's a self-comparison.
3. **Reword the bullet.** If neither is achievable, change the claim to something you *can* support —
   extraction accuracy against a hand-labeled set, or field-level precision/recall. A defensible smaller
   claim beats an impressive one that dissolves under a follow-up question.

Also track regardless: **extraction accuracy per field type**, and how often the mapping UI's suggestion
was correct.

**Do not uncomment** the GitHub link at `Bravim_Purohit_FDE.tex:142` until this is resolved and the repo
is public.

## Architecture

```
 browser: drag & drop
    │
    │ 1. request presigned URL
    ▼
 ┌──────────────┐          2. chunked upload (direct)       ┌────────┐
 │   FastAPI    │ ◄─────────────── never proxies bytes ────►│   S3   │
 └──────┬───────┘                                          └────┬───┘
        │ 3. enqueue job                                        │
        ▼                                                       │
 ┌────────────────── worker ──────────────────┐                 │
 │  partition document ◄──────────────────────┼─────────────────┘
 │    ├─ header / table / paragraph           │
 │    └─ layout-aware chunking                │
 │  extract → target schema (Claude)          │
 │  rate-limited + retried                    │
 │  confidence per field                      │
 └────────────────┬───────────────────────────┘
                  ▼
 ┌────────────────────────────────────────────┐
 │  mapping UI: source span ↔ target field    │
 │  low-confidence flagged for review         │
 │  operator corrects → COMMIT                │
 └────────────────┬───────────────────────────┘
                  ▼
        strict, validated JSON out
```

## The three real engineering problems

**1. Partitioning, not just text extraction.** Pulling raw text from a PDF is a solved, boring problem.
Knowing that *this* block is a header, *that* is a table, and *this* is a paragraph is what makes
downstream extraction work — a table flattened into prose loses the row/column relationships that
carried the meaning. This is the core of what to study in `unstructured`.

**2. Uploads that survive reality.** Chunked, resumable, presigned direct-to-S3. Bytes never proxy
through the API. A 200MB scanned PDF over hotel wifi is the test case, and "the upload failed at 90%,
start over" is the failure that makes a tool feel unusable.

**3. Verification before commit, with provenance.** Every extracted field should point back to the span
it came from, so an operator can check it in one glance instead of re-reading the document. Confidence
scores decide what gets flagged. The UI is not decoration here — for messy real-world documents,
extraction will be wrong sometimes, and the verification step is what makes the tool trustworthy anyway.

## Why this fits the FDE role

Every enterprise deployment starts the same way: the customer has data in a format nobody designed for
machines, and it has to become clean structured records before anything else can happen. Onboarding
friction is the thing that kills deals. Building the tool that removes it is forward-deployed work, and
this project is a direct echo of the intake pipeline in your Kinetic Systems experience — which makes it
easy to talk about with real authority.

## Getting started

```bash
# backend
python -m venv .venv && source .venv/bin/activate   # needs Python 3.11+
pip install -r api/requirements.txt
cp .env.example .env                                # ANTHROPIC_API_KEY, S3 bucket, AWS creds
uvicorn api.main:app --reload

# frontend
cd web && npm install && npm run dev
```

## Layout

```
api/            FastAPI, presigned URLs, job orchestration
partition/      PDF/CSV → typed elements (header/table/paragraph)
extract/        schema-constrained extraction, rate limiting, retries
schemas/        target JSON schemas + validation
web/            Next.js drag-drop upload + mapping UI
eval/           hand-labeled set, per-field accuracy
docs/STUDY.md   notes from unstructured and firecrawl
```

## Documents

| File | What it's for |
| --- | --- |
| [SPEC.md](SPEC.md) | **Authoritative** technical specification — what to build, the data model, the measurement protocol, and the honest-claims register |
| [ROADMAP.md](ROADMAP.md) | Build order, milestone by milestone |
| [CLAUDE.md](CLAUDE.md) | Operating rules for a coding session here: environment, ports, conventions, when to stop and ask |
| [docs/STUDY.md](docs/STUDY.md) | What to read in the reference implementations before writing code |

Where `SPEC.md` and any other document disagree, `SPEC.md` wins.

## Status

Implemented. Chunked upload, extraction, schema mapping and validation, encryption, export, and the
batch paths (`batch/kafka`, `batch/spark`, `batch/airflow`) are built, with the mapping UI in `web/` and 21
test files. **The onboarding-time reduction is not measured**: that claim is a comparison against a manual
baseline, and the manual arm has to be timed with real people before any percentage exists. This repo reserves ports **7800–7899**; up to eight sibling
projects may run at the same time, so nothing here binds outside that block.
