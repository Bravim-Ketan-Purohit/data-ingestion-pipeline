# Study notes — Unstructured Data Ingestion Pipeline

Reference material, carried over from `projects-ref.md`.

## References

### [`Unstructured-IO/unstructured`](https://github.com/Unstructured-IO/unstructured)

The industry standard for turning PDFs into clean text.

**What to study:** the **partitioning logic** — how they identify what is a "Header" vs. a "Table" vs. a
"Paragraph" in a messy PDF. This is the heart of the project.

Read specifically:

- `partition_pdf` and the strategies it offers (`fast`, `hi_res`, `ocr_only`) — and what each trades
  away. Knowing when the cheap path is sufficient is most of the practical skill.
- The **element taxonomy** — their type hierarchy is a well-considered starting point; don't reinvent it.
- **Table extraction**, which is the hardest case. Note how they preserve structure rather than
  flattening to text, and where they still fail.
- How they decide when OCR is needed at all.

### [`mendableai/firecrawl`](https://github.com/mendableai/firecrawl)

Turns entire websites into clean Markdown.

**What to study:** how they handle **API rate limiting and chunking**. Their queue + worker architecture
for large crawls is the model for processing many documents without melting the extraction API, and their
retry/backoff behavior is worth copying rather than reinventing.

Also look at how they normalize wildly inconsistent input into one clean output format — same core
problem as this repo, different input medium.

## Also worth reading

- **Anthropic API docs** — structured output / tool use for schema-constrained extraction, rate limit
  headers, and long-context handling for big documents.
- **S3 multipart upload** + presigned URLs. The important property: bytes go browser → S3 directly. If
  your API proxies uploads, it becomes the bottleneck and a 200MB file will prove it.
- **Token-bucket rate limiting** — enough to implement client-side limiting that respects the API's
  published limits instead of discovering them via 429s.

## Questions to answer before coding

1. A table spans two pages with the header repeated. How is that detected and reassembled?
2. A PDF is a scan with no text layer. How do you know, and what happens then?
3. A CSV has merged headers in the first two rows. How is the real header inferred?
4. Extraction returns a field the operator knows is wrong. How do they see *where* it came from, and how
   many clicks to fix it?
5. 500 documents arrive at once. What stops you from hitting the API rate limit, and what's the queue
   behavior?
6. Confidence per document or per field? Why does the distinction matter for the review UI?
7. What's the honest failure rate on the messiest documents in the corpus, and does the README say so?

## The connection to your work experience

This project is a deliberate echo of the Kinetic Systems intake pipeline on your resume — Gmail
ingestion, OCR, semantic chunking, schema-constrained extraction. That's an advantage: you have real
production experience with this exact problem shape, so you can speak about the failure modes with
authority most candidates won't have.

Lean into it in interviews, and keep the distinction clean: that one was production for a paying
enterprise account; this one is the open-source version you can actually show them.

## Deliberate divergences from the references

| Area | unstructured / firecrawl does | This repo does | Why |
| --- | --- | --- | --- |
| | | | |
