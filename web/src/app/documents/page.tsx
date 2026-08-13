"use client";

import { useEffect, useState } from "react";

interface DocumentSummary {
  id: string;
  filename: string;
  state: string;
  mime: string;
  created_at: string;
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDocuments();
  }, []);

  const loadDocuments = async () => {
    try {
      // This would use a list documents endpoint
      // For now, show the page structure
      setLoading(false);
    } catch (err) {
      setLoading(false);
    }
  };

  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-6">Documents</h1>
      <p className="text-muted-foreground mb-4">
        Review and verify extracted fields. Click a document to open the mapping view.
      </p>

      {loading ? (
        <p>Loading...</p>
      ) : documents.length === 0 ? (
        <div className="border-2 border-dashed rounded-lg p-12 text-center">
          <p className="text-lg text-muted-foreground">No documents yet.</p>
          <p className="text-sm text-muted-foreground mt-2">
            Upload documents to begin extraction and review.
          </p>
          <a
            href="/upload"
            className="inline-block mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm"
          >
            Upload Documents
          </a>
        </div>
      ) : (
        <div className="space-y-2">
          {documents.map((doc) => (
            <a
              key={doc.id}
              href={`/documents/${doc.id}`}
              className="block border rounded-lg p-4 hover:border-primary transition-colors"
            >
              <div className="flex justify-between items-center">
                <div>
                  <p className="font-medium">{doc.filename}</p>
                  <p className="text-xs text-muted-foreground">{doc.mime}</p>
                </div>
                <span
                  className={`text-xs px-2 py-1 rounded ${
                    doc.state === "committed"
                      ? "bg-green-100 text-green-700"
                      : doc.state === "review"
                        ? "bg-yellow-100 text-yellow-700"
                        : doc.state === "failed"
                          ? "bg-red-100 text-red-700"
                          : "bg-secondary"
                  }`}
                >
                  {doc.state}
                </span>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
