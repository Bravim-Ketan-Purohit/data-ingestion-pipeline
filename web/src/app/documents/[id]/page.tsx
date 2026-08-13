"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import {
  getDocument,
  getDocumentSource,
  correctField,
  verifyFields,
  commitDocument,
  DocumentDetail,
  DocumentField,
} from "@/lib/api";

/**
 * Mapping / Verify View — the screen the resume bullet is about.
 *
 * Split pane: rendered source (PDF page with bbox overlays, or CSV grid)
 * on one side, target-schema fields on the other.
 *
 * - Clicking a field highlights its source span
 * - Clicking the source jumps to the field
 * - Low-confidence fields sorted first
 * - Validation errors inline
 * - Keyboard-driven accept/correct
 */
export default function DocumentMappingPage() {
  const params = useParams();
  const documentId = params.id as string;

  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string>("");
  const [selectedField, setSelectedField] = useState<string | null>(null);
  const [highlightedSpan, setHighlightedSpan] = useState<any>(null);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>("");
  const [commitError, setCommitError] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDocument();
  }, [documentId]);

  const loadDocument = async () => {
    try {
      const doc = await getDocument(documentId);
      setDocument(doc);
      const source = await getDocumentSource(documentId);
      setSourceUrl(source.url);
    } catch (err) {
      console.error("Failed to load document", err);
    } finally {
      setLoading(false);
    }
  };

  // Bidirectional highlight: click field → highlight source
  const onFieldClick = useCallback((field: DocumentField) => {
    setSelectedField(field.path);
    setHighlightedSpan(field.source_span);
  }, []);

  // Bidirectional highlight: click source → jump to field
  const onSourceSpanClick = useCallback(
    (span: any) => {
      if (!document) return;
      const matchingField = document.fields.find(
        (f) => JSON.stringify(f.source_span) === JSON.stringify(span)
      );
      if (matchingField) {
        setSelectedField(matchingField.path);
        // Scroll to the field in the right pane
        const el = window.document.getElementById(`field-${matchingField.path}`);
        el?.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    },
    [document]
  );

  const handleCorrect = async (path: string) => {
    try {
      await correctField(documentId, path.replace(/^\//, ""), JSON.parse(editValue));
      setEditingField(null);
      await loadDocument();
    } catch (err: any) {
      console.error("Correction failed", err);
    }
  };

  const handleVerify = async (paths: string[]) => {
    await verifyFields(documentId, paths);
    await loadDocument();
  };

  const handleCommit = async () => {
    try {
      setCommitError(null);
      await commitDocument(documentId);
      await loadDocument();
    } catch (err: any) {
      if (err.status === 409) {
        setCommitError(err.errors);
      } else {
        setCommitError([{ message: err.message || "Commit failed" }]);
      }
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!document || !selectedField) return;

      if (e.key === "Enter" && e.metaKey) {
        // Cmd+Enter to verify selected field
        handleVerify([selectedField]);
      }
      if (e.key === "ArrowDown") {
        // Move to next field
        const idx = document.fields.findIndex((f) => f.path === selectedField);
        if (idx < document.fields.length - 1) {
          const next = document.fields[idx + 1];
          setSelectedField(next.path);
          setHighlightedSpan(next.source_span);
        }
      }
      if (e.key === "ArrowUp") {
        // Move to previous field
        const idx = document.fields.findIndex((f) => f.path === selectedField);
        if (idx > 0) {
          const prev = document.fields[idx - 1];
          setSelectedField(prev.path);
          setHighlightedSpan(prev.source_span);
        }
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [document, selectedField]);

  if (loading) {
    return <div className="container mx-auto px-4 py-8">Loading...</div>;
  }

  if (!document) {
    return <div className="container mx-auto px-4 py-8">Document not found</div>;
  }

  const unverifiedRequired = document.fields.filter((f) => !f.verified);
  const hasErrors = document.fields.some((f) => f.validation_error);

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      {/* Header */}
      <div className="border-b p-4 flex justify-between items-center">
        <div>
          <h1 className="font-semibold">{document.filename}</h1>
          <span className="text-sm text-muted-foreground capitalize">{document.state}</span>
        </div>
        <div className="flex gap-2">
          {document.state === "review" && (
            <>
              <button
                onClick={() => handleVerify(document.fields.map((f) => f.path))}
                className="px-3 py-1.5 text-sm border rounded-md hover:bg-secondary"
              >
                Verify All
              </button>
              <button
                onClick={handleCommit}
                disabled={unverifiedRequired.length > 0 || hasErrors}
                className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md disabled:opacity-50"
              >
                Commit
              </button>
            </>
          )}
        </div>
      </div>

      {/* Commit errors */}
      {commitError && (
        <div className="border-b border-destructive/50 bg-destructive/5 p-3">
          <p className="text-sm font-medium text-destructive mb-1">
            Commit blocked ({commitError.length} issue{commitError.length !== 1 ? "s" : ""})
          </p>
          <ul className="text-xs text-destructive space-y-1">
            {commitError.map((err: any, i: number) => (
              <li key={i}>
                {err.path && <span className="font-mono">{err.path}</span>}: {err.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Split pane */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Source viewer with bbox overlays */}
        <div className="w-1/2 border-r overflow-auto p-4">
          <SourceViewer
            url={sourceUrl}
            mime={document.mime}
            partitions={document.partitions}
            highlightedSpan={highlightedSpan}
            onSpanClick={onSourceSpanClick}
          />
        </div>

        {/* Right: Fields list */}
        <div className="w-1/2 overflow-auto p-4">
          <div className="space-y-2">
            {document.fields.map((field) => (
              <FieldCard
                key={field.path}
                field={field}
                isSelected={selectedField === field.path}
                isEditing={editingField === field.path}
                onClick={() => onFieldClick(field)}
                onEdit={() => {
                  setEditingField(field.path);
                  setEditValue(JSON.stringify(field.value, null, 2));
                }}
                onSave={() => handleCorrect(field.path)}
                onVerify={() => handleVerify([field.path])}
                editValue={editValue}
                onEditChange={setEditValue}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Source document viewer with bounding box overlays */
function SourceViewer({
  url,
  mime,
  partitions,
  highlightedSpan,
  onSpanClick,
}: {
  url: string;
  mime: string;
  partitions: any[];
  highlightedSpan: any;
  onSpanClick: (span: any) => void;
}) {
  if (mime === "application/pdf") {
    return (
      <div className="relative">
        <p className="text-sm text-muted-foreground mb-4">
          PDF viewer with bounding box overlays
        </p>
        {/* PDF rendering with bbox overlays */}
        <div className="border rounded-lg bg-white min-h-[600px] relative">
          {url && (
            <iframe
              src={url}
              className="w-full h-[600px] rounded-lg"
              title="Source document"
            />
          )}
          {/* Bbox overlays */}
          {partitions
            .filter((p) => p.bbox)
            .map((partition) => {
              const isHighlighted =
                highlightedSpan &&
                JSON.stringify(partition.bbox) === JSON.stringify(highlightedSpan);
              return (
                <div
                  key={partition.id}
                  className={`absolute border-2 cursor-pointer transition-colors ${
                    isHighlighted
                      ? "border-primary bg-primary/20"
                      : "border-transparent hover:border-muted-foreground/30"
                  }`}
                  style={{
                    left: `${(partition.bbox.x0 / 612) * 100}%`,
                    top: `${(partition.bbox.y0 / 792) * 100}%`,
                    width: `${((partition.bbox.x1 - partition.bbox.x0) / 612) * 100}%`,
                    height: `${((partition.bbox.y1 - partition.bbox.y0) / 792) * 100}%`,
                  }}
                  onClick={() => onSpanClick(partition.bbox)}
                  title={`${partition.kind} (page ${partition.page})`}
                />
              );
            })}
        </div>
      </div>
    );
  }

  // CSV grid view
  return (
    <div className="overflow-auto">
      <p className="text-sm text-muted-foreground mb-4">
        CSV grid with row/column highlighting
      </p>
      <div className="border rounded-lg p-4 bg-white">
        <p className="text-sm">
          {partitions.length} partition(s) detected.
        </p>
        {partitions.map((p) => (
          <div
            key={p.id}
            className={`py-1 px-2 text-xs font-mono rounded cursor-pointer ${
              highlightedSpan && p.row_range === highlightedSpan?.row_range
                ? "bg-primary/20 border border-primary"
                : "hover:bg-secondary"
            }`}
            onClick={() => onSpanClick({ row_range: p.row_range })}
          >
            [{p.kind}] rows {p.row_range || "?"} ({p.content_length} chars)
          </div>
        ))}
      </div>
    </div>
  );
}

/** Individual field card with confidence indicator */
function FieldCard({
  field,
  isSelected,
  isEditing,
  onClick,
  onEdit,
  onSave,
  onVerify,
  editValue,
  onEditChange,
}: {
  field: DocumentField;
  isSelected: boolean;
  isEditing: boolean;
  onClick: () => void;
  onEdit: () => void;
  onSave: () => void;
  onVerify: () => void;
  editValue: string;
  onEditChange: (v: string) => void;
}) {
  const confidenceColor =
    (field.confidence || 0) >= 0.9
      ? "text-green-600"
      : (field.confidence || 0) >= 0.7
        ? "text-yellow-600"
        : "text-red-600";

  return (
    <div
      id={`field-${field.path}`}
      className={`border rounded-lg p-3 cursor-pointer transition-colors ${
        isSelected ? "border-primary bg-primary/5" : "hover:border-muted-foreground/50"
      } ${field.validation_error ? "border-destructive/50" : ""}`}
      onClick={onClick}
    >
      <div className="flex justify-between items-start mb-1">
        <span className="font-mono text-xs text-muted-foreground">{field.path}</span>
        <div className="flex items-center gap-2">
          <span className={`text-xs font-medium ${confidenceColor}`}>
            {field.confidence ? `${(field.confidence * 100).toFixed(0)}%` : "—"}
          </span>
          {field.verified && (
            <span className="text-xs px-1.5 py-0.5 bg-green-100 text-green-700 rounded">
              verified
            </span>
          )}
        </div>
      </div>

      {isEditing ? (
        <div className="space-y-2">
          <textarea
            value={editValue}
            onChange={(e) => onEditChange(e.target.value)}
            className="w-full p-2 text-sm border rounded font-mono"
            rows={3}
          />
          <div className="flex gap-2">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSave();
              }}
              className="text-xs px-2 py-1 bg-primary text-primary-foreground rounded"
            >
              Save
            </button>
          </div>
        </div>
      ) : (
        <div className="text-sm font-medium truncate">
          {JSON.stringify(field.value)}
        </div>
      )}

      {field.validation_error && (
        <p className="text-xs text-destructive mt-1">{field.validation_error}</p>
      )}

      {field.corrected_from !== null && (
        <p className="text-xs text-muted-foreground mt-1">
          Corrected from: {JSON.stringify(field.corrected_from)}
        </p>
      )}

      {!field.verified && !isEditing && (
        <div className="flex gap-2 mt-2">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onVerify();
            }}
            className="text-xs px-2 py-1 border rounded hover:bg-secondary"
          >
            Accept
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onEdit();
            }}
            className="text-xs px-2 py-1 border rounded hover:bg-secondary"
          >
            Correct
          </button>
        </div>
      )}
    </div>
  );
}
