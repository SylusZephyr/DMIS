"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import type { EChartsOption } from "echarts";
import { axis, EChart } from "@/components/charts/echart";
import { Explain } from "@/components/metric";
import { Badge, Card, CardHeader, Empty, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { ExportButtons } from "@/components/export-button";
import { GlossaryBar } from "@/components/glossary";
import { useMarket } from "@/components/market-switcher";
import type { MarketRow } from "@/lib/api";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";
import { ModelFlag } from "@/components/market-size";

type Signal = { code: string; kind: "strength" | "weakness"; value: number; price_index?: number; opening?: string };
type Brand = {
  brand: string; listings: number; products: number; segments: number; top_segment: string | null; units_est: number;
  revenue_est: number; revenue_lo: number; revenue_hi: number; share_est: number; share_lo: number; share_hi: number;
  rank: number; rank_lo: number; rank_hi: number; p_top: number; median_price: number | null; price_index: number | null;
  rating_bayes: number | null; rating_gap: number | null; reviews: number | null; entrants: number | null;
  entrant_revenue_share: number | null; median_age_days: number | null; position: string; signals: Signal[];
};
type Momentum = { scope_id: string; segment_label: string; recent: number; previous: number; rate_ratio: number;
  p_value: number | null; q_value: number | null; direction: string; significant: boolean; entrant_revenue_share: number | null };
type Cohort = { quarter: string; launched: number; revenue_est: number; revenue_share: number; revenue_per_listing: number };
type Change = { brand: string; share_from: number; share_to: number; change: number; z: number; p_value: number; q_value: number; significant: boolean };
type Payload = {
  market: string; as_of: string | null; evidence_grade: string | null; brands: Brand[]; cohorts: Cohort[]; momentum: Momentum[];
  changes: Change[]; change_info: { status: string; from?: string; to?: string; periods?: number; tested?: number; significant?: number };
  info: { hhi: number; hhi_lo: number; hhi_hi: number; effective_competitors: number; equal_share_benchmark: number;
    rating_mean: number | null; rating_prior_strength: number; rating_weight_basis: string; brands: number } | null;
};

const POSITION_COLOR: Record<string, string> = { leader: "#3987e5", above_par: "#199e70", par: "#8ea0bb", below_par: "#5b6b82" };
const DIRECTION_COLOR: Record<string, string> = { accelerating: "var(--good)", slowing: "var(--bad)", no_significant_change: "var(--ink-3)" };

/** Share as a dot on its 95% interval, with the equal-share benchmark as a vertical rule. */
function ShareInterval({ b, max, bench }: { b: Brand; max: number; bench: number }) {
  const x = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <div className="relative h-4 w-full" title={`${pct(b.share_est, 1)} (${pct(b.share_lo, 1)}–${pct(b.share_hi, 1)})`}>
      <div className="absolute top-1/2 h-px w-full -translate-y-1/2 bg-line" />
      <div className="absolute top-0 h-4 w-px bg-warn/70" style={{ left: x(bench) }} />
      <div className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-accent/35" style={{ left: x(b.share_lo), width: `calc(${x(b.share_hi)} - ${x(b.share_lo)})` }} />
      <div className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-panel bg-accent" style={{ left: x(b.share_est) }} />
    </div>
  );
}

