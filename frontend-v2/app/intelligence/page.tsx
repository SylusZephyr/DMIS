"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronRight, CircleHelp, Sparkles } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Meter, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import { KindBadge, WhyDrawer } from "@/components/why-drawer";
import type { MarketRow } from "@/lib/api";
import type { Schemas } from "@/lib/api-typed";
import { money, moneyShort, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { humanize, useMarketName } from "@/lib/market-name";
import { cn } from "@/lib/utils";
import { seq } from "@/lib/viz";
import { EChart, axis } from "@/components/charts/echart";
import { ExportButtons } from "@/components/export-button";
import type { EChartsOption } from "echarts";
import { ModelFlag } from "@/components/v2/market-size";

type Taxonomy = Schemas["TaxonomyResponse"];
type Capacity = Schemas["CapacityResponse"];
type CapRow = Schemas["CapacityRow"];
type Opps = Schemas["OpportunityResponse"];
type Opp = Schemas["OpportunityRow"];
type Sel = { scope: "category" | "segment" | "taxonomy"; id: string };

const DIMS = ["demand", "offline_strength", "growth", "customer_pain", "competition_gap", "pricing_margin",
  "supplier_availability", "entry_ease", "data_confidence"] as const;
const LEVEL_COLOR: Record<string, string> = { High: "var(--good)", Medium: "var(--warn)", Low: "var(--bad)", Unknown: "var(--ink-3)" };
const SEV_COLOR: Record<string, string> = { high: "var(--bad)", medium: "var(--warn)", low: "var(--ink-3)" };

/** Score cell for the heatmap: color intensity by score; unmeasured dimensions are hatched, not zero. */
function HeatCell({ v }: { v: number | null | undefined }) {
  if (v == null) {
    return <td className="px-1 py-1"><div className="h-7 rounded bg-[repeating-linear-gradient(45deg,var(--panel-2),var(--panel-2)_4px,transparent_4px,transparent_8px)]" /></td>;
  }
  return (
    <td className="px-1 py-1">
      <div className="flex h-7 items-center justify-center rounded font-mono text-[11px] tabular-nums text-ink"
        style={{ background: `color-mix(in srgb, var(--accent) ${Math.round(12 + v * 0.7)}%, transparent)` }}>{num(v)}</div>
    </td>
  );
}

function LeafCard({ market, cap, opp, onWhy }: { market: string; cap: CapRow; opp: Opp | undefined; onWhy: () => void }) {
  const mn = useMarketName();
  const { t } = useI18n();
  const stat = (label: string, value: React.ReactNode, sub?: React.ReactNode) => (
    <div className="min-w-0 rounded-lg border border-line p-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-3">{label}</div>
      <div className="mt-1 font-mono text-lg tabular-nums text-ink">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-ink-3">{sub}</div>}
    </div>
  );
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title={cap.scope === "category" ? mn(market) : cap.scope === "taxonomy" ? humanize(cap.label ?? "") : cap.label} subtitle={cap.scope === "taxonomy" ? cap.scope_id : cap.scope}
          right={<div className="flex flex-wrap items-center justify-end gap-2">
            {cap.scope !== "category" && <ExportButtons label={t("kn.requirements")} formats={["md"]}
              path={`/markets/${encodeURIComponent(market)}/requirements?${new URLSearchParams({ scope: cap.scope, id: cap.scope_id })}`} />}
            <Button variant="outline" size="sm" onClick={onWhy}><CircleHelp className="h-4 w-4" />{t("kn.why")}</Button></div>} />
        <div className="grid grid-cols-2 gap-3 p-4 pt-0 md:grid-cols-4">
          {stat(t("kn.products"), num(cap.products), `${num(cap.listings)} ${t("kn.listings").toLowerCase()}`)}
          {stat(t("kn.brands"), num(cap.brands), cap.sellers != null ? `${num(cap.sellers)} ${t("kn.sellers").toLowerCase()}` : undefined)}
          {stat(t("kn.revenue"), moneyShort(cap.revenue_est),
            <span className="flex items-center gap-1.5">{cap.revenue_lo != null ? `${moneyShort(cap.revenue_lo)}–${moneyShort(cap.revenue_hi)}` : ""}<KindBadge kind="modeled" /><ModelFlag market={market} className="inline" /></span>)}
          {stat(t("kn.units"), num(cap.units_est),
            <span className="flex items-center gap-1.5">{cap.units_lo != null ? `${num(cap.units_lo)}–${num(cap.units_hi)}` : ""}<KindBadge kind="modeled" /><ModelFlag market={market} className="inline" /></span>)}
          {stat(t("kn.sourceEstimate"), moneyShort(cap.revenue_source_estimate),
            <span className="flex items-center gap-1.5">{pct(cap.revenue_source_coverage)}<KindBadge kind="estimated" /></span>)}
          {stat(t("kn.offline"), cap.offline_adjusted_revenue != null ? moneyShort(cap.offline_adjusted_revenue) : "—", t("kn.notObserved"))}
          {stat(t("kn.price"), money(cap.price_median), `${money(cap.price_min)}–${money(cap.price_max)}`)}
          {stat(t("kn.rating"), cap.avg_rating != null ? cap.avg_rating.toFixed(2) : "—", cap.rating_basis ?? undefined)}
          {stat(t("kn.feeHeadroom"), cap.fee_headroom != null ? pct(cap.fee_headroom) : "—",
            <span className="flex items-center gap-1.5">{t("kn.landedCoverage", { c: pct(cap.landed_cost_coverage ?? 0) })}<KindBadge kind="derived" /></span>)}
          {stat(t("kn.maxFob"), cap.max_fob_median != null ? money(cap.max_fob_median, 2) : "—", cap.landed_cost_basis ?? t("kn.noLandedCost"))}
        </div>
        <div className="grid gap-3 border-t border-line p-4 text-sm md:grid-cols-3">
          <div>
            <div className="text-xs text-ink-3">{t("kn.dental")}</div>
            <div className="mt-1 flex items-center gap-2"><Meter value={cap.dental_confidence} max={100} /><span className="font-mono tabular-nums">{num(cap.dental_confidence)}</span></div>
          </div>
          <div>
            <div className="text-xs text-ink-3">{t("kn.data")}</div>
            <div className="mt-1 flex items-center gap-2"><Meter value={cap.data_confidence} max={100} /><span className="font-mono tabular-nums">{num(cap.data_confidence)}</span></div>
          </div>
          <div>
            <div className="text-xs text-ink-3">{t("kn.concentration")}</div>
            <div className="mt-1 font-mono tabular-nums">HHI {num(cap.hhi)} · {t("kn.top3")} {pct(cap.top3_share)}</div>
            {cap.top_brand && <div className="text-xs text-ink-3">{t("kn.topBrand")}: {cap.top_brand}</div>}
          </div>
          {cap.tier_entry_below != null && (
            <div className="text-xs text-ink-3 md:col-span-3">{t("kn.tiers", { a: money(cap.tier_entry_below), b: money(cap.tier_premium_from) })}</div>
          )}
        </div>
      </Card>
      {opp && (
        <Card>
          <CardHeader title={t("kn.opportunity")}
            subtitle={<span>{t("kn.coverage")}: {pct(opp.evidence_coverage)}</span>}
            right={opp.opportunity_score != null
              ? <span className="font-mono text-2xl tabular-nums text-accent">{num(opp.opportunity_score)}</span>
              : <Badge color="var(--warn)">{t("kn.insufficient")}</Badge>} />
          <div className="grid gap-5 p-4 pt-0 lg:grid-cols-2">
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">{t("kn.dimensions")}</h3>
              <ul className="space-y-1.5">
                {DIMS.map((d) => {
                  const cell = opp.evidence_matrix[d];
                  const s = opp.dimensions[d];
                  return (
                    <li key={d} className="grid grid-cols-[9rem_1fr_3rem] items-center gap-2 text-xs" title={cell?.basis ?? ""}>
                      <span className="truncate text-ink-2">{t(`kn.dim.${d}`)}</span>
                      {s == null ? <span className="text-ink-3">{t("kn.unknown")}</span> : <Meter value={s} max={100} color={LEVEL_COLOR[cell?.status ?? "Unknown"]} />}
                      <span className="text-right font-mono tabular-nums">{s == null ? "—" : num(s)}</span>
                    </li>
                  );
                })}
              </ul>
            </div>
            <div className="space-y-4">
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">{t("kn.reasons")}</h3>
                <ol className="list-decimal space-y-1 pl-5 text-sm text-ink-2">{opp.reasons.map((r, i) => <li key={i}>{r}</li>)}</ol>
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">{t("kn.patterns")}</h3>
                {opp.patterns.length === 0 ? <p className="text-xs text-ink-3">{t("kn.noPatterns")}</p> : (
                  <div className="flex flex-wrap gap-1.5">{opp.patterns.map((p, i) => <Badge key={i} color="var(--good)">{String(p.code)} · {String(p.name)}</Badge>)}</div>
                )}
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">{t("kn.risks")}</h3>
                <ul className="space-y-1 text-xs">
                  {opp.risks.map((r, i) => (
                    <li key={i} className="flex gap-2">
                      <Badge color={SEV_COLOR[String(r.severity)] ?? "var(--ink-3)"}>{t(`kn.sev.${String(r.severity)}`)}</Badge>
                      <span className="text-ink-2">{String(r.detail)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

function View() {
  const mn = useMarketName();
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const markets = useApi<MarketRow[]>("/markets");
  const [globalMarket] = useMarket();
  const names = (markets.data ?? []).map((m) => m.name);
  const market = params.get("market") ?? (names.includes(globalMarket) ? globalMarket : names[0] ?? "");
  const enc = encodeURIComponent(market);
  const [view, setView] = useState<"machine" | "approved">("machine");
  const [tab, setTab] = useState<"map" | "heatmap" | "bubbles">("map");
  const initScope = params.get("scope");
  const [sel, setSel] = useState<Sel>(initScope === "segment" || initScope === "taxonomy"
    ? { scope: initScope, id: params.get("id") ?? "" } : { scope: "category", id: market });
  const [why, setWhy] = useState<Sel | null>(null);
  const tax = useApi<Taxonomy>(market ? `/markets/${enc}/taxonomy?view=${view}` : null, [market, view]);
  const cap = useApi<Capacity>(market ? `/markets/${enc}/capacity` : null, [market]);
  const opp = useApi<Opps>(market ? `/markets/${enc}/opportunities/explained` : null, [market]);
  const capOf = useMemo(() => new Map((cap.data?.rows ?? []).map((r) => [`${r.scope}:${r.scope_id}`, r])), [cap.data]);
  const oppOf = useMemo(() => new Map((opp.data?.rows ?? []).map((r) => [`${r.scope}:${r.scope_id}`, r])), [opp.data]);
  const current = sel.scope === "category" ? capOf.get(`category:${market}`) : capOf.get(`${sel.scope}:${sel.id}`);
  const segments = (cap.data?.rows ?? []).filter((r) => r.scope === "segment").sort((a, b) => (b.revenue_est ?? 0) - (a.revenue_est ?? 0));
  const nodes = tax.data?.nodes ?? [];
  const pick = (s: Sel) => setSel(s);
  // market map (spec 114): each segment / taxonomy node at its median price and modeled revenue, sized by
  // canonical products, coloured by the explainable opportunity score (grey: insufficient evidence)
  const bubbles = useMemo(() => {
    const pts = (cap.data?.rows ?? []).filter((r) => r.scope !== "category" && (r.price_median ?? 0) > 0 && (r.revenue_est ?? 0) > 0);
    const maxP = Math.max(1, ...pts.map((r) => r.products));
    return {
      grid: { left: 64, right: 24, top: 20, bottom: 48 },
      tooltip: { formatter: (p: { data: { raw: CapRow; score: number | null } }) => {
        const r = p.data.raw;
        return `<b>${r.label}</b><br/>${t("kn.price")} ${money(r.price_median)} · ${t("kn.revenue")} ${moneyShort(r.revenue_est)}<br/>` +
          `${num(r.products)} ${t("kn.products").toLowerCase()} · ${t("kn.opportunity")} ${p.data.score == null ? t("kn.insufficient") : p.data.score.toFixed(0)}`; } },
      xAxis: { ...axis, type: "log", name: t("kn.price"), nameLocation: "middle", nameGap: 28, axisLabel: { color: "var(--ink-3)", formatter: (v: number) => money(v) } },
      yAxis: { ...axis, type: "log", name: t("kn.revenue"), axisLabel: { color: "var(--ink-3)", formatter: (v: number) => moneyShort(v) } },
      series: [{ type: "scatter", data: pts.map((r) => {
        const o = oppOf.get(`${r.scope}:${r.scope_id}`);
        const score = o?.opportunity_score ?? null;
        return { value: [r.price_median, r.revenue_est], raw: r, score, symbolSize: 10 + 34 * Math.sqrt(r.products / maxP),
          itemStyle: { color: score == null ? "var(--ink-3)" : seq(score / 100), opacity: 0.85, borderColor: "var(--panel)", borderWidth: 2 } };
      }) }],
    } as unknown as EChartsOption;
  }, [cap.data, oppOf, t]);
  const rowCls = (active: boolean) => cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
    active ? "bg-accent/10 text-accent" : "text-ink-2 hover:bg-panel-2");

  return (
    <div className="space-y-5 p-4 md:p-6">
      <PageHeader title={t("kn.mapTitle")} subtitle={t("kn.mapSub")}
        right={<Select aria-label={t("mkt.label")} value={market} onChange={(e) => { router.push(`/intelligence?market=${encodeURIComponent(e.target.value)}`); setSel({ scope: "category", id: e.target.value }); }}>
          {names.map((n) => <option key={n} value={n}>{mn(n)}</option>)}
        </Select>} />
      <ErrorNote error={markets.error ?? tax.error ?? cap.error ?? opp.error} />
      <div className="flex flex-wrap items-center gap-2">
      <div className="flex gap-2" role="tablist">
        {(["map", "bubbles", "heatmap"] as const).map((k) => (
          <Button key={k} role="tab" aria-selected={tab === k} variant={tab === k ? "primary" : "outline"} size="sm" onClick={() => setTab(k)}>
            {t(k === "map" ? "kn.map" : k === "bubbles" ? "kn.bubbles" : "kn.heatmap")}
          </Button>
        ))}

      </div>
      {market && <Link href={`/galaxy/${encodeURIComponent(market)}`}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-3 text-sm text-ink-2 hover:border-accent hover:text-accent">
          <Sparkles className="h-4 w-4" />{t("nav.galaxy")}</Link>}
      </div>
      {tab === "map" ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
          <Card className="self-start">
            <CardHeader title={t("kn.taxonomy")} right={
              <Select aria-label={t("kn.taxonomy")} value={view} onChange={(e) => setView(e.target.value as "machine" | "approved")}>
                <option value="machine">{t("kn.machine")}</option><option value="approved">{t("kn.approved")}</option>
              </Select>} />
            <div className="space-y-0.5 px-2 pb-3">
              <button type="button" className={rowCls(sel.scope === "category")} onClick={() => pick({ scope: "category", id: market })}>
                <span className="flex-1 truncate font-medium">{t("kn.category")}</span>
                <span className="font-mono text-xs tabular-nums text-ink-3">{moneyShort(capOf.get(`category:${market}`)?.revenue_est)}</span>
              </button>
              {nodes.length === 0 && <p className="px-2 py-2 text-xs text-ink-3">{t("kn.noTaxonomy")}</p>}
              {nodes.map((n) => (
                <button key={n.node_key} type="button" className={rowCls(sel.scope === "taxonomy" && sel.id === n.node_key)}
                  style={{ paddingLeft: `${0.5 + n.depth * 0.9}rem` }} onClick={() => pick({ scope: "taxonomy", id: n.node_key })}>
                  <ChevronRight className="h-3 w-3 shrink-0 text-ink-3" />
                  <span className="flex-1 truncate" title={n.approved_label ?? n.value}>{n.approved_label ?? humanize(n.value)}</span>
                  {n.status !== "machine" && <Badge color={n.status === "rejected" ? "var(--bad)" : "var(--good)"}>{n.status}</Badge>}
                  <span className="font-mono text-xs tabular-nums text-ink-3">{num(n.products)}</span>
                </button>
              ))}
              <div className="px-2 pt-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{t("kn.segments")}</div>
              {segments.map((s) => (
                <button key={s.scope_id} type="button" className={rowCls(sel.scope === "segment" && sel.id === s.scope_id)}
                  onClick={() => pick({ scope: "segment", id: s.scope_id })}>
                  <span className="flex-1 truncate" title={s.label}>{s.label}</span>
                  <span className="font-mono text-xs tabular-nums text-ink-3">{moneyShort(s.revenue_est)}</span>
                </button>
              ))}
            </div>
          </Card>
          <div className="min-w-0">
            {current ? (
              <LeafCard market={market} cap={current} opp={oppOf.get(`${sel.scope}:${sel.id}`)}
                onWhy={() => setWhy({ scope: sel.scope, id: sel.scope === "category" ? market : sel.id })} />
            ) : <Card><Empty title={t("common.notAvailable")} /></Card>}
          </div>
        </div>
      ) : tab === "bubbles" ? (
        <Card>
          <CardHeader title={t("kn.bubbles")} subtitle={t("kn.bubblesSub")} />
          <div className="px-2 pb-3">
            <EChart option={bubbles} height={420} label={t("kn.bubbles")} onEvents={{ click: (p) => {
              const r = (p as { data?: { raw?: CapRow } }).data?.raw;
              if (r) { setSel({ scope: r.scope as Sel["scope"], id: r.scope_id }); setTab("map"); } } }} />
          </div>
        </Card>
      ) : (
        <Card>
          <CardHeader title={t("kn.heatmap")} />
          <div className="overflow-x-auto px-2 pb-3">
            <table className="w-full min-w-[56rem] text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-3">
                  <th scope="col" className="px-2 py-2">{t("kn.opportunity")}</th>
                  <th scope="col" className="px-1 text-center">{t("kn.score")}</th>
                  {DIMS.map((d) => <th scope="col" key={d} className="px-1 text-center">{t(`kn.dim.${d}`)}</th>)}
                </tr>
              </thead>
              <tbody>
                {(opp.data?.rows ?? []).map((r) => (
                  <tr key={`${r.scope}:${r.scope_id}`} className="cursor-pointer hover:bg-panel-2"
                    onClick={() => { setSel({ scope: r.scope as Sel["scope"], id: r.scope_id }); setTab("map"); }}>
                    <th scope="row" className="max-w-[16rem] truncate px-2 py-1 text-left font-normal text-ink-2" title={r.label}>{r.label}</th>
                    {r.opportunity_score == null
                      ? <td className="px-1 text-center"><Badge color="var(--warn)">{t("kn.insufficient")}</Badge></td>
                      : <HeatCell v={r.opportunity_score} />}
                    {DIMS.map((d) => <HeatCell key={d} v={r.dimensions[d]} />)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {why && <WhyDrawer market={market} entityType={why.scope} entityId={why.id} onClose={() => setWhy(null)} />}
    </div>
  );
}

export default function IntelligencePage() {
  return <Suspense><View /></Suspense>;
}
