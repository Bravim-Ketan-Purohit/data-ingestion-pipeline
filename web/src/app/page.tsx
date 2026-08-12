export default function Home() {
  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="text-3xl font-bold mb-4">Data Ingestion Pipeline</h1>
      <p className="text-muted-foreground mb-8">
        Drag-and-drop normalization of messy client PDFs and CSVs into strict JSON schemas.
      </p>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        <a
          href="/upload"
          className="group rounded-lg border p-6 hover:border-primary transition-colors"
        >
          <h2 className="text-xl font-semibold mb-2 group-hover:text-primary">
            Upload Documents
          </h2>
          <p className="text-sm text-muted-foreground">
            Drag and drop PDFs or CSVs. Chunked, resumable uploads direct to S3.
          </p>
        </a>

        <a
          href="/documents"
          className="group rounded-lg border p-6 hover:border-primary transition-colors"
        >
          <h2 className="text-xl font-semibold mb-2 group-hover:text-primary">
            Review & Map
          </h2>
          <p className="text-sm text-muted-foreground">
            Verify extracted fields with bidirectional click-to-highlight.
          </p>
        </a>

        <a
          href="/schemas"
          className="group rounded-lg border p-6 hover:border-primary transition-colors"
        >
          <h2 className="text-xl font-semibold mb-2 group-hover:text-primary">
            Schema Editor
          </h2>
          <p className="text-sm text-muted-foreground">
            Define target JSON schemas with field descriptions and validation rules.
          </p>
        </a>

        <a
          href="/runs"
          className="group rounded-lg border p-6 hover:border-primary transition-colors"
        >
          <h2 className="text-xl font-semibold mb-2 group-hover:text-primary">
            Runs & Costs
          </h2>
          <p className="text-sm text-muted-foreground">
            Throughput, rate-limit throttling events, cost per document.
          </p>
        </a>

        <a
          href="/timing"
          className="group rounded-lg border p-6 hover:border-primary transition-colors"
        >
          <h2 className="text-xl font-semibold mb-2 group-hover:text-primary">
            Timing Comparison
          </h2>
          <p className="text-sm text-muted-foreground">
            Manual vs tool arm results — the resume number.
          </p>
        </a>
      </div>
    </div>
  );
}