function View() {
  const mn = useMarketName();
  const { t } = useI18n();
  const router = useRouter();
  const markets = useApi<MarketRow[]>("/markets");
  const [globalMarket] = useMarket();
  const names = (markets.data ?? []).map((m) => m.name);
  const market = useSearchParams().get("market") ?? (names.includes(globalMarket) ? globalMarket : names[0] ?? "");
  const enc = encodeURIComponent(market);
  const d = useApi<Payload>(market ? `/markets/${enc}/competitors-v3?limit=60` : null, [market]);
  const p = d.data;
  const info = p?.info;
  const brands = p?.brands ?? [];
  const maxShare = Math.max(0.05, ...brands.slice(0, 25).map((b) => b.share_hi));
  const mkt = p?.momentum.find((m) => m.scope_id === "");
  const segMom = (p?.momentum ?? []).filter((m) => m.scope_id !== "").sort((a, b) => (b.recent + b.previous) - (a.recent + a.previous));

  const cohortChart = useMemo<EChartsOption>(() => {
    const c = p?.cohorts ?? [];
    return {
      grid: { left: 44, right: 16, top: 16, bottom: 28 }, tooltip: { trigger: "axis" as const },
      xAxis: { ...axis, type: "category" as const, data: c.map((x) => x.quarter) },
      yAxis: { ...axis, type: "value" as const, minInterval: 1 },
      series: [{ name: t("comp.launched"), type: "bar" as const, barWidth: 14,
        data: c.map((x) => ({ value: x.launched, itemStyle: { color: "#3987e5", borderRadius: [4, 4, 0, 0] } })) }],
    } as unknown as EChartsOption;
  }, [p, t]);
  const cohortRevenue = useMemo<EChartsOption>(() => {
    const c = p?.cohorts ?? [];
    return {
      grid: { left: 44, right: 16, top: 16, bottom: 28 },
      tooltip: { trigger: "axis" as const, valueFormatter: (v: unknown) => moneyShort(Number(v)) },
      xAxis: { ...axis, type: "category" as const, data: c.map((x) => x.quarter) },
      yAxis: { ...axis, type: "value" as const, axisLabel: { color: "#62789a", formatter: (v: number) => moneyShort(v) } },
      series: [{ name: t("comp.revenueToday"), type: "bar" as const, barWidth: 14,
        data: c.map((x) => ({ value: Math.round(x.revenue_est), itemStyle: { color: "#199e70", borderRadius: [4, 4, 0, 0] } })) }],
    } as unknown as EChartsOption;
  }, [p, t]);

  const signalText = (s: Signal) => t(`comp.sig.${s.code}`, { v: s.code === "entrant_driven" ? pct(s.value) : s.value.toFixed(2), p: s.price_index?.toFixed(2) ?? "" });

  return (
    <div className="pb-10">
      <PageHeader title={t("comp.title")}
        subtitle={<>{t("comp.subtitle")}<GlossaryBar terms={["interval95", "pTop", "hhi", "entrant", "fdr"]} /></>}
        right={<div className="flex flex-col items-end gap-2">
          <Select aria-label={t("a11y.market")} value={market} onChange={(e) => router.push(`/competitors?market=${encodeURIComponent(e.target.value)}`)} className="w-64">
            {(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}
          </Select>
          {market && <ExportButtons path={`/markets/${enc}/export/brands`} label={t("exp.brands")} />}
        </div>} />
      <ErrorNote error={markets.error ?? d.error} />

      {p && !info && <Card className="mx-6"><Empty title={t("comp.notProcessed")} /></Card>}
      {info && (
        <div className="space-y-4 px-6">
          <Card>
            <div className="grid grid-cols-2 divide-x divide-line md:grid-cols-5">
              <div className="p-4"><div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("metric.hhi.0")} <Explain metric="hhi" /></div>
                <div className="mt-1 font-mono text-xl">{num(info.hhi)}</div><div className="text-[11px] text-ink-3">{t("common.interval")} {num(info.hhi_lo)}–{num(info.hhi_hi)}</div></div>
              <div className="p-4"><div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("metric.effective_competitors.0")} <Explain metric="effective_competitors" /></div>
                <div className="mt-1 font-mono text-xl">{info.effective_competitors.toFixed(1)}</div><div className="text-[11px] text-ink-3">{t("comp.ofBrands", { n: info.brands })}</div></div>
              <div className="p-4"><div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("comp.benchmark")} <Explain metric="equal_share_benchmark" /></div>
                <div className="mt-1 font-mono text-xl">{pct(info.equal_share_benchmark, 1)}</div><div className="text-[11px] text-ink-3">{t("comp.benchmarkSub")}</div></div>
              <div className="p-4"><div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("comp.launchMomentum")} <Explain metric="launch_momentum" /></div>
                <div className="mt-1 text-base font-medium leading-7" style={{ color: mkt ? DIRECTION_COLOR[mkt.direction] : undefined }}>{mkt ? t(`comp.dir.${mkt.direction}`) : "—"}</div>
                <div className="text-[11px] text-ink-3">{mkt ? t("comp.recentVsPrev", { a: mkt.recent, b: mkt.previous, p: mkt.p_value == null ? "—" : mkt.p_value.toFixed(3) }) : t("comp.noLaunchDates")}</div></div>
              <div className="p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("common.evidenceGrade")}</div>
                <div className="mt-1 font-mono text-xl">{p?.evidence_grade ?? "—"}</div><div className="text-[11px] text-ink-3">{t("comp.asOf", { d: p?.as_of ?? "—" })}</div></div>
            </div>
          </Card>

          <Card>
            <CardHeader title={t("comp.brandsTitle")} subtitle={<>{t("comp.brandsSub", { basis: info.rating_weight_basis, m: info.rating_mean?.toFixed(2) ?? "—", c: num(info.rating_prior_strength) })}<ModelFlag market={p?.market} /></>} />
            <div className="flex flex-wrap gap-3 border-b border-line px-4 py-2 text-[11px] text-ink-3">
              {Object.entries(POSITION_COLOR).map(([k, c]) => <span key={k} className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: c }} />{t(`comp.pos.${k}`)}</span>)}
              <span className="flex items-center gap-1"><span className="h-3 w-px bg-warn" />{t("comp.benchmarkLine")}</span>
            </div>
            {brands.length === 0 ? <Empty title={t("common.notAvailable")} /> : (
              <div className="max-h-[680px] overflow-auto scrollbar-thin" tabIndex={0} role="region" aria-label={t("comp.title")}>
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10 bg-panel text-left text-[10px] uppercase text-ink-3 [&_th]:whitespace-nowrap [&_th]:pr-3">
                    <tr><th scope="col" className="px-4 py-2">{t("comp.rank")}</th><th scope="col">{t("comp.brand")}</th><th scope="col" className="w-56">{t("metric.revenue_share.0")} (95%)</th>
                      <th scope="col">{t("comp.revenue")}</th><th scope="col">P(#1)</th><th scope="col">{t("comp.priceIndex")}</th><th scope="col">{t("comp.rating")}</th><th scope="col">{t("metric.entrants.0")}</th><th scope="col" className="pr-4">{t("comp.signals")}</th></tr>
                  </thead>
                  <tbody className="divide-y divide-line [&_td]:pr-3">
                    {brands.map((b) => (
                      <tr key={b.brand} className="align-top">
                        <td className="px-4 py-2 font-mono text-xs">{b.rank}<div className="text-ink-3">{b.rank_lo === b.rank_hi ? "" : `${b.rank_lo}–${b.rank_hi}`}</div></td>
                        <td className="py-2 pr-2"><div className="font-medium">{b.brand}</div>
                          <Badge color={POSITION_COLOR[b.position]}>{t(`comp.pos.${b.position}`)}</Badge>
                          <div className="mt-1 text-[11px] text-ink-3">{t("comp.footprint", { l: b.listings, p: b.products, s: b.segments })}</div>
                          {b.top_segment && <div className="max-w-[220px] truncate text-[11px] text-ink-3">{truncate(b.top_segment, 40)}</div>}</td>
                        <td className="py-2 pr-3"><div className="font-mono text-xs">{pct(b.share_est, 1)} <span className="text-ink-3">{pct(b.share_lo, 1)}–{pct(b.share_hi, 1)}</span></div>
                          <ShareInterval b={b} max={maxShare} bench={info.equal_share_benchmark} /></td>
                        <td className="py-2 font-mono text-xs">{moneyShort(b.revenue_est)}<div className="text-ink-3">{moneyShort(b.revenue_lo)}–{moneyShort(b.revenue_hi)}</div></td>
                        <td className="py-2 font-mono text-xs">{b.p_top >= 0.005 ? pct(b.p_top) : "<1%"}</td>
                        <td className="py-2 font-mono text-xs">{b.price_index?.toFixed(2) ?? "—"}<div className="text-ink-3">{money(b.median_price, 2)}</div></td>
                        <td className="py-2 font-mono text-xs">{b.rating_bayes?.toFixed(2) ?? "—"}
                          {b.rating_gap != null && <div className={b.rating_gap < 0 ? "text-bad" : "text-good"}>{b.rating_gap > 0 ? "+" : ""}{b.rating_gap.toFixed(2)}</div>}</td>
                        <td className="py-2 font-mono text-xs">{b.entrants ?? "—"}{b.entrant_revenue_share != null && <div className="text-ink-3">{pct(b.entrant_revenue_share)} {t("comp.ofRevenue")}</div>}</td>
                        <td className="max-w-[300px] py-2 pr-4 text-xs">
                          {b.signals.length === 0 ? <span className="text-ink-3">{t("comp.noSignal")}</span> : b.signals.map((s, i) => (
                            <div key={i}><span className={s.kind === "weakness" ? "text-bad" : "text-good"}>{signalText(s)}</span>
                              {s.opening && <span className="text-ink-2"> → {t(`comp.open.${s.opening}`)}</span>}</div>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title={t("comp.cohortsTitle")} subtitle={<>{t("comp.cohortsSub")}<ModelFlag market={p?.market} /></>} />
              {!p?.cohorts.length ? <Empty title={t("comp.noLaunchDates")} /> : (
                <div className="grid grid-cols-1 gap-2 p-2 md:grid-cols-2">
                  <div><div className="px-2 text-[11px] text-ink-3">{t("comp.launched")}</div><EChart option={cohortChart} height={220} /></div>
                  <div><div className="px-2 text-[11px] text-ink-3">{t("comp.revenueToday")}</div><EChart option={cohortRevenue} height={220} /></div>
                </div>
              )}
            </Card>
            <Card>
              <CardHeader title={t("comp.momentumTitle")} subtitle={t("comp.momentumSub")} />
              {!segMom.length ? <Empty title={t("comp.noLaunchDates")} /> : (
                <div className="max-h-[300px] overflow-auto scrollbar-thin" tabIndex={0}>
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-panel text-left text-[10px] uppercase text-ink-3 [&_th]:whitespace-nowrap [&_th]:pr-3">
                      <tr><th scope="col" className="px-4 py-1.5">{t("comp.segment")}</th><th scope="col">{t("comp.last12")}</th><th scope="col">{t("comp.prev12")}</th><th scope="col">{t("comp.ratio")}</th><th scope="col">q</th><th scope="col" className="pr-4">{t("comp.direction")}</th></tr>
                    </thead>
                    <tbody className="divide-y divide-line [&_td]:pr-3">
                      {segMom.map((m) => (
                        <tr key={m.scope_id}>
                          <td className="max-w-[260px] truncate px-4 py-1.5">{m.segment_label}</td>
                          <td className="font-mono">{m.recent}</td><td className="font-mono">{m.previous}</td>
                          <td className="font-mono">{m.rate_ratio.toFixed(2)}</td>
                          <td className="font-mono">{m.q_value == null ? <span className="text-ink-3">{t("comp.tooFew")}</span> : m.q_value.toFixed(3)}</td>
                          <td className="pr-4" style={{ color: DIRECTION_COLOR[m.direction] }}>{t(`comp.dir.${m.direction}`)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          <Card>
            <CardHeader title={t("comp.changesTitle")}
              subtitle={p?.change_info.status === "ok" ? t("comp.changesSub", { a: p.change_info.from ?? "", b: p.change_info.to ?? "", n: p.change_info.significant ?? 0, m: p.change_info.tested ?? 0 }) : ""} />
            {p?.change_info.status !== "ok" ? <Empty title={t("comp.needsTwo")}>{t("comp.needsTwoSub")}</Empty> : (
              <div className="max-h-[360px] overflow-auto scrollbar-thin" tabIndex={0}>
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-panel text-left text-[10px] uppercase text-ink-3 [&_th]:whitespace-nowrap [&_th]:pr-3">
                    <tr><th scope="col" className="px-4 py-1.5">{t("comp.brand")}</th><th scope="col">{t("comp.from")}</th><th scope="col">{t("comp.to")}</th><th scope="col">{t("comp.change")}</th><th scope="col">z</th><th scope="col">q</th><th scope="col" className="pr-4" /></tr>
                  </thead>
                  <tbody className="divide-y divide-line [&_td]:pr-3">
                    {p.changes.slice(0, 60).map((c) => (
                      <tr key={c.brand} className={c.significant ? "bg-accent/5" : ""}>
                        <td className="px-4 py-1.5">{c.brand}</td><td className="font-mono">{pct(c.share_from, 1)}</td><td className="font-mono">{pct(c.share_to, 1)}</td>
                        <td className={`font-mono ${c.change > 0 ? "text-good" : c.change < 0 ? "text-bad" : ""}`}>{c.change > 0 ? "+" : ""}{(c.change * 100).toFixed(1)} {t("comp.pts")}</td>
                        <td className="font-mono">{c.z.toFixed(2)}</td><td className="font-mono">{c.q_value.toFixed(3)}</td>
                        <td className="pr-4">{c.significant ? <Badge color="var(--accent)">{t("comp.significant")}</Badge> : <span className="text-ink-3">{t("comp.withinNoise")}</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

export default function CompetitorsPage() {
  return <Suspense><View /></Suspense>;
}
