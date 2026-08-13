"use client";

import { useEffect, useState } from "react";
import { getSchemas } from "@/lib/api";

export default function SchemasPage() {
  const [schemas, setSchemas] = useState<any[]>([]);
  const [newSchema, setNewSchema] = useState({
    name: "",
    description: "",
    json_schema: '{\n  "type": "object",\n  "properties": {},\n  "required": [],\n  "additionalProperties": false\n}',
  });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadSchemas();
  }, []);

  const loadSchemas = async () => {
    try {
      const data = await getSchemas();
      setSchemas(data);
    } catch (err) {
      console.error(err);
    }
  };

  const handleCreate = async () => {
    setError(null);
    try {
      const parsed = JSON.parse(newSchema.json_schema);
      const res = await fetch("/api/schemas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newSchema.name,
          json_schema: parsed,
          description: newSchema.description || null,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadSchemas();
      setNewSchema({
        name: "",
        description: "",
        json_schema: '{\n  "type": "object",\n  "properties": {},\n  "required": [],\n  "additionalProperties": false\n}',
      });
    } catch (err: any) {
      setError(err.message);
    }
  };

  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-6">Schema Editor</h1>

      {/* Create new schema */}
      <div className="border rounded-lg p-6 mb-8">
        <h2 className="text-lg font-semibold mb-4">Create Schema</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Name</label>
            <input
              type="text"
              value={newSchema.name}
              onChange={(e) => setNewSchema({ ...newSchema, name: e.target.value })}
              className="w-full max-w-md px-3 py-2 border rounded-md text-sm"
              placeholder="e.g. invoice, client_record"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Description</label>
            <input
              type="text"
              value={newSchema.description}
              onChange={(e) => setNewSchema({ ...newSchema, description: e.target.value })}
              className="w-full max-w-md px-3 py-2 border rounded-md text-sm"
              placeholder="What this schema extracts"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">JSON Schema (draft 2020-12)</label>
            <textarea
              value={newSchema.json_schema}
              onChange={(e) => setNewSchema({ ...newSchema, json_schema: e.target.value })}
              className="w-full px-3 py-2 border rounded-md text-sm font-mono h-64"
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <button
            onClick={handleCreate}
            disabled={!newSchema.name || !newSchema.json_schema}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
          >
            Create Schema
          </button>
        </div>
      </div>

      {/* Existing schemas */}
      <h2 className="text-lg font-semibold mb-4">Existing Schemas</h2>
      <div className="space-y-4">
        {schemas.map((schema) => (
          <div key={schema.id} className="border rounded-lg p-4">
            <div className="flex justify-between items-start mb-2">
              <div>
                <h3 className="font-medium">{schema.name}</h3>
                <p className="text-xs text-muted-foreground">
                  v{schema.version} | {schema.id}
                </p>
              </div>
              <span className="text-xs text-muted-foreground">
                {schema.created_at ? new Date(schema.created_at).toLocaleDateString() : ""}
              </span>
            </div>
            {schema.description && (
              <p className="text-sm text-muted-foreground mb-2">{schema.description}</p>
            )}
            <pre className="text-xs bg-secondary p-3 rounded overflow-auto max-h-48">
              {JSON.stringify(schema.json_schema, null, 2)}
            </pre>
          </div>
        ))}
        {schemas.length === 0 && (
          <p className="text-sm text-muted-foreground">No schemas created yet.</p>
        )}
      </div>
    </div>
  );
}
