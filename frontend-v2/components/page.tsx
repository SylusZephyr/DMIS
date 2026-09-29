import * as React from "react";

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="fade-up flex flex-wrap items-end justify-between gap-3 px-6 pb-4 pt-6">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-ink-3">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="mx-6 rounded-lg border border-bad/40 bg-bad/10 px-4 py-3 text-sm text-bad">
      Could not reach the platform API. Start it with <code className="font-mono">python scripts/dmis.py serve</code>.
      <div className="mt-1 text-xs opacity-80">{error}</div>
    </div>
  );
}
