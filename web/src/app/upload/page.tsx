"use client";

import { useCallback, useState, useRef } from "react";
import { useDropzone } from "react-dropzone";
import { computeFileHash, initiateUpload, completeUpload, getUploadParts } from "@/lib/api";

interface UploadFile {
  file: File;
  id?: string;
  uploadId?: string;
  parts?: { part_number: number; presigned_url: string; offset: number; size: number }[];
  completedParts: { part_number: number; etag: string }[];
  progress: number;
  status: "pending" | "uploading" | "paused" | "completing" | "done" | "error";
  error?: string;
}

const CONCURRENCY = 3; // Parallel part uploads

export default function UploadPage() {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [schemaId, setSchemaId] = useState<string>("");
  const abortControllers = useRef<Map<string, AbortController>>(new Map());

  const onDrop = useCallback((acceptedFiles: File[]) => {
    const newFiles: UploadFile[] = acceptedFiles.map((file) => ({
      file,
      completedParts: [],
      progress: 0,
      status: "pending" as const,
    }));
    setFiles((prev) => [...prev, ...newFiles]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "text/csv": [".csv"],
    },
    maxSize: 500 * 1024 * 1024, // 500 MB
  });

  const uploadFile = async (fileIndex: number) => {
    const uploadFile = files[fileIndex];
    if (!uploadFile || !schemaId) return;

    const updateFile = (update: Partial<UploadFile>) => {
      setFiles((prev) =>
        prev.map((f, i) => (i === fileIndex ? { ...f, ...update } : f))
      );
    };

    try {
      updateFile({ status: "uploading" });

      // Compute content hash for dedupe
      const contentHash = await computeFileHash(uploadFile.file);

      // Initiate upload
      const response = await initiateUpload({
        filename: uploadFile.file.name,
        size: uploadFile.file.size,
        mime: uploadFile.file.type || "application/pdf",
        schema_id: schemaId,
        content_hash: contentHash,
      });

      if (response.deduplicated) {
        updateFile({ id: response.document_id, progress: 100, status: "done" });
        return;
      }

      const { document_id, upload_id, parts } = response;
      updateFile({ id: document_id, uploadId: upload_id, parts });

      // Upload parts with concurrency control
      const controller = new AbortController();
      abortControllers.current.set(document_id, controller);

      const completedParts: { part_number: number; etag: string }[] = [];
      let partsUploaded = 0;

      const uploadPart = async (part: { part_number: number; presigned_url: string; offset: number; size: number }) => {
        if (controller.signal.aborted) return;

        const blob = uploadFile.file.slice(part.offset, part.offset + part.size);
        const res = await fetch(part.presigned_url, {
          method: "PUT",
          body: blob,
          signal: controller.signal,
        });

        if (!res.ok) throw new Error(`Part ${part.part_number} upload failed: ${res.status}`);

        const etag = res.headers.get("ETag") || "";
        completedParts.push({ part_number: part.part_number, etag });
        partsUploaded++;

        updateFile({
          completedParts: [...completedParts],
          progress: Math.round((partsUploaded / (parts?.length || 1)) * 100),
        });
      };

      // Upload parts with limited concurrency
      const partsToUpload = parts || [];
      for (let i = 0; i < partsToUpload.length; i += CONCURRENCY) {
        const batch = partsToUpload.slice(i, i + CONCURRENCY);
        await Promise.all(batch.map(uploadPart));
      }

      // Complete the upload
      updateFile({ status: "completing" });
      await completeUpload(document_id, completedParts);
      updateFile({ status: "done", progress: 100 });

      abortControllers.current.delete(document_id);
    } catch (err: any) {
      if (err.name === "AbortError") {
        updateFile({ status: "paused" });
      } else {
        updateFile({ status: "error", error: err.message });
      }
    }
  };

  const resumeUpload = async (fileIndex: number) => {
    const uploadFile = files[fileIndex];
    if (!uploadFile?.id) return;

    const updateFile = (update: Partial<UploadFile>) => {
      setFiles((prev) =>
        prev.map((f, i) => (i === fileIndex ? { ...f, ...update } : f))
      );
    };

    try {
      updateFile({ status: "uploading" });

      // Get which parts are already uploaded (resume support)
      const partStatus = await getUploadParts(uploadFile.id);
      const { completed, missing } = partStatus;

      const completedParts = completed.map((p: any) => ({
        part_number: p.part_number,
        etag: p.etag,
      }));

      if (missing.length === 0) {
        // All parts uploaded, just complete
        updateFile({ status: "completing" });
        await completeUpload(uploadFile.id, completedParts);
        updateFile({ status: "done", progress: 100 });
        return;
      }

      // Upload missing parts
      const controller = new AbortController();
      abortControllers.current.set(uploadFile.id, controller);

      let partsUploaded = completed.length;
      const totalParts = completed.length + missing.length;

      for (const part of missing) {
        if (controller.signal.aborted) break;

        // Find offset from original parts
        const originalPart = uploadFile.parts?.find(
          (p) => p.part_number === part.part_number
        );
        if (!originalPart) continue;

        const blob = uploadFile.file.slice(
          originalPart.offset,
          originalPart.offset + originalPart.size
        );
        const res = await fetch(part.presigned_url, {
          method: "PUT",
          body: blob,
          signal: controller.signal,
        });

        if (!res.ok) throw new Error(`Part ${part.part_number} failed`);

        const etag = res.headers.get("ETag") || "";
        completedParts.push({ part_number: part.part_number, etag });
        partsUploaded++;

        updateFile({
          completedParts: [...completedParts],
          progress: Math.round((partsUploaded / totalParts) * 100),
        });
      }

      // Complete
      updateFile({ status: "completing" });
      await completeUpload(uploadFile.id, completedParts);
      updateFile({ status: "done", progress: 100 });
    } catch (err: any) {
      if (err.name === "AbortError") {
        updateFile({ status: "paused" });
      } else {
        updateFile({ status: "error", error: err.message });
      }
    }
  };

  const pauseUpload = (fileIndex: number) => {
    const uploadFile = files[fileIndex];
    if (uploadFile?.id) {
      const controller = abortControllers.current.get(uploadFile.id);
      controller?.abort();
    }
  };

  const startAll = () => {
    files.forEach((_, i) => {
      if (files[i].status === "pending") {
        uploadFile(i);
      }
    });
  };

  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-6">Upload Documents</h1>

      {/* Schema selector */}
      <div className="mb-6">
        <label className="block text-sm font-medium mb-2">Target Schema ID</label>
        <input
          type="text"
          value={schemaId}
          onChange={(e) => setSchemaId(e.target.value)}
          placeholder="Enter schema UUID"
          className="w-full max-w-md px-3 py-2 border rounded-md text-sm"
        />
      </div>

      {/* Drop zone */}
      <div
        {...getRootProps()}
        className={`border-2 border-dashed rounded-lg p-12 text-center cursor-pointer transition-colors ${
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-muted-foreground/25 hover:border-primary/50"
        }`}
      >
        <input {...getInputProps()} />
        <div className="space-y-2">
          <p className="text-lg font-medium">
            {isDragActive ? "Drop files here" : "Drag & drop PDFs or CSVs"}
          </p>
          <p className="text-sm text-muted-foreground">
            Files upload directly to S3. Resumable across page refreshes. Max 500 MB.
          </p>
        </div>
      </div>

      {/* File list */}
      {files.length > 0 && (
        <div className="mt-8 space-y-4">
          <div className="flex justify-between items-center">
            <h2 className="text-lg font-semibold">{files.length} file(s)</h2>
            <button
              onClick={startAll}
              disabled={!schemaId}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
            >
              Upload All
            </button>
          </div>

          {files.map((f, i) => (
            <div key={i} className="border rounded-lg p-4">
              <div className="flex justify-between items-center mb-2">
                <div>
                  <span className="font-medium text-sm">{f.file.name}</span>
                  <span className="text-xs text-muted-foreground ml-2">
                    ({(f.file.size / 1024 / 1024).toFixed(1)} MB)
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs capitalize px-2 py-1 rounded bg-secondary">
                    {f.status}
                  </span>
                  {f.status === "paused" && (
                    <button
                      onClick={() => resumeUpload(i)}
                      className="text-xs px-2 py-1 bg-primary text-primary-foreground rounded"
                    >
                      Resume
                    </button>
                  )}
                  {f.status === "uploading" && (
                    <button
                      onClick={() => pauseUpload(i)}
                      className="text-xs px-2 py-1 bg-destructive text-destructive-foreground rounded"
                    >
                      Pause
                    </button>
                  )}
                </div>
              </div>
              {/* Progress bar */}
              <div className="w-full bg-secondary rounded-full h-2">
                <div
                  className="bg-primary h-2 rounded-full transition-all duration-300"
                  style={{ width: `${f.progress}%` }}
                />
              </div>
              <div className="flex justify-between mt-1">
                <span className="text-xs text-muted-foreground">
                  {f.completedParts.length}/{f.parts?.length || "?"} parts
                </span>
                <span className="text-xs text-muted-foreground">{f.progress}%</span>
              </div>
              {f.error && (
                <p className="text-xs text-destructive mt-1">{f.error}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
