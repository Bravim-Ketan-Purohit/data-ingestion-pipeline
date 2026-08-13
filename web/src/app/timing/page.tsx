"use client";

import { useEffect, useState } from "react";
import { getTimingComparison } from "@/lib/api";

export default function TimingPage() {
  const [comparison, setComparison] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadComparison();
  }, []);

  const loadComparison = async () => {
    try {
      const data = await getTimingComparison();
      setComparison(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className="container mx-auto px-4 py-8">Loading...</div>;

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
  };

  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-2">Manual vs Tool Timing</h1>
      <p className="text-muted-foreground mb-8">
        Counterbalanced comparison from the measurement protocol (SPEC §9).
        This is the resume number.
      </p>

      {comparison && (
        <>
          {/* The headline number */}
          {comparison.reduction_percent > 0 && (
            <div className="border-2 border-primary rounded-lg p-8 text-center mb-8">
              <p className="text-5xl font-bold text-primary">
                {comparison.reduction_percent}%
              </p>
              <p className="text-lg text-muted-foreground mt-2">
                reduction in document normalization time
              </p>
              <p className="text-sm text-muted-foreground mt-1">
                N={comparison.total_documents} documents, K={comparison.participant_count} participants
              </p>
            </div>
          )}

          {/* Comparison table */}
          <div className="border rounded-lg overflow-hidden mb-8">
            <table className="w-full text-sm">
              <thead className="bg-secondary">
                <tr>
                  <th className="text-left p-3">Metric</th>
                  <th className="text-right p-3">Manual Arm</th>
                  <th className="text-right p-3">Tool Arm</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t">
                  <td className="p-3">Documents timed</td>
                  <td className="text-right p-3">{comparison.manual.count}</td>
                  <td className="text-right p-3">{comparison.tool.count}</td>
                </tr>
                <tr className="border-t">
                  <td className="p-3 font-medium">Median time</td>
                  <td className="text-right p-3 font-medium">
                    {formatTime(comparison.manual.median_seconds)}
                  </td>
                  <td className="text-right p-3 font-medium">
                    {formatTime(comparison.tool.median_seconds)}
                  </td>
                </tr>
                <tr className="border-t">
                  <td className="p-3">Total time</td>
                  <td className="text-right p-3">
                    {formatTime(comparison.manual.total_seconds)}
                  </td>
                  <td className="text-right p-3">
                    {formatTime(comparison.tool.total_seconds)}
                  </td>
                </tr>
                <tr className="border-t">
                  <td className="p-3">Mean accuracy</td>
                  <td className="text-right p-3">
                    {comparison.manual.mean_accuracy
                      ? `${(comparison.manual.mean_accuracy * 100).toFixed(1)}%`
                      : "—"}
                  </td>
                  <td className="text-right p-3">
                    {comparison.tool.mean_accuracy
                      ? `${(comparison.tool.mean_accuracy * 100).toFixed(1)}%`
                      : "—"}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* Methodology note */}
          <div className="bg-secondary/50 rounded-lg p-4 text-sm text-muted-foreground">
            <p className="font-medium text-foreground mb-2">Methodology</p>
            <p>
              Formula: (manual_median - tool_median) / manual_median x 100.
              Counterbalanced design: same participant does set A manually and set B with the tool,
              assignment reversed across participants. Accuracy measured in both arms against ground truth.
            </p>
            {comparison.participant_count < 3 && (
              <p className="mt-2 text-destructive">
                Note: K {"<"} 3 participants. Per-participant numbers reported rather than pooled average.
              </p>
            )}
          </div>
        </>
      )}

      {comparison && comparison.manual.count === 0 && comparison.tool.count === 0 && (
        <div className="border-2 border-dashed rounded-lg p-8 text-center">
          <p className="text-lg text-muted-foreground">No timing data yet.</p>
          <p className="text-sm text-muted-foreground mt-2">
            Run the measurement protocol to fill the Benchmarks table.
          </p>
        </div>
      )}
    </div>
  );
}
