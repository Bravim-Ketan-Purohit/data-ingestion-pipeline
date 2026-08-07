# Roadmap — Unstructured Data Ingestion Pipeline

Start with the ugliest documents you can find, not clean ones. A pipeline built against tidy PDFs and
then pointed at a scanned fax falls apart, and the scanned fax is the real use case.

## M1 — Collect the hard corpus first

- [ ] Gather deliberately messy documents: multi-column PDFs, scanned/OCR-needed, tables spanning pages,
      CSVs with merged headers, inconsistent date formats, mid-file encoding changes
- [ ] Define 2–3 target JSON schemas
- [ ] **Hand-label ground truth** for a subset — this is the eval set, and it must exist before tuning
- [ ] Note which documents you expect to fail; being right about that is a good sign

## M2 — Upload that doesn't break

- [ ] Presigned S3 URLs; **bytes never proxy through the API**
- [ ] Chunked, resumable multipart upload
- [ ] Progress reporting to the browser
- [ ] Test: kill the network at 90% → resume, don't restart
- [ ] Test: 200MB scanned PDF
- [ ] File type + size validation server-side, not just in the UI

## M3 — Partitioning

- [ ] PDF → typed elements: header, paragraph, table, list, footer
- [ ] Table structure preserved as rows/columns, **not flattened to prose**
- [ ] CSV → header inference, type inference per column
- [ ] OCR path for scanned documents
- [ ] Layout-aware chunking that respects element boundaries
- [ ] Every element retains a **source span** (page, bbox / row) for provenance

## M4 — Extraction

- [ ] Schema-constrained extraction against the target schema
- [ ] **Confidence per field**, not per document
- [ ] Rate limiting with token-bucket + exponential backoff on 429
- [ ] Retry with jitter; partial-failure handling that doesn't lose the whole document
- [ ] Cost tracked per document
- [ ] Score against the M1 labeled set: **per-field-type accuracy**

## M5 — Mapping and verification UI

- [ ] Drag-and-drop upload with live progress
- [ ] Side-by-side: source document with the span highlighted ↔ extracted field
- [ ] Low-confidence fields visually flagged for review
- [ ] Inline correction, with corrections recorded (they're training signal and an accuracy measurement)
- [ ] Explicit **commit** step — nothing is final until an operator confirms
- [ ] Schema validation on commit; validation errors shown per field, not as one blob

## M6 — Measure the claim

- [ ] Time a manual onboarding of the same corpus, stopwatch running
- [ ] Time it through the pipeline
- [ ] If any real pilot users are reachable, time them — far better evidence
- [ ] **Fill the Benchmarks table**, stating plainly whether it's a pilot or a self-comparison
- [ ] If neither is achievable, **reword the resume bullet** to a claim the eval set supports

## M7 — Demo-ready

- [ ] Sample documents bundled so anyone can try it in 30 seconds
- [ ] Screenshots / short recording in the README
- [ ] Honest "known limitations" section — which document types still fail
- [ ] CI green
- [ ] Flip repo public, then uncomment `Bravim_Purohit_FDE.tex:142`

## Gate before the resume link goes live

`[XX]%` either measured or the bullet reworded — **not left bracketed** · per-field accuracy on a
hand-labeled set · resumable upload proven against a killed connection · known limitations stated.
