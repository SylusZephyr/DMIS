"use client";
import { useState } from "react";
import { Upload } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input, Skeleton } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post } from "@/lib/api";
import { money, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Sale = { asin: string; title: string | null; period_start: string; period_end: string; days: number; units: number;
  units_month: number; revenue: number | null; sessions: number | null; conversion: number | null; source_name: string | null };
type CheckRow = { asin: string; title: string | null; period_start: string; period_end: string; actual_units_month: number;
  status: string; market?: string; observed_at?: string; days_apart?: number; estimate?: number; estimate_kind?: string;
  ratio_actual_to_estimate?: number | null; estimate_interval?: [number, number | null]; within_interval?: boolean };
type Check = { rows: CheckRow[]; compared: number; median_ratio?: number | null; ratio_p25_p75?: [number, number] | null;
  badge_compared?: number; within_interval_share?: number | null };

function lastMonth(): [string, string] {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0));
  return [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
}

export default function OwnSales() {
  const { t } = useI18n();
  const mn = useMarketName();
  const [def0, def1] = lastMonth();
  const [file, setFile] = useState<File | null>(null);
  const [start, setStart] = useState(def0);
  const [end, setEnd] = useState(def1);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ver, setVer] = useState(0);
  const sales = useApi<{ rows: Sale[] }>("/own-sales", [ver]);
  const check = useApi<Check>("/own-sales/estimate-check", [ver]);
  const c = check.data;

  const upload = async () => {
    if (!file) return;
    setBusy(true); setErr(null); setMsg(null);
    const fd = new FormData();
    fd.append("file", file); fd.append("period_start", start); fd.append("period_end", end);
    try {
      const r = await post<{ rows: number; period: [string, string] }>("/own-sales", fd);
      setMsg(t("own.done", { n: r.rows, s: r.period[0], e: r.period[1] })); setFile(null); setVer((v) => v + 1);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const compared = (c?.rows ?? []).filter((r) => r.status === "compared");
  const others = (c?.rows ?? []).filter((r) => r.status !== "compared");
  return (
    <div className="pb-8">
      <PageHeader title={t("own.title")} subtitle={t("own.subtitle")} />
      <ErrorNote error={sales.error ?? check.error} />
      <div className="space-y-5 px-4 md:px-6">
        <Card>
          <CardHeader title={t("own.upload")} subtitle={t("own.uploadSub")} />
          <div className="flex flex-wrap items-end gap-3 p-4 text-sm">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-1.5 hover:border-accent">
              <Upload className="h-4 w-4" aria-hidden />{file ? file.name : t("own.choose")}
              <input type="file" accept=".csv,.xlsx,.xls,.tsv" className="sr-only" aria-label={t("own.file")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <label className="block">{t("own.start")}<Input className="mt-1" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
            <label className="block">{t("own.end")}<Input className="mt-1" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
            <Button onClick={upload} disabled={!file || busy || !start || !end} aria-busy={busy}>{t("own.import")}</Button>
          </div>
          {msg && <p role="status" className="px-4 pb-3 text-sm text-good">{msg}</p>}
          {err && <p role="alert" className="px-4 pb-3 text-sm text-bad">{err}</p>}
        </Card>

        <Card>
          <CardHeader title={t("own.check")} subtitle={t("own.checkSub")} />
          {!c ? <Skeleton className="m-4 h-24 rounded-xl" /> : c.compared === 0 ? <Empty title={t("own.noCheck")} /> : (<>
            <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-3">
              <div className="rounded-xl border border-line p-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{t("own.median")}</div>
                <div className="mt-1 font-mono text-2xl text-ink">{c.median_ratio != null ? `${c.median_ratio.toFixed(2)}×` : "—"}</div>
                {c.ratio_p25_p75 && <div className="text-[11px] text-ink-3">{t("own.iqr", { a: `${c.ratio_p25_p75[0].toFixed(2)}×`, b: `${c.ratio_p25_p75[1].toFixed(2)}×` })}</div>}
              </div>
              <div className="rounded-xl border border-line p-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{t("own.within")}</div>
                <div className="mt-1 font-mono text-2xl text-ink">{c.within_interval_share != null ? pct(c.within_interval_share, 0) : "—"}</div>
                {!!c.badge_compared && <div className="text-[11px] text-ink-3">n = {c.badge_compared}</div>}
              </div>
              <div className="rounded-xl border border-line p-3">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{t("own.compared")}</div>
                <div className="mt-1 font-mono text-2xl text-ink">{num(c.compared)}</div>
              </div>
            </div>
            <p className="px-4 pb-2 text-xs text-ink-3">{t("own.ratioNote")}</p>
          </>)}
          {(compared.length > 0 || others.length > 0) && (
            <div className="overflow-x-auto border-t border-line" tabIndex={0} role="region" aria-label={t("own.check")}>
              <table className="w-full text-sm">
                <thead className="text-left text-[10px] uppercase tracking-wide text-ink-3 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-2">
                  <tr><th scope="col">{t("own.col.asin")}</th><th scope="col">{t("own.col.period")}</th>
                    <th scope="col" className="text-right">{t("own.col.actual")}</th><th scope="col" className="text-right">{t("own.col.estimate")}</th>
                    <th scope="col" className="text-right">{t("own.col.ratio")}</th><th scope="col">{t("own.col.status")}</th></tr>
                </thead>
                <tbody className="divide-y divide-line [&_td]:px-3 [&_td]:py-2">
                  {[...compared, ...others].map((r) => (
                    <tr key={`${r.asin}|${r.period_start}`}>
                      <td className="max-w-[18rem]"><div className="truncate text-ink" title={r.title ?? r.asin}>{r.title ?? r.asin}</div>
                        <div className="font-mono text-[11px] text-ink-3">{r.asin}{r.market ? ` · ${mn(r.market)}` : ""}</div></td>
                      <td className="whitespace-nowrap text-xs text-ink-3">{r.period_start} – {r.period_end}</td>
                      <td className="text-right font-mono">{num(r.actual_units_month, 0)}</td>
                      <td className="text-right font-mono">{r.estimate != null ? <>{num(r.estimate)}{r.estimate_interval && <span className="text-ink-3"> ({num(r.estimate_interval[0])}–{r.estimate_interval[1] != null ? num(r.estimate_interval[1]) : "∞"})</span>}</> : "—"}</td>
                      <td className="text-right font-mono">{r.ratio_actual_to_estimate != null ? `${r.ratio_actual_to_estimate.toFixed(2)}×` : "—"}</td>
                      <td className="text-xs">
                        {r.status === "compared" ? (r.within_interval != null
                          ? <Badge color={r.within_interval ? "var(--good)" : "var(--warn)"}>{r.within_interval ? t("own.inside") : t("own.outside")}</Badge>
                          : <span className="text-ink-3">{t("own.st.compared")} · {r.observed_at}</span>)
                          : <span className="text-ink-3">{t(`own.st.${r.status}`)}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title={t("own.reports")} />
          {!sales.data ? <Skeleton className="m-4 h-16 rounded-xl" /> : sales.data.rows.length === 0 ? <Empty title={t("own.none")} /> : (
            <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={t("own.reports")}>
              <table className="w-full text-sm">
                <thead className="text-left text-[10px] uppercase tracking-wide text-ink-3 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-2">
                  <tr><th scope="col">{t("own.col.asin")}</th><th scope="col">{t("own.col.period")}</th><th scope="col" className="text-right">{t("own.col.units")}</th>
                    <th scope="col" className="text-right">{t("own.col.actual")}</th><th scope="col" className="text-right">{t("own.col.revenue")}</th>
                    <th scope="col" className="text-right">{t("own.col.sessions")}</th><th scope="col" className="text-right">{t("own.col.conversion")}</th></tr>
                </thead>
                <tbody className="divide-y divide-line [&_td]:px-3 [&_td]:py-2">
                  {sales.data.rows.map((r) => (
                    <tr key={`${r.asin}|${r.period_start}|${r.period_end}`}>
                      <td className="max-w-[18rem]"><div className="truncate text-ink" title={r.title ?? r.asin}>{r.title ?? r.asin}</div><div className="font-mono text-[11px] text-ink-3">{r.asin}</div></td>
                      <td className="whitespace-nowrap text-xs text-ink-3">{r.period_start} – {r.period_end} ({r.days}d)</td>
                      <td className="text-right font-mono">{num(r.units)}</td>
                      <td className="text-right font-mono">{num(r.units_month, 0)}</td>
                      <td className="text-right font-mono">{money(r.revenue, 0)}</td>
                      <td className="text-right font-mono">{num(r.sessions)}</td>
                      <td className="text-right font-mono">{r.conversion != null ? pct(r.conversion, 1) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
