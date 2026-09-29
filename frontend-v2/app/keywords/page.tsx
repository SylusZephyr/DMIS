"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ArrowDown, ArrowUp, Upload } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input, Meter, Select, Skeleton } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import { post } from "@/lib/api";
import { money, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type SegRow = { segment_id: string; segment_label: string; keywords: number; searches: number; purchases: number | null;
  purchase_rate: number | null; ppc_bid_median: number | null; gaps: number; top_keywords: string[] };
type Summary = { market: string; keywords: number; assigned?: number; unassigned?: number; gaps?: number; searches?: number;
  coverage?: number | null; columns?: string[]; source_name?: string | null; imported_at?: string | null; segments: SegRow[] };
type Row = { keyword: string; translation?: string | null; searches: number | null; purchases?: number | null; purchase_rate?: number | null;
  title_density?: number | null; products?: number | null; ppc_bid?: number | null; score: number | null; coverage: number;
  components: Record<string, number>; is_gap: boolean; segment_id: string | null; segment_label: string | null;
  matched_listings: number; assign_share: number | null };
type Page = { total: number; rows: Row[] };
type Imported = Summary & { mapping: Record<string, string>; header_scores: Record<string, number>; notes: string[]; unmapped: string[] };

const COLS = ["searches", "purchases", "purchase_rate", "title_density", "products", "ppc_bid"] as const;

function View() {
  const { t } = useI18n();
  const mn = useMarketName();
  const sp = useSearchParams();
  const [ctx] = useMarket();
  const market = sp.get("market") || ctx;
  const enc = encodeURIComponent(market);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [imported, setImported] = useState<Imported | null>(null);
  const [ver, setVer] = useState(0);
  const [seg, setSeg] = useState(sp.get("segment") ?? "");
  const [gaps, setGaps] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<string>("score");
  const [desc, setDesc] = useState(true);
  const [limit, setLimit] = useState(50);
  const summ = useApi<Summary>(market ? `/markets/${enc}/keywords/summary` : null, [market, ver]);
  const params = new URLSearchParams({ sort, desc: String(desc), limit: String(limit), ...(seg ? { segment: seg } : {}),
    ...(gaps ? { gaps: "true" } : {}), ...(q.trim() ? { q: q.trim() } : {}) });
  const page = useApi<Page>(market ? `/markets/${enc}/keywords?${params}` : null, [market, params.toString(), ver]);
  const s = summ.data;
  const has = (c: string) => (s?.columns ?? []).includes(c);
  const maxSeg = Math.max(1, ...(s?.segments ?? []).map((x) => x.searches));

  const upload = async () => {
    if (!file || !market) return;
    setBusy(true); setErr(null);
    const fd = new FormData();
    fd.append("file", file);
    try { setImported(await post<Imported>(`/markets/${enc}/keywords`, fd)); setFile(null); setVer((v) => v + 1); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const sortBy = (c: string) => { if (sort === c) setDesc(!desc); else { setSort(c); setDesc(c !== "title_density" && c !== "products" && c !== "ppc_bid"); } };
  const fmt = (c: string, v: number | null | undefined) =>
    v == null ? "—" : c === "purchase_rate" ? pct(v, 1) : c === "ppc_bid" ? money(v, 2) : num(v);

  return (
    <div className="pb-8">
      <PageHeader title={t("kw.title")} subtitle={t("kw.subtitle")} />
      <ErrorNote error={summ.error ?? page.error} />
      {!market ? <div className="px-6"><Card><Empty title={t("kw.noMarket")} /></Card></div> : (
      <div className="space-y-5 px-4 md:px-6">
        <Card>
          <CardHeader title={t("kw.upload")} subtitle={`${mn(market)} · ${t("kw.uploadSub")}`} />
          <div className="flex flex-wrap items-center gap-3 p-4 text-sm">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-line px-3 py-1.5 hover:border-accent">
              <Upload className="h-4 w-4" aria-hidden />{file ? file.name : t("kw.choose")}
              <input type="file" accept=".csv,.xlsx,.xls,.tsv" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <Button onClick={upload} disabled={!file || busy} aria-busy={busy}>{t("kw.import")}</Button>
            {s?.source_name && <span className="text-xs text-ink-3">{s.source_name}</span>}
          </div>
          {err && <p role="alert" className="px-4 pb-3 text-sm text-bad">{err}</p>}
          {imported && (
            <div role="status" className="space-y-1 border-t border-line px-4 py-3 text-xs text-ink-2">
              <div><span className="text-ink-3">{t("kw.mapping")}:</span> {Object.entries(imported.mapping).map(([f, h]) =>
                <span key={f} className="mr-2 inline-block"><b className="text-ink">{f}</b> ← {h}</span>)}</div>
              {imported.notes.length > 0 && <div><span className="text-ink-3">{t("kw.notes")}:</span> {imported.notes.join(" · ")}</div>}
            </div>
          )}
        </Card>

        {summ.loading && !s ? <Skeleton className="h-24 rounded-2xl" /> : !s?.keywords ? <Card><Empty title={t("kw.none")}>{t("kw.noneSub")}</Empty></Card> : (<>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {([["keywords", num(s.keywords)], ["searches", num(s.searches)], ["gaps", num(s.gaps)], ["unassigned", num(s.unassigned)]] as const).map(([k, v]) => (
              <Card key={k} className="p-4">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{t(`kw.kpis.${k}`)}</div>
                <div className="mt-1 font-mono text-2xl text-ink">{v}</div>
              </Card>
            ))}
          </div>

          <Card>
            <CardHeader title={t("kw.bySeg")} subtitle={t("kw.bySegSub")} />
            <ul className="divide-y divide-line">
              {s.segments.map((g) => (
                <li key={g.segment_id} className="grid gap-2 px-4 py-2.5 text-sm md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)_auto] md:items-center">
                  <button type="button" className="truncate text-left text-ink hover:text-accent" title={g.segment_label} onClick={() => setSeg(g.segment_id)}>{g.segment_label}</button>
                  <div className="flex items-center gap-2" title={`${num(g.searches)}`}>
                    <div className="h-2 min-w-0 flex-1 rounded-full bg-panel-2"><div className="h-2 rounded-full bg-accent" style={{ width: `${(100 * g.searches) / maxSeg}%` }} /></div>
                    <span className="w-20 text-right font-mono text-xs text-ink">{num(g.searches)}</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5 text-[11px] text-ink-3">
                    {g.gaps > 0 && <Badge color="var(--good)">{t("kw.gapsN", { n: g.gaps })}</Badge>}
                    {g.purchase_rate != null && <span>{t("kw.rate", { r: pct(g.purchase_rate, 1) })}</span>}
                    {g.ppc_bid_median != null && <span>{t("kw.bid", { b: money(g.ppc_bid_median, 2) })}</span>}
                  </div>
                </li>
              ))}
            </ul>
          </Card>

          <Card>
            <CardHeader title={t("kw.table")} subtitle={page.data ? t("kw.shown", { n: page.data.rows.length, t: page.data.total }) : undefined} />
            <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 text-sm">
              <Input className="w-56" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("kw.search")} aria-label={t("kw.search")} />
              <Select aria-label={t("kw.filterSeg")} value={seg} onChange={(e) => setSeg(e.target.value)}>
                <option value="">{t("kw.allSegs")}</option>
                {s.segments.map((g) => <option key={g.segment_id} value={g.segment_id}>{g.segment_label}</option>)}
                <option value="unassigned">{t("kw.unassigned")}</option>
              </Select>
              <label className="inline-flex items-center gap-2 text-xs"><input type="checkbox" checked={gaps} onChange={(e) => setGaps(e.target.checked)} />{t("kw.gapsOnly")}</label>
            </div>
            <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={t("kw.table")}>
              <table className="w-full text-sm">
                <thead className="text-left text-[10px] uppercase tracking-wide text-ink-3 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-2">
                  <tr>
                    <th scope="col">{t("kw.col.keyword")}</th>
                    {(["score", ...COLS.filter(has)] as string[]).map((c) => (
                      <th key={c} scope="col" className="text-right" aria-sort={sort === c ? (desc ? "descending" : "ascending") : "none"}>
                        <button type="button" className="inline-flex items-center gap-1 uppercase hover:text-ink" onClick={() => sortBy(c)}>
                          {t(`kw.col.${c}`)}{sort === c && (desc ? <ArrowDown className="h-3 w-3" aria-hidden /> : <ArrowUp className="h-3 w-3" aria-hidden />)}
                        </button>
                      </th>
                    ))}
                    <th scope="col">{t("kw.col.segment")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line [&_td]:px-3 [&_td]:py-2">
                  {(page.data?.rows ?? []).map((r) => (
                    <tr key={r.keyword}>
                      <td className="min-w-[12rem] max-w-[22rem]">
                        <div className="flex flex-wrap items-center gap-1.5"><span className="text-ink">{r.keyword}</span>
                          {r.is_gap && <Badge color="var(--good)">{t("kw.gap")}</Badge>}</div>
                        {r.translation && r.translation !== "None" && <div className="text-[11px] text-ink-3">{r.translation}</div>}
                      </td>
                      <td className="w-28 text-right" title={t("kw.scoreTip", { c: pct(r.coverage, 0) })}>
                        <div className="font-mono">{r.score != null ? r.score.toFixed(0) : "—"}</div><Meter value={r.score} max={100} />
                      </td>
                      {COLS.filter(has).map((c) => <td key={c} className="text-right font-mono">{fmt(c, r[c])}</td>)}
                      <td className="max-w-[16rem] text-xs">
                        {r.segment_label ? <>
                          <div className="truncate text-ink-2" title={r.segment_label}>{r.segment_label}</div>
                          <div className="text-[11px] text-ink-3">{t("kw.matched", { n: r.matched_listings, s: pct(r.assign_share, 0) })}</div>
                        </> : <span className="text-ink-3">{t("kw.unassigned")}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {page.data && page.data.total > page.data.rows.length && (
              <div className="border-t border-line p-3 text-center"><Button variant="ghost" size="sm" onClick={() => setLimit(limit + 100)}>{t("kw.more")}</Button></div>
            )}
          </Card>
        </>)}
      </div>)}
    </div>
  );
}

export default function KeywordsPage() {
  return <Suspense><View /></Suspense>;
}
