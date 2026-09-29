"use client";
import Link from "next/link";
import { useState } from "react";
import { Info } from "lucide-react";
import { basisZh } from "@/lib/basis-zh";
import { useI18n } from "@/lib/i18n";
import { money, moneyShort, num, pct } from "@/lib/format";

export type MetricRow = { scope: string; scope_id: string; metric: string; value: number | null; low: number | null; high: number | null;
  n: number | null; unit: string; formula: string; basis: string; caveat: string };

export function fmt(v: number | null | undefined, unit: string): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (unit.startsWith("USD/month")) return moneyShort(v);
  if (unit.startsWith("USD")) return money(v, 2);
  if (unit.startsWith("share") || unit === "probability") return pct(v, v < 0.1 ? 1 : 0);
  if (unit.startsWith("HHI")) return num(v);
  if (unit === "0-1") return v.toFixed(2);
  if (unit === "0-100") return v.toFixed(0);
  if (unit === "stars") return v.toFixed(1);
  return num(v, v < 10 ? 1 : 0);
}

export function interval(r: Pick<MetricRow, "low" | "high" | "unit">): string | null {
  if (r.low == null || r.high == null) return null;
  return `${fmt(r.low, r.unit)} – ${fmt(r.high, r.unit)}`;
}

/** ⓘ popover: what the number means, its interval and n, formula id (links to the methodology), basis, caveat. */
export function Explain({ row, metric, className }: { row?: MetricRow | null; metric?: string; className?: string }) {
  const { t, lang } = useI18n();
  const tr = (x: string) => (lang === "zh" ? basisZh(x) : x);
  const [open, setOpen] = useState(false);
  const id = row?.metric ?? metric ?? "";
  const name = t(`metric.${id}.0`);
  const expl = t(`metric.${id}.1`);
  return (
    <span className={`relative inline-block ${className ?? ""}`}>
      <button onClick={(e) => { e.stopPropagation(); setOpen(!open); }} className="align-middle text-ink-3 hover:text-accent" aria-label={t("common.why")}>
        <Info className="h-3.5 w-3.5" />
      </button>
      {open && (
        <span className="absolute left-0 top-5 z-50 block w-80 rounded-lg border border-line bg-panel p-3 text-left text-xs font-normal normal-case tracking-normal text-ink-2 shadow-xl"
          onClick={(e) => e.stopPropagation()}>
          <span className="block text-sm font-semibold text-ink">{name.startsWith("metric.") ? id : name}</span>
          {!expl.startsWith("metric.") && <span className="mt-1 block">{expl}</span>}
          {row && <>
            <span className="mt-2 block font-mono text-ink">{fmt(row.value, row.unit)}{interval(row) ? `  (${t("common.interval")} ${interval(row)})` : ""}</span>
            {row.n != null && <span className="block">{t("common.sampleSize")} = {num(row.n)}</span>}
            {row.basis && <span className="mt-1 block"><b>{t("common.basis")}:</b> {tr(row.basis)}</span>}
            {row.caveat && <span className="mt-1 block text-warn"><b>{t("common.caveat")}:</b> {tr(row.caveat)}</span>}
            {row.formula && <Link href={`/methodology#${row.formula}`} className="mt-1 block text-accent">{t("common.formula")} {row.formula} →</Link>}
          </>}
          <button className="mt-2 text-ink-3 hover:text-ink" onClick={() => setOpen(false)}>{t("common.close")}</button>
        </span>
      )}
    </span>
  );
}

/** Headline number with interval and its explanation. */
export function MetricTile({ row, label, sub }: { row?: MetricRow | null; label?: string; sub?: React.ReactNode }) {
  const { t } = useI18n();
  const id = row?.metric ?? "";
  const name = label ?? (t(`metric.${id}.0`).startsWith("metric.") ? id : t(`metric.${id}.0`));
  return (
    <div className="p-4">
      <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{name} {row && <Explain row={row} />}</div>
      <div className="mt-1 font-mono text-xl text-ink">{row ? fmt(row.value, row.unit) : "—"}</div>
      <div className="mt-0.5 text-[11px] text-ink-3">{row && interval(row) ? `${t("common.interval")} ${interval(row)}` : ""}{sub ? <> {sub}</> : null}</div>
    </div>
  );
}

/** 0..1 component bar with its name and explanation. */
export function ComponentBar({ id, value }: { id: string; value: number | null | undefined }) {
  const { t } = useI18n();
  return (
    <div className="text-xs">
      <div className="flex items-center justify-between"><span className="flex items-center gap-1">{t(`metric.${id}.0`)} <Explain metric={id} /></span>
        <span className="font-mono">{value == null ? "—" : value.toFixed(2)}</span></div>
      <div className="mt-0.5 h-1.5 rounded-full bg-panel-2">
        {value != null && <div className="h-1.5 rounded-full bg-accent" style={{ width: `${Math.max(2, value * 100)}%` }} />}
      </div>
    </div>
  );
}
