// shadcn/ui-style primitives (copied-in components, themed to the command center).
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("glass rounded-2xl border border-line glow", className)} {...p} />;
}
export function CardHeader({ title, subtitle, right }: { title: React.ReactNode; subtitle?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          <span aria-hidden className="accent-bar h-3 w-0.5 shrink-0 rounded-full" />{title}</div>
        {subtitle && <div className="mt-0.5 text-xs text-ink-3">{subtitle}</div>}
      </div>
      {right}
    </div>
  );
}

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-40 disabled:pointer-events-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
  {
    variants: {
      variant: {
        primary: "bg-accent text-on-accent shadow-[0_0_0_1px_var(--accent),0_8px_24px_-10px_var(--accent)] hover:bg-accent-hover",
        ghost: "text-ink-2 hover:bg-panel-2 hover:text-ink",
        outline: "border border-line text-ink hover:border-accent hover:text-accent",
      },
      size: { sm: "h-8 min-w-8 px-3", md: "h-9 px-4", lg: "h-11 px-5" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);
export function Button({ className, variant, size, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button>) {
  return <button className={cn(button({ variant, size }), className)} {...p} />;
}

export function Input(p: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...p}
      className={cn("h-9 w-full rounded-lg border border-line bg-panel-2 px-3 text-sm text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none", p.className)}
    />
  );
}

export function Select({ className, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...p}
      className={cn("h-9 rounded-lg border border-line bg-panel-2 px-2 text-sm text-ink focus:border-accent focus:outline-none", className)}
    />
  );
}

export function Badge({ children, color, className }: { children: React.ReactNode; color?: string; className?: string }) {
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", className)}
      style={{ borderColor: color ?? "var(--line)", color: color ?? "var(--ink-2)" }}
    >
      {children}
    </span>
  );
}

export function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{label}</div>
      <div className={cn("mt-1 truncate font-mono text-ink", typeof value === "string" && value.length > 9 ? "text-base leading-7" : "text-xl")}
        title={typeof value === "string" ? value : undefined}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-ink-3">{sub}</div>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <div className="text-sm font-medium text-ink-2">{title}</div>
      {children && <div className="max-w-md text-xs text-ink-3">{children}</div>}
    </div>
  );
}

export function Meter({ value, max = 1, color = "var(--accent)" }: { value: number | null | undefined; max?: number; color?: string }) {
  const w = value == null ? 0 : Math.max(0, Math.min(1, value / max)) * 100;
  return (
    <div className="h-1.5 w-full rounded-full bg-panel-2">
      <div className="h-full rounded-full" style={{ width: `${w}%`, background: color }} />
    </div>
  );
}

/** Screen-reader announcement for async work: says `busyText` while `busy`, then `doneText` once a result exists.
 *  Visually hidden; place it next to the control that starts the work. */
export function LiveStatus({ busy, done, busyText, doneText }: { busy: boolean; done: boolean; busyText: string; doneText: string }) {
  return <span role="status" aria-live="polite" className="sr-only">{busy ? busyText : done ? doneText : ""}</span>;
}

/** Loading placeholder with a moving sheen. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("skeleton h-4", className)} />;
}
