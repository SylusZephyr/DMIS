"use client";
import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Columns2, PackageSearch, Rocket } from "lucide-react";
import { Badge, Card, CardHeader, Empty, Select } from "@/components/ui/primitives";
import { ModelFlag } from "@/components/v2/market-size";
import { ErrorNote, PageHeader } from "@/components/page";
import { ExportButtons } from "@/components/export-button";
import { GlossaryBar } from "@/components/glossary";
import { useMarket } from "@/components/market-switcher";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { SearchDemand } from "@/components/v2/search-demand";
import { useI18n } from "@/lib/i18n";
import { WhyDrawer } from "@/components/why-drawer";
import { basisZh } from "@/lib/basis-zh";
import { seq } from "@/lib/viz";
import { useMarketName } from "@/lib/market-name";

type Q = { mean: number; median: number; p10: number; p90: number };
type Row = {
  market: string; segment_id: string; segment_label: string; features: string[]; price_range: [number, number] | null; price: number | null;
  basis: string; concept_source: "gaps" | "median"; opportunity_index: number | null; opportunity_level: string | null;
  opportunity_coverage: number | null; evidence_grade: string | null; segment_revenue: number | null; hhi: number | null;
  entrant_success_rate: number | null; listings: number | null;
  engine: { score: number | null; raw_score: number | null; coverage: number | null; status: string | null; confidence: string | null;
    rank: number | null; patterns: { code?: string; name?: string }[]; risks: { code: string; severity?: string }[]; reasons: string[] } | null;
  leader?: { brand: string; share: number; share_lo: number; share_hi: number; p_top: number };
  momentum?: "accelerating" | "slowing" | "no_significant_change";
  growth: { per_month: number; lo: number; hi: number; direction: string } | { status: string; periods: number; min_periods: number };
  launch?: { units: Q; revenue: Q & { target: number; p_target: number }; profit: { median: number; p_positive: number } | null;
    risks: { code: string; severity: string }[]; entrants_actual: { n: number; median?: number }; assumed_rating: number | null;
    rating_basis: string } | { error: string };
};
type SortKey = "engine" | "opportunity" | "units" | "revenue" | "p_target" | "growth" | "leader" | "entry" | "risks";

const GRADE: Record<string, string> = { A: "var(--good)", B: "#3987e5", C: "var(--warn)", D: "var(--ink-3)" };
const SEV: Record<string, string> = { high: "var(--bad)", medium: "var(--warn)", low: "var(--ink-3)" };
const DIR: Record<string, string> = { growing: "var(--good)", declining: "var(--bad)", no_significant_trend: "var(--ink-3)",
  accelerating: "var(--good)", slowing: "var(--bad)", no_significant_change: "var(--ink-3)" };
const sim = (r: Row) => (r.launch && "units" in r.launch ? r.launch : null);
const grow = (r: Row) => ("per_month" in r.growth ? r.growth : null);
const signed = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;

function sortVal(r: Row, k: SortKey): number {
  const s = sim(r);
  switch (k) {
    case "engine": return r.engine?.score ?? (r.engine ? -1 : -2);      // unscored (insufficient evidence) after scored
    case "opportunity": return r.opportunity_index ?? -1;
    case "units": return s?.units.median ?? -1;
    case "revenue": return s?.revenue.median ?? -1;
    case "p_target": return s?.revenue.p_target ?? -1;
    case "growth": return grow(r)?.per_month ?? -99;
    case "leader": return r.leader ? -r.leader.share : -2;              // weaker leader first
    case "entry": return r.entrant_success_rate ?? -1;
    case "risks": return -(s?.risks.filter((x) => x.severity === "high").length ?? 99) * 10 - (s?.risks.length ?? 99);
  }
}

