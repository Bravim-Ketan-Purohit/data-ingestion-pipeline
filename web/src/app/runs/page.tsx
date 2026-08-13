"use client";

import { useEffect, useState } from "react";
import { getRuns } from "@/lib/api";

export default function RunsPage() {
  const [stats, setStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadStats();
    const interval = setInterval(loadStats, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, []);

  const loadStats = async () => {
    try {
      const data = await getRuns();
      setStats(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <div className="container mx-auto px-4 py-8">Loading...</div>;

  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-6">Runs & Throughput</h1>

      {stats && (
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          <StatCard
            title="Requests Made"
            value={stats.requests_made}
            description="Total extraction API calls"
          />
          <StatCard
            title="Requests Throttled"
            value={stats.requests_throttled}
            description="Times rate limiter delayed a request"
          />
          <StatCard
            title="Total Wait Time"
            value={`${stats.total_wait_seconds}s`}
            description="Cumulative time waiting on rate limits"
          />
          <StatCard
            title="Total Cost"
            value={`$${stats.total_cost_usd.toFixed(4)}`}
            description={`Ceiling: $${stats.cost_ceiling_usd}`}
          />
          <StatCard
            title="Cost Ceiling"
            value={`$${stats.cost_ceiling_usd}`}
            description="Per-run maximum spend"
          />
          <StatCard
            title="Throughput"
            value={
              stats.total_wait_seconds > 0
                ? `${(stats.requests_made / (stats.total_wait_seconds / 60)).toFixed(1)}/min`
                : "—"
            }
            description="Effective requests per minute"
          />
        </div>
      )}
    </div>
  );
}

function StatCard({ title, value, description }: { title: string; value: string | number; description: string }) {
  return (
    <div className="border rounded-lg p-6">
      <p className="text-sm text-muted-foreground mb-1">{title}</p>
      <p className="text-2xl font-bold">{value}</p>
      <p className="text-xs text-muted-foreground mt-1">{description}</p>
    </div>
  );
}
