/**
 * API client for the data ingestion pipeline.
 * All requests go through Next.js rewrites to localhost:7801.
 */

const API_BASE = "/api";

export interface UploadInitResponse {
  document_id: string;
  upload_id?: string;
  parts?: { part_number: number; presigned_url: string; offset: number; size: number }[];
  deduplicated: boolean;
  state?: string;
}

export interface DocumentField {
  id: string;
  path: string;
  value: unknown;
  confidence: number | null;
  source_partition_id: string | null;
  source_span: { x0: number; y0: number; x1: number; y1: number } | { row: number; col: number } | null;
  verified: boolean;
  corrected_from: unknown;
  corrected_by: string | null;
  corrected_at: string | null;
  validation_error: string | null;
}

export interface DocumentPartition {
  id: string;
  ordinal: number;
  kind: string;
  page: number | null;
  bbox: { x0: number; y0: number; x1: number; y1: number } | null;
  row_range: string | null;
  content_length: number;
}

export interface DocumentDetail {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number;
  state: string;
  schema_id: string;
  cost_usd: number;
  created_at: string;
  committed_at: string | null;
  partitions: DocumentPartition[];
  fields: DocumentField[];
}

export async function initiateUpload(data: {
  filename: string;
  size: number;
  mime: string;
  schema_id: string;
  content_hash: string;
}): Promise<UploadInitResponse> {
  const res = await fetch(`${API_BASE}/uploads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getUploadParts(documentId: string) {
  const res = await fetch(`${API_BASE}/uploads/${documentId}/parts`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function completeUpload(documentId: string, parts: { part_number: number; etag: string }[]) {
  const res = await fetch(`${API_BASE}/uploads/${documentId}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parts }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getDocument(documentId: string): Promise<DocumentDetail> {
  const res = await fetch(`${API_BASE}/documents/${documentId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getDocumentSource(documentId: string) {
  const res = await fetch(`${API_BASE}/documents/${documentId}/source`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function correctField(documentId: string, fieldPath: string, value: unknown) {
  const res = await fetch(`${API_BASE}/documents/${documentId}/fields/${fieldPath}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function verifyFields(documentId: string, paths: string[]) {
  const res = await fetch(`${API_BASE}/documents/${documentId}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function commitDocument(documentId: string) {
  const res = await fetch(`${API_BASE}/documents/${documentId}/commit`, {
    method: "POST",
  });
  if (res.status === 409) {
    const data = await res.json();
    throw { status: 409, errors: data.detail?.errors || [] };
  }
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getSchemas() {
  const res = await fetch(`${API_BASE}/schemas`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getRuns() {
  const res = await fetch(`${API_BASE}/runs`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getTimingComparison() {
  const res = await fetch(`${API_BASE}/timing/comparison`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * Compute SHA-256 hash of a file for content-based deduplication.
 */
export async function computeFileHash(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map((b) => b.toString(16).padStart(2, "0")).join("");
}