function Th({ k, children, className = "", sort, asc, onSort }: { k: SortKey; children: React.ReactNode; className?: string;
  sort: SortKey; asc: boolean; onSort: (k: SortKey) => void }) {
  return (
    <th scope="col" aria-sort={sort === k ? (asc ? "ascending" : "descending") : "none"} className={`select-none px-2 py-2 font-medium ${className}`}>
      <button type="button" onClick={() => onSort(k)} className="inline-flex items-center gap-1 uppercase hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
        {children}{sort === k && (asc ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</button>
    </th>
  );
}

export default function Opportunities() {
  const mn = useMarketName();
  const { t, lang } = useI18n();
  const [picked, setPicked] = useState<string | null>(null);
  const [globalMarket, setGlobalMarket] = useMarket();
  const [sort, setSort] = useState<SortKey>("engine");
  const [asc, setAsc] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [why, setWhy] = useState<{ market: string; id: string } | null>(null);
  const d = useApi<{ items: Row[]; markets: number }>("/opportunities-v3");
  const markets = useMemo(() => [...new Set((d.data?.items ?? []).map((r) => r.market))], [d.data]);
  // default filter: the market chosen in the global switcher (or ?market=), if it is on the board
  const wanted = picked ?? globalMarket;
  const market = markets.includes(wanted) ? wanted : "";
  const setMarket = (v: string) => { setPicked(v); if (v) setGlobalMarket(v); };
  const rows = useMemo(() => {
    const r = (d.data?.items ?? []).filter((x) => !market || x.market === market);
    return [...r].sort((a, b) => (asc ? 1 : -1) * (sortVal(a, sort) - sortVal(b, sort)));
  }, [d.data, market, sort, asc]);
  const target = (d.data?.items ?? []).map((r) => sim(r)?.revenue.target).find((v) => v != null) ?? null;
  const onSort = (k: SortKey) => { if (sort === k) setAsc(!asc); else { setSort(k); setAsc(false); } };
  const sp = { sort, asc, onSort };
  // facts that are the same on every row are said once above the table instead of on each row
  const anyGrowth = rows.some((r) => grow(r));
  const growthNote = !anyGrowth && rows[0] && !grow(rows[0]) && "periods" in rows[0].growth
    ? t("board.growthNone", { n: rows[0].growth.periods ?? 0, m: rows[0].growth.min_periods ?? "—" }) : null;
  const covs = new Set(rows.map((r) => r.engine?.coverage ?? null));
  const sameCov = covs.size === 1 ? [...covs][0] : undefined;
  const grades = new Set(rows.map((r) => r.evidence_grade));
  const sameGrade = grades.size === 1 ? [...grades][0] : undefined;
  const cols = anyGrowth ? 10 : 9;

  return (
    <div className="pb-8">
      <PageHeader title={t("board.title")}
        subtitle={<>{t("board.subtitle")}<GlossaryBar terms={["opportunityIndex", "interval95", "evidenceGrade", "pTop", "hhi", "entrant", "fdr"]} /></>}
        right={<div className="flex flex-col items-end gap-2">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Select aria-label={t("a11y.market")} value={market} onChange={(e) => setMarket(e.target.value)} className="w-56">
              <option value="">{t("board.allMarkets", { n: markets.length })}</option>
              {markets.map((m) => <option key={m} value={m}>{mn(m)}</option>)}
            </Select>
            <Select aria-label={t("board.sortBy")} value={sort} onChange={(e) => { setSort(e.target.value as SortKey); setAsc(false); }} className="w-44">
              {(["engine", "opportunity", "units", "revenue", "p_target", "growth", "leader", "entry", "risks"] as const).map((k) =>
                <option key={k} value={k}>↕ {t(`board.sort.${k}`)}</option>)}
            </Select>
            <Link href="/compare?mode=concepts" className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-sm text-ink-2 hover:border-accent hover:text-accent">
              <Columns2 className="h-4 w-4" />{t("nav.compare")}</Link>
          </div>
          <ExportButtons path={`/export/opportunities${market ? `?market=${encodeURIComponent(market)}` : ""}`} label={t("exp.board")} />
        </div>} />
      <ErrorNote error={d.error} />
      <Card className="mx-6">
        <CardHeader title={t("board.tableTitle", { n: rows.length })} subtitle={t("board.tableSub")} />
        {!d.data ? <div className="space-y-2 p-4">{[0, 1, 2, 3].map((i) => <div key={i} className="h-10 animate-pulse rounded bg-panel-2" />)}</div>
          : rows.length === 0 ? <Empty title={t("board.empty")}>{t("board.emptySub")}</Empty> : (
          <div className="overflow-x-auto" tabIndex={0}>
            {(growthNote || sameCov != null || sameGrade) && (
              <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-line px-4 py-2 text-[11px] text-ink-3">
                {sameCov != null && <span>{t("board.engineSub", { c: pct(sameCov, 0) })} ({t("board.allRows")})</span>}
                {sameGrade && <span>{t("common.evidenceGrade")} {sameGrade} ({t("board.allRows")})</span>}
                {growthNote && <span>{t("board.growth")}: {growthNote} — {t("board.growthLater")}</span>}
              </div>
            )}
            <table className="w-full min-w-[980px] text-xs">
              <thead className="border-b border-line text-left text-[10px] uppercase tracking-wide text-ink-3">
                <tr>
                  <th scope="col" className="w-8 px-2 py-2">#</th>
                  <th scope="col" className="px-2 py-2 font-medium">{t("board.concept")}</th>
                  <Th {...sp} k="engine" className="text-right">{t("board.engine")}</Th>
                  <Th {...sp} k="units" className="text-right">{t("board.units")}</Th>
                  <Th {...sp} k="revenue" className="text-right">{t("board.revenue")}</Th>
                  <Th {...sp} k="p_target">{t("board.pTarget", { v: moneyShort(target) })}</Th>
                  {anyGrowth && <Th {...sp} k="growth">{t("board.growth")}</Th>}
                  <Th {...sp} k="leader">{t("board.leader")}</Th>
                  <Th {...sp} k="entry" className="text-right">{t("board.entry")}</Th>
                  <Th {...sp} k="risks">{t("board.risks")}</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((r, i) => {
                  const key = `${r.market}|${r.segment_id}`;
                  const s = sim(r);
                  const g = grow(r);
                  const isOpen = open === key;
                  return (
                    <Fragment key={key}>
                      <tr className="cursor-pointer align-top hover:bg-panel-2" onClick={() => setOpen(isOpen ? null : key)}>
                        <td className="px-2 py-2.5 font-mono text-ink-3">
                          <button type="button" aria-expanded={isOpen} onClick={(e) => { e.stopPropagation(); setOpen(isOpen ? null : key); }}
                            className="inline-flex items-center gap-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
                            {isOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}{i + 1}</button></td>
                        <td className="max-w-[340px] px-2 py-2.5">
                          <div className="truncate text-sm font-medium" title={r.segment_label}>{truncate(r.segment_label, 60)}</div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-1">
                            <span className="text-ink-3">{mn(r.market)}</span>
                            {r.evidence_grade && sameGrade === undefined && <Badge color={GRADE[r.evidence_grade]}>{r.evidence_grade}</Badge>}
                            {r.features.map((f) => <Badge key={f} color="var(--accent)">{f}</Badge>)}
                            <span className="font-mono text-ink-2">@ {money(r.price, 2)}</span>
                          </div>
                        </td>
                        <td className="px-2 py-2.5 text-right">
                          {!r.engine ? <span className="text-[10px] text-ink-3">{t("board.engineNone")}</span>
                            : r.engine.score == null ? <span className="text-[10px] text-warn">{t("board.insufficientShort")}</span>
                            : <div className="font-mono text-base" style={{ color: seq(r.engine.score / 100) }}>{r.engine.score.toFixed(0)}</div>}
                          {r.engine?.coverage != null && sameCov === undefined && <div className="text-[10px] text-ink-3">{t("board.engineSub", { c: pct(r.engine.coverage, 0) })}</div>}
                          {r.opportunity_index != null && <div className="text-[10px] text-ink-3" title={t("market.indexTip")}>{t("market.indexShort")} {r.opportunity_index.toFixed(0)}</div>}
                        </td>
                        <td className="px-2 py-2.5 text-right font-mono">{s ? <>{num(s.units.median, 0)}<div className="text-[10px] text-ink-3">{num(s.units.p10, 0)}–{num(s.units.p90, 0)}</div></> : "—"}</td>
                        <td className="px-2 py-2.5 text-right font-mono">{s ? <>{moneyShort(s.revenue.median)}<div className="text-[10px] text-ink-3">{moneyShort(s.revenue.p10)}–{moneyShort(s.revenue.p90)}</div><ModelFlag market={r.market} className="font-sans text-[10px]" /></> : "—"}</td>
                        <td className="px-2 py-2.5">{s ? <div className="w-24">
                          <div className="flex justify-between font-mono"><span>{pct(s.revenue.p_target, 0)}</span></div>
                          <div className="mt-1 h-1.5 rounded bg-line"><div className="h-full rounded bg-[#3987e5]" style={{ width: `${s.revenue.p_target * 100}%` }} /></div>
                          {s.profit && <div className="mt-0.5 text-[10px] text-ink-3">P({t("board.profit")}&gt;0) {pct(s.profit.p_positive, 0)}</div>}
                        </div> : "—"}</td>
                        {anyGrowth && (
                        <td className="px-2 py-2.5">{g ? <><div className="font-mono" style={{ color: DIR[g.direction] }}>{signed(g.per_month)}</div>
                          <div className="text-[10px] text-ink-3">{signed(g.lo)} – {signed(g.hi)}</div></>
                          : <span className="text-[10px] text-ink-3">{t("board.growthNone", { n: "periods" in r.growth ? r.growth.periods ?? 0 : 0, m: "min_periods" in r.growth ? r.growth.min_periods : "—" })}</span>}
                          {r.momentum && r.momentum !== "no_significant_change" && <div className="text-[10px]" style={{ color: DIR[r.momentum] }}>{t(`seg.dir.${r.momentum}`)}</div>}
                        </td>
                        )}
                        <td className="max-w-[160px] px-2 py-2.5">{r.leader ? <>
                          <div className="truncate">{r.leader.brand}</div>
                          <div className="text-[10px] text-ink-3">{pct(r.leader.share, 0)} ({pct(r.leader.share_lo, 0)}–{pct(r.leader.share_hi, 0)}) · P(#1) {pct(r.leader.p_top, 0)}</div></> : "—"}</td>
                        <td className="px-2 py-2.5 text-right font-mono">{r.entrant_success_rate == null ? "—" : pct(r.entrant_success_rate, 0)}</td>
                        <td className="px-2 py-2.5">{s ? <div className="flex flex-wrap gap-1">
                          {s.risks.length === 0 ? <span className="text-good">{t("board.noRisk")}</span>
                            : [...s.risks].sort((a, b) => (a.severity === "high" ? 0 : 1) - (b.severity === "high" ? 0 : 1)).slice(0, 2).map((x) =>
                              <span key={x.code} className="inline-flex max-w-[150px] items-center gap-1 truncate rounded border border-line px-1 text-[10px] text-ink-2" title={t(`launch.risk.${x.code}.0`)}>
                                <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: SEV[x.severity] }} /><span className="truncate">{t(`launch.risk.${x.code}.0`)}</span></span>)}
                          {s.risks.length > 2 && <span className="text-[10px] text-ink-3">+{s.risks.length - 2}</span>}
                        </div> : "—"}</td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-panel-2/40">
                          <td />
                          <td colSpan={cols} className="px-2 pb-4 pt-1">
                            {r.engine && r.engine.reasons.length > 0 && (
                              <div className="mb-3 space-y-1">
                                <div className="flex items-center gap-2 text-[11px] uppercase text-ink-3">{t("kn.reasons")}
                                  <button type="button" onClick={() => setWhy({ market: r.market, id: r.segment_id })}
                                    className="normal-case text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">{t("kn.why")}</button></div>
                                <ol className="list-decimal space-y-0.5 pl-5 text-ink-2">{r.engine.reasons.map((x, j) => <li key={j}>{x}</li>)}</ol>
                                {r.engine.risks.length > 0 && <div className="flex flex-wrap gap-1 pt-1">{r.engine.risks.map((x) =>
                                  <Badge key={x.code} color={SEV[x.severity ?? "low"] ?? "var(--ink-3)"}>{x.code}</Badge>)}</div>}
                              </div>
                            )}
                            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                              <div className="space-y-1">
                                <div className="text-[11px] uppercase text-ink-3">{t("board.why")}</div>
                                <div className="text-ink-2">{r.concept_source === "gaps" ? t("board.fromGaps") : t("board.fromMedian")}</div>
                                <div className="text-[11px] text-ink-3">{lang === "zh" ? basisZh(r.basis) : r.basis}</div>
                                {r.price_range && <div>{t("market.priceRange")}: <span className="font-mono">{money(r.price_range[0], 2)}–{money(r.price_range[1], 2)}</span></div>}
                                <div>{t("board.segRevenue")}: <span className="font-mono">{moneyShort(r.segment_revenue)}</span> · {num(r.listings)} {t("common.listings")}</div>
                                {r.opportunity_index != null && <div>{t("board.index")}: <span className="font-mono">{r.opportunity_index.toFixed(0)}</span>{r.opportunity_level ? ` · ${t(`market.level.${r.opportunity_level}`)}` : ""}</div>}
                                <div>HHI <span className="font-mono">{num(r.hhi)}</span>{r.opportunity_coverage != null && r.opportunity_coverage < 1 && <> · {pct(r.opportunity_coverage)} {t("common.coverage")}</>}</div>
                                <SearchDemand market={r.market} segmentId={r.segment_id} />
                              </div>
                              <div className="space-y-1">
                                <div className="text-[11px] uppercase text-ink-3">{t("launch.risks")}</div>
                                {!s ? <div className="text-ink-3">{r.launch && "error" in r.launch ? r.launch.error : t("board.noSim")}</div>
                                  : s.risks.length === 0 ? <div className="text-good">{t("launch.noRisk")}</div>
                                  : s.risks.map((x) => <div key={x.code} className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: SEV[x.severity] }} />{t(`launch.risk.${x.code}.0`)}</div>)}
                                {s && s.assumed_rating != null && <div className="text-[11px] text-ink-3">{t(s.rating_basis?.startsWith("segment median") ? "board.assumedRatingMedian" : "board.assumedRating", { r: s.assumed_rating.toFixed(2) })}</div>}
                                {s && s.entrants_actual?.n > 0 && <div className="text-[11px] text-ink-3">{t("board.entrantsActual", { n: s.entrants_actual.n, m: num(s.entrants_actual.median, 0) })}</div>}
                              </div>
                              <div className="flex flex-col items-start gap-2">
                                <Link href={`/launch?${new URLSearchParams({ title: [r.segment_label, ...r.features].join(" "), price: String(r.price ?? ""), market: r.market, segment_id: r.segment_id })}`}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-accent/50 px-3 py-1.5 text-accent hover:bg-accent/10"><Rocket className="h-3.5 w-3.5" />{t("board.simulate")}</Link>
                                <Link href={`/sourcing?${new URLSearchParams({ market: r.market, id: r.segment_id })}`}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-ink-2 hover:border-accent hover:text-accent"><PackageSearch className="h-3.5 w-3.5" />{t("board.findSuppliers")}</Link>
                                <Link href={`/markets/${encodeURIComponent(r.market)}`} className="text-accent hover:underline">{t("board.openMarket")} →</Link>
                                <Link href={`/competitors?market=${encodeURIComponent(r.market)}`} className="text-accent hover:underline">{t("board.openCompetitors")} →</Link>
                                <Link href={`/compare?${new URLSearchParams({ mode: "concepts", a: key })}`} className="inline-flex items-center gap-1 text-accent hover:underline">
                                  <Columns2 className="h-3.5 w-3.5" />{t("cmp.fromBoard")} →</Link>
                                <ExportButtons label={t("exp.memo")} formats={["md", "html"]}
                                  path={`/export/memo?${new URLSearchParams({ market: r.market, segment_id: r.segment_id, lang })}`} />
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="px-4 py-3 text-[11px] text-ink-3">{t("board.note")}</p>
      </Card>
      {why && <WhyDrawer market={why.market} entityType="segment" entityId={why.id} onClose={() => setWhy(null)} />}
    </div>
  );
}
