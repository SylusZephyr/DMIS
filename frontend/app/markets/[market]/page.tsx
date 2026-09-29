"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { ChevronDown, ChevronRight } from "lucide-react";
import { axis, EChart } from "@/components/charts/echart";
import { ForecastCard } from "@/components/forecast-card";
import { type Momentum, type PriceBand, type SegBrand, SegmentDetail } from "@/components/segment-detail";
import { ComponentBar, Explain, fmt, interval, MetricTile, type MetricRow } from "@/components/metric";
import { Badge, Button, Card, CardHeader, Empty, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { ExportButtons } from "@/components/export-button";
import { GlossaryBar } from "@/components/glossary";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { apiPost } from "@/lib/api-typed";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { basisZh } from "@/lib/basis-zh";
import { categoricalMap, OTHER, seq } from "@/lib/viz";
import { useMarketName } from "@/lib/market-name";
import { opportunityColor } from "@/lib/colors";

type Summary = {
  revenue_month: { estimate: number; low: number; high: number; floor: number; ceiling: number | null;
    headline?: number; headline_basis?: string; model_validated?: boolean; model_issues?: string[];
    undecided?: { listings: number; floor: number; estimate: number; floor_share: number | null; decided_floor: number } | null };
  units_month: { estimate: number; low: number; high: number }; hhi: number; entrant_success_rate: number | null;
  evidence_grade: "A" | "B" | "C" | "D"; badged_share: number; as_of: string; as_of_basis: string; snapshots: number;
  demand_model: { family: string; n: number; crossvalidation: { auc_badged_vs_unbadged?: number | null; coverage_95?: number } | null;
    price_association: { value: number; low: number | null; high: number | null } | null };
  unit_cost: { derived_field: boolean; note: string | null; valid_listings: number };
  top_brands: { brand: string; share_est: number; share_lo: number; share_hi: number }[];
};
type Seg = { segment_id: string; segment_label: string; family_label: string; listings_v3: number; revenue_est: number; revenue_lo: number;
  revenue_hi: number; hhi_est: number; top_brand_est: string | null; entrants: number; entrant_success_rate: number | null;
  entrant_revenue_share: number | null; quality_gap_share: number | null; rating_bar: number | null; unbadged_share: number | null;
  entrant_value_kind: string | null; entrant_value_expected: number | null; entrant_value_lo: number | null; entrant_value_hi: number | null;
  entrant_target_prob: number | null; opportunity_index: number | null; opportunity_level: string | null; opportunity_coverage: number | null;
  top_terms: string[] | null; segment_description: string | null; segment_description_zh: string | null; margin_rate_median: number | null;
  opp_demand: number | null; opp_entry: number | null; opp_margin: number | null; opp_competition: number | null;
  opp_quality_gap: number | null; opp_saturation: number | null };
type Prod = { product_id: string; title: string; brand: string | null; price: number | null; segment_id: string; units_est?: number | null;
  units_lo?: number | null; units_hi?: number | null; revenue_est?: number | null; opportunity_score: number | null; is_entrant?: boolean };
type Rec = { segment_id: string; segment_label: string; opportunity_index: number | null; features: string[];
  feature_evidence: { feature: string; lift: number; lift_lo: number; lift_hi: number; q_value: number; supply_share: number; demand_share: number }[];
  price_range: [number, number] | null; expected_units: number | null; expected_units_lo: number | null; expected_units_hi: number | null;
  comparables: { id: string; title: string; price: number; units_est: number }[]; basis: string };
type Gap = { segment_id: string; feature: string; listings_with: number; supply_share: number; demand_share: number; lift: number | null;
  lift_lo: number | null; lift_hi: number | null; q_value: number; is_gap: boolean };
type Scope = { has_definition: boolean; counts: Record<string, number>; sub_categories: { category: string; listings: number; decision: string;
  source: string; reason: string }[] };
type Snap = { dataset_id: string; period: string; declared: boolean; rows: number };

const gradeTone = (g: string) => (g === "A" ? "var(--good)" : g === "B" ? "var(--accent)" : g === "C" ? "var(--warn)" : "var(--ink-2)");

type Engine = { score: number | null; status: string };


function SegmentRow({ s, market, rank, engine }: { s: Seg; market: string; rank: number; engine?: Engine }) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const ex = useApi<{ metrics: MetricRow[]; brands: SegBrand[]; price_bands: PriceBand[]; momentum: Momentum | null }>(open ? `/markets/${encodeURIComponent(market)}/segments/${s.segment_id}/explain` : null);
  const desc = lang === "zh" ? s.segment_description_zh : s.segment_description;
  return (
    <div className="border-b border-line">
      <button className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-panel-2" onClick={() => setOpen(!open)}>
        <span className="mt-0.5 w-5 font-mono text-xs text-ink-3">{rank}</span>
        {open ? <ChevronDown className="mt-0.5 h-4 w-4 text-ink-3" /> : <ChevronRight className="mt-0.5 h-4 w-4 text-ink-3" />}
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">{s.segment_label}</span>
          <span className="block text-[11px] text-ink-3">{desc}</span>
          <span className="mt-1 flex flex-wrap gap-1">{(s.top_terms ?? []).slice(0, 6).map((k) => <Badge key={k}>{k}</Badge>)}</span>
        </span>
        <span className="hidden w-40 text-right md:block">
          <span className="block font-mono text-sm">{moneyShort(s.revenue_est)}</span>
          <span className="block text-[10px] text-ink-3">{moneyShort(s.revenue_lo)}–{moneyShort(s.revenue_hi)}</span>
        </span>
        <span className="hidden w-24 text-right font-mono text-xs lg:block">{num(s.hhi_est)}</span>
        <span className="hidden w-24 text-right font-mono text-xs lg:block">{s.entrant_success_rate == null ? "—" : pct(s.entrant_success_rate)}</span>
        <span className="w-28 text-right">
          {/* the opportunity engine's score (as on the board); the older index is shown beneath for reference */}
          <span className="block font-mono text-lg" style={{ color: opportunityColor(engine?.score ?? null) }}>{engine?.score?.toFixed(0) ?? "—"}</span>
          <span className="block text-[10px] text-ink-3">{engine && engine.score == null ? `${t("board.insufficientShort")} · ` : ""}
            {s.opportunity_index != null && <span title={t("market.indexTip")}>{t("market.indexShort")} {s.opportunity_index.toFixed(0)}</span>}</span>
        </span>
      </button>
      {open && (
        <div className="grid grid-cols-1 gap-4 bg-panel-2/40 px-12 pb-4 pt-1 lg:grid-cols-[280px_1fr]">
          <div className="space-y-2">
            <div className="text-[11px] uppercase text-ink-3">{t("market.components")}</div>
            {(["opp_demand", "opp_entry", "opp_margin", "opp_competition", "opp_quality_gap", "opp_saturation"] as const).map((k) =>
              <ComponentBar key={k} id={k} value={s[k]} />)}
          </div>
          <div className="overflow-x-auto" tabIndex={0}>
            {!ex.data ? <div className="text-xs text-ink-3">{t("common.loading")}</div> : (
              <table className="w-full text-xs">
                <tbody className="divide-y divide-line">
                  {ex.data.metrics.filter((r) => !r.metric.startsWith("opp_")).map((r) => (
                    <tr key={r.metric}>
                      <td className="py-1 pr-2">{t(`metric.${r.metric}.0`).startsWith("metric.") ? r.metric : t(`metric.${r.metric}.0`)} <Explain row={r} /></td>
                      <td className="pr-2 text-right font-mono">{fmt(r.value, r.unit)}</td>
                      <td className="pr-2 font-mono text-ink-3">{interval(r) ?? ""}</td>
                      <td className="pr-2 font-mono text-ink-3">{r.n != null ? `n=${num(r.n)}` : ""}</td>
                      <td className="max-w-[340px] truncate text-ink-3" title={r.basis}>{lang === "zh" ? basisZh(r.caveat || r.basis || "") : r.caveat || r.basis}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {ex.data && <SegmentDetail brands={ex.data.brands ?? []} bands={ex.data.price_bands ?? []} momentum={ex.data.momentum ?? null} />}
        </div>
      )}
    </div>
  );
}

export default function MarketAnalysis() {
  const mn = useMarketName();
  const market = decodeURIComponent(useParams<{ market: string }>().market);
  const router = useRouter();
  const { t, lang } = useI18n();
  const enc = encodeURIComponent(market);
  const met = useApi<{ summary: Summary | null; rows: MetricRow[] }>(`/markets/${enc}/metrics?scope=market`);
  const segs = useApi<Seg[]>(`/markets/${enc}/segments-v3`);
  const prods = useApi<{ total: number; items: Prod[] }>(`/markets/${enc}/products?limit=3000&sort=revenue_est`);
  const recs = useApi<Rec[]>(`/markets/${enc}/recommendations`);
  const gaps = useApi<Gap[]>(`/markets/${enc}/gaps`);
  const scope = useApi<Scope>(`/markets/${enc}/scope`);
  const snaps = useApi<Snap[]>(`/markets/${enc}/snapshots`);
  const [snap, setSnap] = useState<string>("latest");
  const snapPts = useApi<{ price: number; sales: number | null; title: string }[]>(
    snap !== "latest" ? `/markets/${enc}/snapshots/${snap}/matrix` : null, [snap]);
  const [colorBy, setColorBy] = useState<"segment" | "opportunity" | "entrant">("segment");
  const [logScale, setLogScale] = useState(true);
  const [segFilter, setSegFilter] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const s = met.data?.summary;
  const row = (m: string) => met.data?.rows.find((r) => r.metric === m) ?? null;
  // every tile computed from the demand model says so when the model failed hold-out validation
  const notValidated = met.data?.summary?.revenue_month.model_validated === false
    ? <span className="block text-warn" title={(met.data.summary.revenue_month.model_issues ?? []).join("; ")}>{t("market.modelBased")}</span> : null;
  const eng = useApi<{ rows: { scope: string; scope_id: string; opportunity_score: number | null; status: string }[] }>(`/markets/${enc}/opportunities/explained`);
  const engOf = useMemo(() => new Map((eng.data?.rows ?? []).filter((r) => r.scope === "segment").map((r) => [String(r.scope_id), { score: r.opportunity_score, status: r.status }])), [eng.data]);
  const segList = useMemo(() => segs.data ?? [], [segs.data]);
  // segments ranked by the engine score like the board; unscored ones follow by the older index
  const segRanked = useMemo(() => [...segList].sort((a, b) => {
    const x = engOf.get(a.segment_id)?.score ?? null, y = engOf.get(b.segment_id)?.score ?? null;
    if (x != null || y != null) return (y ?? -1) - (x ?? -1);
    return (b.opportunity_index ?? -1) - (a.opportunity_index ?? -1);
  }), [segList, engOf]);
  const segLabel = useMemo(() => new Map(segList.map((x) => [x.segment_id, x.segment_label])), [segList]);
  const famOf = useMemo(() => new Map(segList.map((x) => [x.segment_id, x.family_label])), [segList]);
  const entrantShare = useMemo(() => {
    const w = segList.filter((x) => x.entrant_revenue_share != null);
    const tot = w.reduce((a, x) => a + (x.revenue_est || 0), 0);
    return tot ? w.reduce((a, x) => a + (x.entrant_revenue_share ?? 0) * (x.revenue_est || 0), 0) / tot : null;
  }, [segList]);

  const matrix = useMemo<EChartsOption>(() => {
    const val = (v: number | null | undefined) => (v == null ? null : logScale ? Math.max(v, 0.5) : v);
    const axes = (yName: string) => ({
      xAxis: { ...axis, type: logScale ? "log" : "value", name: t("mx.price"), nameLocation: "middle", nameGap: 28 },
      yAxis: { ...axis, type: logScale ? "log" : "value", name: yName, nameLocation: "middle", nameGap: 44 },
    });
    if (snap !== "latest") {
      const pts = (snapPts.data ?? []).filter((p) => p.price != null && p.sales != null);
      return {
        ...axes(t("mx.observedUnits")),
        tooltip: { formatter: (p: { data: (number | string)[] }) => `<b>${truncate(String(p.data[2]), 70)}</b><br/>${money(Number(p.data[0]), 2)} · ${num(Number(p.data[1]))}+ ${t("mx.perMo")}` },
        series: [{ type: "scatter", symbolSize: 9, data: pts.map((p) => [p.price, val(p.sales), p.title]), itemStyle: { color: "#3987e5", borderColor: "#0a1220", borderWidth: 2 } }],
      } as unknown as EChartsOption;
    }
    const items = (prods.data?.items ?? []).filter((p) => p.price != null && p.units_est != null && (!segFilter || p.segment_id === segFilter));
    const fams = [...new Set(segList.map((x) => x.family_label))];
    const famTot = new Map(fams.map((f) => [f, segList.filter((x) => x.family_label === f).reduce((a, x) => a + (x.revenue_est || 0), 0)]));
    const famOrder = fams.sort((a, b) => (famTot.get(b) ?? 0) - (famTot.get(a) ?? 0));
    const cmap = categoricalMap(famOrder);
    const maxRev = Math.max(1, ...items.map((p) => p.revenue_est ?? 0));
    const OTHER_G = t("mx.other"), ENT = t("mx.entrant"), EST = t("mx.established"), ALL = t("common.products");
    const groupOf = (p: Prod) => colorBy === "segment" ? (cmap.has(famOf.get(p.segment_id) ?? "") ? famOf.get(p.segment_id)! : OTHER_G)
      : colorBy === "entrant" ? (p.is_entrant ? ENT : EST) : ALL;
    const groups = colorBy === "segment" ? [...famOrder.filter((f) => cmap.has(f)), OTHER_G] : colorBy === "entrant" ? [ENT, EST] : [ALL];
    const colorOf = (p: Prod) => colorBy === "segment" ? cmap.get(famOf.get(p.segment_id) ?? "") ?? OTHER
      : colorBy === "entrant" ? (p.is_entrant ? "#d95926" : "#3987e5") : seq((p.opportunity_score ?? 0) / 100);
    return {
      ...axes(t("mx.estUnits")),
      legend: groups.length > 1 ? { top: 0, type: "scroll", textStyle: { color: "#9fb3cf" } } : undefined,
      grid: { left: 64, right: 24, top: groups.length > 1 ? 40 : 20, bottom: 48 },
      tooltip: { formatter: (p: { data: { raw: Prod } }) => { const r = p.data.raw;
        return `<b>${truncate(r.title, 70)}</b><br/>${r.brand ?? ""} · ${money(r.price, 2)}<br/>` +
          `${num(r.units_est)} ${t("mx.perMoUnits")} (95%: ${num(r.units_lo)}–${num(r.units_hi)})<br/>${t("mx.revenue")} ${moneyShort(r.revenue_est)} ${t("mx.perMo")} · ${t("mx.opportunity")} ${r.opportunity_score?.toFixed(0) ?? "—"}<br/>${truncate(segLabel.get(r.segment_id) ?? "", 60)}`; } },
      series: groups.map((g) => ({ name: g, type: "scatter",
        color: g === OTHER_G ? OTHER : colorBy === "segment" ? cmap.get(g) : colorBy === "entrant" ? (g === EST ? "#3987e5" : "#d95926") : "#3987e5",
        data: items.filter((p) => groupOf(p) === g).map((p) => ({ value: [p.price, val(p.units_est)], raw: p,
          symbolSize: 8 + 30 * Math.sqrt((p.revenue_est ?? 0) / maxRev), itemStyle: { color: colorOf(p) } })),
        itemStyle: { borderColor: "#0a1220", borderWidth: 2, opacity: 0.9 }, emphasis: { focus: "series" } })),
    } as unknown as EChartsOption;
  }, [prods.data, segList, segFilter, colorBy, logScale, snap, snapPts.data, famOf, segLabel, t]);

  const painBar = useMemo(() => {
    const d = segList.filter((x) => x.quality_gap_share != null).sort((a, b) => (a.quality_gap_share ?? 0) - (b.quality_gap_share ?? 0)).slice(-12);
    return {
      grid: { left: 240, right: 40, top: 10, bottom: 30 }, tooltip: { trigger: "item" as const, valueFormatter: (v: unknown) => pct(Number(v)) },
      xAxis: { ...axis, type: "value" as const, min: 0, axisLabel: { color: "#62789a", formatter: (v: number) => pct(v) } },
      yAxis: { ...axis, type: "category" as const, data: d.map((x) => truncate(x.segment_label, 40)) },
      series: [{ type: "bar" as const, barWidth: 10, label: { show: true, position: "right" as const, color: "#9fb3cf", fontSize: 10, formatter: (p: { value: unknown }) => pct(Number(p.value), 1) },
        data: d.map((x) => ({ value: x.quality_gap_share, itemStyle: { color: "#3987e5", borderRadius: [0, 4, 4, 0] } })) }],
    };
  }, [segList]);

  const decide = async (category: string, decision: string) => {
    setErr(null);
    try { await apiPost("/markets/{market}/scope", { category, decision }, { market }); scope.reload(); } catch (e) { setErr((e as Error).message); }
  };
  const top = recs.data?.find((r) => r.features.length) ?? recs.data?.[0];
  const snapCount = snaps.data?.length ?? s?.snapshots ?? 1;

  return (
    <div className="pb-16">
      <PageHeader title={`${mn(market)} — ${t("market.title")}`}
        subtitle={<>{t("market.subtitle")}<GlossaryBar terms={["interval95", "floor", "evidenceGrade", "opportunityIndex", "hhi", "entrant", "lift", "fdr"]} /></>}
        right={<div className="flex flex-wrap items-center gap-2">
          {s && <Badge color={gradeTone(s.evidence_grade)}>{t("common.evidenceGrade")} {s.evidence_grade} · {t(`market.grade.${s.evidence_grade}`)}</Badge>}
          <Link href={`/markets/${enc}/hierarchy`} className="text-xs text-accent">Hierarchy →</Link>
          <Link href={`/competitors?market=${enc}`} className="text-xs text-accent">{t("nav.competitors")} →</Link>
          <Link href={`/launch?market=${enc}`} className="text-xs text-accent">{t("nav.launch")} →</Link>
        </div>} />
      <ErrorNote error={met.error ?? segs.error ?? err} />
      {!s ? <Card className="mx-6"><Empty title={t("common.loading")} /></Card> : (
        <div className="space-y-4 px-6">
          <Card className="grid grid-cols-2 divide-x divide-line md:grid-cols-3 xl:grid-cols-6">
            {/* headline = what the data certainly shows; the modelled estimate sits beside it, flagged by validation */}
            <MetricTile row={row("revenue_month_floor")} label={t("market.size")}
              sub={<>
                <span className="block">{t("market.sizeSub", { est: moneyShort(s.revenue_month.estimate), lo: moneyShort(s.revenue_month.low), hi: moneyShort(s.revenue_month.high) })}</span>
                {s.revenue_month.model_validated != null && (
                  <span className={`block ${s.revenue_month.model_validated ? "text-good" : "text-warn"}`} title={(s.revenue_month.model_issues ?? []).join("; ")}
                    data-testid="size-validation">
                    {s.revenue_month.model_validated ? t("market.sizeValidated") : t("market.sizeNotValidated")}
                    {!s.revenue_month.model_validated && s.revenue_month.model_issues?.[0] ? `: ${s.revenue_month.model_issues[0]}` : ""}
                  </span>)}
                {s.revenue_month.ceiling != null && <span className="block">{t("market.sizeCeiling", { ceiling: moneyShort(s.revenue_month.ceiling) })}</span>}
                {!!s.revenue_month.undecided?.listings && s.revenue_month.undecided.floor_share != null && s.revenue_month.undecided.floor_share > 0 && (
                  <a href="#scope" className="block text-warn hover:underline" data-testid="size-undecided">
                    {t("market.sizeUndecided", { v: moneyShort(s.revenue_month.undecided.floor), share: pct(s.revenue_month.undecided.floor_share),
                      n: num(s.revenue_month.undecided.listings) })}
                  </a>)}
              </>} />
            {/* units lead with the observed floor like revenue; model-derived tiles say so when the model failed validation */}
            {row("units_month_floor") ? (
              <MetricTile row={row("units_month_floor")} label={t("market.unitsObserved")}
                sub={<span className="block">{t("market.unitsModelled", { est: num(s.units_month.estimate), lo: num(s.units_month.low), hi: num(s.units_month.high) })}
                  {notValidated}</span>} />
            ) : <MetricTile row={row("units_month")} label={t("market.units")} />}
            <MetricTile row={row("hhi")} label={t("market.concentration")} sub={<span className="block">{s.top_brands[0] ? `${s.top_brands[0].brand} ${pct(s.top_brands[0].share_est)}` : ""}{notValidated}</span>} />
            <MetricTile row={row("entrant_success_rate")} label={t("market.entry")} sub={notValidated} />
            <div className="p-4">
              <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("market.model")}</div>
              <div className="mt-1 font-mono text-sm">{t("market.modelSub", { family: s.demand_model.family, auc: s.demand_model.crossvalidation?.auc_badged_vs_unbadged?.toFixed(2) ?? "—" })}</div>
              <div className="mt-0.5 text-[11px] text-ink-3">{t("market.badged", { share: pct(s.badged_share) })}</div>
            </div>
            <div className="p-4">
              <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("market.costNote")}</div>
              <div className="mt-1 text-[11px] text-ink-2">{s.unit_cost.derived_field ? t("market.costDerived") : s.unit_cost.valid_listings ? `${num(s.unit_cost.valid_listings)} ${t("common.listings")}` : t("market.costNone")}</div>
            </div>
          </Card>

          {top && (
            <Card>
              <CardHeader title={t("market.recommended")} subtitle={t("market.recommendedSub")} />
              <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-2">
                <div className="space-y-2 text-sm">
                  <div className="text-base font-semibold">{top.segment_label}</div>
                  {!top.features.length && <div className="text-xs text-warn">{t("market.noGap")}</div>}
                  {top.feature_evidence.map((f) => (
                    <div key={f.feature} className="flex flex-wrap items-center gap-2 text-xs">
                      <Badge color="var(--good)">{f.feature}</Badge>
                      <span className="font-mono">{t("common.lift")} {f.lift.toFixed(2)}× ({f.lift_lo.toFixed(2)}–{f.lift_hi.toFixed(2)})</span>
                      <span className="text-ink-3">q {f.q_value.toFixed(3)} · supply {pct(f.supply_share)} → demand {pct(f.demand_share)}</span>
                    </div>
                  ))}
                  <div className="text-xs">{t("market.priceRange")}: <span className="font-mono">{top.price_range ? `${money(top.price_range[0], 2)} – ${money(top.price_range[1], 2)}` : "—"}</span></div>
                  <div className="text-xs">{t("market.expected")}: <span className="font-mono">{num(top.expected_units)}</span>
                    <span className="text-ink-3"> ({num(top.expected_units_lo)}–{num(top.expected_units_hi)}) · {lang === "zh" ? basisZh(top.basis) : top.basis}</span>
                    {notValidated}</div>
                </div>
                <div>
                  <div className="mb-1 text-[11px] uppercase text-ink-3">{t("market.comparables")}</div>
                  {top.comparables.map((c) => (
                    <div key={c.id} className="flex justify-between gap-2 border-b border-line py-1 text-xs">
                      <span className="truncate">{c.title}</span><span className="whitespace-nowrap font-mono">{money(c.price, 2)} · {num(c.units_est)}/mo</span></div>
                  ))}
                </div>
              </div>
            </Card>
          )}

          <Card>
            <CardHeader title={t("market.segmentsTitle")} subtitle={t("market.segmentsSub")}
              right={<div className="flex flex-col items-end gap-1">
                <ExportButtons path={`/markets/${enc}/export/segments`} label={t("exp.segments")} />
                <ExportButtons path={`/markets/${enc}/export/products`} label={t("exp.products")} />
              </div>} />
            <div className="hidden gap-3 border-b border-line px-4 py-1.5 text-[10px] uppercase text-ink-3 md:flex">
              <span className="w-5" /><span className="w-4" /><span className="flex-1" />
              <span className="w-40 text-right">{t("metric.revenue_month.0")}</span>
              <span className="hidden w-24 text-right lg:block">HHI</span>
              <span className="hidden w-24 text-right lg:block">{t("metric.entrant_success_rate.0")}</span>
              <span className="w-28 text-right">{t("market.oppScore")}</span>
            </div>
            {segList.length === 0 ? <Empty title={t("common.loading")} /> :
              segRanked.filter((x) => showAll || x.listings_v3 >= 5).map((x, i) => <SegmentRow key={x.segment_id} s={x} market={market} rank={i + 1} engine={engOf.get(x.segment_id)} />)}
            {segList.some((x) => x.listings_v3 < 5) && (
              <button className="w-full py-2 text-xs text-accent" onClick={() => setShowAll(!showAll)}>
                {showAll ? t("market.hideSmall") : t("market.showSmall", { n: segList.filter((x) => x.listings_v3 < 5).length })}
              </button>)}
          </Card>

          <Card>
            <CardHeader title={t("market.matrix")} subtitle={t("market.matrixSub")}
              right={<div className="flex flex-wrap items-center gap-2 text-xs">
                <label>{t("market.snapshot")} <Select value={snap} onChange={(e) => setSnap(e.target.value)} className="h-8 text-xs">
                  <option value="latest">{t("market.latest")}</option>
                  {(snaps.data ?? []).map((d) => <option key={d.dataset_id} value={d.dataset_id}>{d.period}{d.declared ? "" : ` (${t("mx.uploadDate")})`}</option>)}
                </Select></label>
                <label>{t("market.colorBy")} <Select value={colorBy} onChange={(e) => setColorBy(e.target.value as typeof colorBy)} className="h-8 text-xs">
                  <option value="segment">{t("mx.bySegment")}</option><option value="opportunity">{t("mx.byOpportunity")}</option><option value="entrant">{t("mx.byEntrant")}</option></Select></label>
                <Select aria-label={t("a11y.segmentFilter")} value={segFilter} onChange={(e) => setSegFilter(e.target.value)} className="h-8 max-w-[220px] text-xs">
                  <option value="">{t("common.all")}</option>{segList.map((x) => <option key={x.segment_id} value={x.segment_id}>{truncate(x.segment_label, 40)}</option>)}</Select>
                <Button size="sm" variant="outline" onClick={() => setLogScale(!logScale)}>{t("market.scale")}: {logScale ? t("mx.log") : t("mx.linear")}</Button>
              </div>} />
            {snapCount < 2 && <div className="px-4 text-[11px] text-ink-3">{t("market.oneSnapshot")}</div>}
            <EChart option={matrix} height={440} onEvents={{ click: (p) => { const d = (p as { data?: { raw?: Prod } }).data; if (d?.raw) router.push(`/products/${d.raw.product_id}`); } }} />
          </Card>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <ForecastCard market={market} fallback={<>
              <p>{t("market.forecastNone", { n: snapCount, share: entrantShare == null ? "—" : pct(entrantShare) })}</p>
              <div className="mt-3 grid grid-cols-2 gap-3">
                {segList.slice(0, 6).map((x) => (
                  <div key={x.segment_id} className="rounded-lg border border-line p-2 text-xs">
                    <div className="truncate">{x.segment_label}</div>
                    <div className="font-mono">{x.entrants} · {x.entrant_success_rate == null ? "—" : pct(x.entrant_success_rate)}</div>
                    <div className="text-ink-3">{t("metric.entrants.0")} · {t("metric.entrant_success_rate.0")}</div>
                  </div>
                ))}
              </div>
            </>} />
            <Card>
              <CardHeader title={t("market.pain")} subtitle={t("market.painProxy")} />
              {!segList.some((x) => x.quality_gap_share != null) ? <Empty title={t("common.notAvailable")} />
                : segList.every((x) => !x.quality_gap_share) ? <Empty title={t("market.painNone")}>{t("market.painNoneSub")}</Empty>
                : <EChart option={painBar} height={300} />}
            </Card>
          </div>

          <Card>
            <CardHeader title={t("market.allGaps")} subtitle={t("market.recommendedSub")} />
            {!gaps.data?.length ? <Empty title={t("common.notAvailable")} /> : (
              <div className="max-h-[360px] overflow-auto scrollbar-thin" tabIndex={0}>
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-panel text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-1.5">segment</th><th scope="col">feature</th><th scope="col">{t("common.listings")}</th><th scope="col">supply → demand</th><th scope="col">{t("common.lift")} (95%)</th><th scope="col">{t("common.q")}</th><th scope="col" className="pr-4" /></tr></thead>
                  <tbody className="divide-y divide-line">
                    {gaps.data.slice(0, 120).map((g, i) => (
                      <tr key={i} className={g.is_gap ? "bg-good/5" : ""}>
                        <td className="max-w-[240px] truncate px-4 py-1">{segLabel.get(g.segment_id) ?? g.segment_id}</td><td>{g.feature}</td>
                        <td className="font-mono">{g.listings_with}</td><td className="font-mono">{pct(g.supply_share)} → {pct(g.demand_share)}</td>
                        <td className="font-mono">{g.lift?.toFixed(2) ?? "—"} ({g.lift_lo?.toFixed(2) ?? "—"}–{g.lift_hi?.toFixed(2) ?? "—"})</td>
                        <td className="font-mono">{g.q_value.toFixed(3)}</td><td className="pr-4">{g.is_gap && <Badge color="var(--good)">gap</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card id="scope" className="scroll-mt-4">
            <CardHeader title={t("market.scope")} subtitle={t("market.scopeSub")}
              right={scope.data && <span className="text-xs text-ink-3">{Object.entries(scope.data.counts).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>} />
            {!scope.data?.sub_categories?.length ? <Empty title={t("common.notAvailable")} /> : (
              <table className="w-full text-xs">
                <thead className="text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-1.5">sub-category</th><th scope="col">{t("common.listings")}</th><th scope="col">{t("common.decision")}</th><th scope="col">{t("common.source")}</th><th scope="col">{t("common.reason")}</th><th scope="col" className="pr-4" /></tr></thead>
                <tbody className="divide-y divide-line">
                  {scope.data.sub_categories.map((c) => (
                    <tr key={c.category}>
                      <td className="px-4 py-1.5">{c.category}</td><td className="font-mono">{c.listings}</td>
                      <td><Badge color={c.decision === "in" ? "var(--good)" : c.decision === "out" ? "var(--bad)" : "var(--warn)"}>{c.decision}</Badge></td>
                      <td className="text-ink-3">{c.source}</td><td className="max-w-[420px] text-ink-3">{c.reason}</td>
                      <td className="space-x-1 whitespace-nowrap pr-4">
                        <Button size="sm" variant="ghost" onClick={() => decide(c.category, "in")}>{t("common.include")}</Button>
                        <Button size="sm" variant="ghost" onClick={() => decide(c.category, "out")}>{t("common.exclude")}</Button>
                        {c.source === "person" && <Button size="sm" variant="ghost" onClick={() => decide(c.category, "auto")}>{t("common.reset")}</Button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <div className="text-[11px] text-ink-3">{t("mx.asOf")} {s.as_of} ({s.as_of_basis}) · <Link href="/methodology" className="text-accent">{t("common.methodology")} →</Link></div>
        </div>
      )}
    </div>
  );
}
