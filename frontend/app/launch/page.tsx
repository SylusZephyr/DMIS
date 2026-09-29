"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { axis, EChart } from "@/components/charts/echart";
import { Badge, Button, Card, CardHeader, Empty, Input, LiveStatus, Select } from "@/components/ui/primitives";
import { PageHeader } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import { post, type MarketRow } from "@/lib/api";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { apiPost } from "@/lib/api-typed";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";
import { ModelFlag } from "@/components/market-size";

type Dist = { mean: number; p10: number; median: number; p90: number; lo: number; hi: number };
type Risk = { code: string; severity: "high" | "medium"; value: number | string; bar?: number; brand?: string; range?: number[] };
type CurvePoint = { price: number; units: number; revenue: number; revenue_lo: number; revenue_hi: number;
  profit?: number; profit_lo?: number; profit_hi?: number; p_positive?: number };
type Sim = {
  market: string; as_of: string | null; units: Dist; revenue: Dist & { target: number; p_target: number };
  profit?: Dist & { p_positive: number; target: number; p_target: number };
  break_even_units?: number | null; p_break_even?: number | null; payback_months?: number | null;
  economics: { referral_fee: number; fulfilment_fee: number; fulfilment_basis: string; unit_cost: number | null; unit_cost_basis: string;
    unit_margin: number | null; margin_rate: number | null; fixed_monthly_cost: number; launch_cost: number | null };
  assumptions: { price: number; rating: number | null; rating_basis: string; age_days: number; category: string | null;
    segment_offset: number; segment_offset_n: number; model_family: string; model_listings: number; simulations: number };
  price_curve?: { points: CurvePoint[]; optimise: "profit" | "revenue"; best_price: number; at_edge: boolean; caveat: string };
  entrants_actual: { n: number; median?: number; p25?: number; p75?: number };
  gaps: { feature: string; kind: string; lift: number; lift_lo: number; lift_hi: number; q_value: number; covered: boolean }[];
  risks: Risk[];
  placement: { segment_id: string; segment_label: string; family: string | null; user_chosen: boolean; vote_share: number | null;
    top_similarity: number; alternatives: { segment_id: string; segment_label: string; vote_share: number }[] };
  comparables: { id: string; title: string; brand: string | null; price: number; rating: number | null; units_est: number; units_lo: number;
    units_hi: number; revenue_est: number; is_entrant: boolean; similarity: number }[];
  segment: { opportunity_index: number | null; opportunity_level: string | null; hhi_est: number | null; top_brand_est: string | null;
    entrant_success_rate: number | null; rating_bar: number | null; revenue_est: number | null; revenue_lo: number | null; revenue_hi: number | null;
    segment_description: string | null; segment_description_zh: string | null; price_p10: number | null; price_p90: number | null };
  recommendation?: { features: string[]; price_range: string; expected_units: number | null };
};
type Compare = { segment_label: string; compare_on: "profit" | "revenue"; note: string;
  scenarios: { name: string; price: number; units: Dist; revenue: Dist; profit: (Dist & { p_positive: number }) | null; unit_margin: number | null;
    gaps_covered: number; gaps_total: number; risks: string[]; p_best: number; p_beats_first: number | null; break_even_units: number | null }[] };

const SEV: Record<string, string> = { high: "var(--bad)", medium: "var(--warn)" };
const n = (s: string) => (s.trim() === "" ? null : Number(s));

function Range({ d, money: m }: { d: Dist; money?: boolean }) {
  const f = (v: number) => (m ? moneyShort(v) : num(v, v < 10 ? 1 : 0));
  return <span className="font-mono text-[11px] text-ink-3">p10–p90 {f(d.p10)}–{f(d.p90)}</span>;
}

function Scenarios({ body }: { body: Record<string, unknown> }) {
  const { t } = useI18n();
  const base = Number(body.price) || 0;
  const [rows, setRows] = useState(() => [0.8, 1, 1.25].map((k, i) => ({ name: [t("launch.value"), t("launch.target"), t("launch.premium")][i],
    price: base ? String(Math.round(base * k * 100) / 100) : "", unit_cost: "" })));
  const [c, setC] = useState<Compare | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const edit = (i: number, k: "name" | "price" | "unit_cost", v: string) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const valid = rows.filter((r) => Number(r.price) > 0);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      setC(await post<Compare>("/launch/compare-v3", { ...body, scenarios: valid.map((r) => ({ name: r.name, price: Number(r.price), unit_cost: n(r.unit_cost) })) }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Card>
      <CardHeader title={t("launch.compareTitle")} subtitle={t("launch.compareSub")}
        right={<div className="flex gap-2">
          {rows.length < 6 && <Button size="sm" variant="ghost" onClick={() => setRows([...rows, { name: `#${rows.length + 1}`, price: "", unit_cost: "" }])}>+</Button>}
          <Button size="sm" disabled={busy || valid.length < 2} onClick={run} aria-busy={busy}>{busy ? "…" : t("launch.compare")}</Button><LiveStatus busy={busy} done={!!c} busyText={t("a11y.loading")} doneText={t("a11y.loaded")} /></div>} />
      <div className="grid grid-cols-1 gap-2 p-4 sm:grid-cols-3">
        {rows.map((r, i) => (
          <div key={i} className="flex gap-1">
            <Input value={r.name} onChange={(e) => edit(i, "name", e.target.value)} placeholder={t("launch.name")} />
            <Input type="number" value={r.price} onChange={(e) => edit(i, "price", e.target.value)} placeholder={t("launch.price")} className="w-24" />
            <Input type="number" value={r.unit_cost} onChange={(e) => edit(i, "unit_cost", e.target.value)} placeholder={t("launch.cost")} className="w-20" />
          </div>
        ))}
      </div>
      {err && <div className="px-4 pb-2 text-xs text-bad">{err}</div>}
      {c && (
        <div className="overflow-x-auto" tabIndex={0}>
          <table className="w-full text-xs [&_td]:pr-3 [&_th]:pr-3">
            <thead className="text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("launch.name")}</th><th scope="col">{t("launch.price")}</th>
              <th scope="col">{t("launch.unitsMedian")}</th><th scope="col">{t("launch.revenueMedian")}</th><th scope="col">{c.compare_on === "profit" ? t("launch.profitMean") : ""}</th>
              <th scope="col">P(&gt;0)</th><th scope="col">P({t("launch.best")})</th><th scope="col">P(&gt; #1)</th><th scope="col">{t("launch.gapsCovered")}</th><th scope="col" className="pr-4">{t("launch.risks")}</th></tr></thead>
            <tbody className="divide-y divide-line">
              {c.scenarios.map((x) => {
                const top = Math.max(...c.scenarios.map((y) => y.p_best));
                return (
                  <tr key={x.name}>
                    <td className="px-4 py-2 font-medium">{x.name} {x.p_best === top && <Badge color="var(--good)">{t("launch.best")}</Badge>}</td>
                    <td className="font-mono">{money(x.price, 2)}</td>
                    <td className="font-mono">{num(x.units.median, 1)} <div className="text-ink-3">{num(x.units.p10, 1)}–{num(x.units.p90, 1)}</div></td>
                    <td className="font-mono">{moneyShort(x.revenue.median)}</td>
                    <td className="font-mono">{x.profit ? moneyShort(x.profit.mean) : ""}</td>
                    <td className="font-mono">{x.profit ? pct(x.profit.p_positive) : "—"}</td>
                    <td className="font-mono">{pct(x.p_best)}</td>
                    <td className="font-mono">{x.p_beats_first == null ? "—" : pct(x.p_beats_first)}</td>
                    <td className="font-mono">{x.gaps_covered}/{x.gaps_total}</td>
                    <td className="pr-4 text-ink-3">{x.risks.map((r) => t(`launch.risk.${r}.0`)).join(" · ") || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="px-4 py-2 text-[11px] text-ink-3">{c.segment_label}. {t("launch.crn")}</div>
        </div>
      )}
    </Card>
  );
}

function Simulator() {
  const mn = useMarketName();
  const { t, lang } = useI18n();
  const sp = useSearchParams();
  const router = useRouter();
  const markets = useApi<MarketRow[]>("/markets");
  const [f, setF] = useState({ title: sp.get("title") ?? "", specs: "", price: sp.get("price") ?? "", unit_cost: "", fulfilment_fee: "",
    fixed: "", launch_cost: "", rating: "", market: sp.get("market") ?? "", segment_id: sp.get("segment_id") ?? "" });
  // default market: ?market= if given, else the global switcher's market (until the viewer picks one here)
  const [globalMarket] = useMarket();
  const [marketTouched, setMarketTouched] = useState(sp.get("market") != null);
  const market = marketTouched ? f.market : (markets.data ?? []).some((m) => m.name === globalMarket) ? globalMarket : "";
  const [r, setR] = useState<Sim | null>(null);
  const [body, setBody] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const run = async (segment?: string) => {
    setBusy(true); setErr(null);
    const b = { title: f.title, specs: f.specs, price: Number(f.price), unit_cost: n(f.unit_cost), fulfilment_fee: n(f.fulfilment_fee),
      fixed_monthly_cost: n(f.fixed) ?? 0, launch_cost: n(f.launch_cost), rating: n(f.rating), market: market || null,
      segment_id: segment ?? (f.segment_id || null) };
    try { const out = (await apiPost("/launch/simulate", b)) as Sim; setR(out); setBody({ ...b, market: out.market, segment_id: out.placement.segment_id }); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const curve = useMemo<EChartsOption | null>(() => {
    if (!r?.price_curve) return null;
    const pts = r.price_curve.points;
    const k = r.price_curve.optimise;
    const val = (p: CurvePoint) => (k === "profit" ? p.profit! : p.revenue);
    const lo = (p: CurvePoint) => (k === "profit" ? p.profit_lo! : p.revenue_lo);
    const hi = (p: CurvePoint) => (k === "profit" ? p.profit_hi! : p.revenue_hi);
    return {
      grid: { left: 60, right: 20, top: 20, bottom: 36 },
      tooltip: { trigger: "axis" as const, valueFormatter: (v: unknown) => moneyShort(Number(v)) },
      xAxis: { ...axis, type: "value" as const, min: "dataMin", max: "dataMax", axisLabel: { color: "#62789a", formatter: (v: number) => money(v) } },
      yAxis: { ...axis, type: "value" as const, axisLabel: { color: "#62789a", formatter: (v: number) => moneyShort(v) } },
      series: [
        { name: "p10", type: "line" as const, data: pts.map((p) => [p.price, lo(p)]), lineStyle: { opacity: 0 }, symbol: "none", stack: "band", stackStrategy: "all" },
        { name: "p10–p90", type: "line" as const, data: pts.map((p) => [p.price, hi(p) - lo(p)]), lineStyle: { opacity: 0 }, symbol: "none",
          stack: "band", stackStrategy: "all", areaStyle: { color: "rgba(57,135,229,0.18)" }, tooltip: { show: false } },
        { name: t(k === "profit" ? "launch.profitMean" : "launch.revenueMean"), type: "line" as const, data: pts.map((p) => [p.price, val(p)]),
          lineStyle: { width: 2, color: "#3987e5" }, itemStyle: { color: "#3987e5" }, symbolSize: 6,
          markLine: { symbol: "none", lineStyle: { color: "#c98500", type: "dashed" as const }, label: { color: "#c98500", formatter: t("launch.yourPrice") },
            data: [{ xAxis: r.assumptions.price }] } },
      ],
    } as unknown as EChartsOption;
  }, [r, t]);

  return (
    <div className="pb-10">
      <PageHeader title={t("launch.title")} subtitle={t("launch.subtitle")} />
      <div className="grid grid-cols-1 gap-4 px-6 xl:grid-cols-[380px_1fr]">
        <Card className="h-fit">
          <CardHeader title={t("launch.idea")} />
          <div className="space-y-3 p-4 text-sm">
            <label className="block">{t("launch.product")}<Input value={f.title} onChange={set("title")} placeholder={t("launch.titlePlaceholder")} /></label>
            <label className="block">{t("launch.specs")}<Input value={f.specs} onChange={set("specs")} placeholder="28 teeth, soft gums" /></label>
            <div className="grid grid-cols-2 gap-2">
              <label>{t("launch.price")} ($)<Input type="number" value={f.price} onChange={set("price")} /></label>
              <label>{t("launch.unitCost")} ($)<Input type="number" value={f.unit_cost} onChange={set("unit_cost")} placeholder={t("launch.required")} /></label>
              <label>{t("launch.fba")} ($)<Input type="number" value={f.fulfilment_fee} onChange={set("fulfilment_fee")} placeholder={t("launch.auto")} /></label>
              <label>{t("launch.rating")}<Input type="number" value={f.rating} onChange={set("rating")} placeholder={t("launch.auto")} /></label>
              <label>{t("launch.fixed")} ($)<Input type="number" value={f.fixed} onChange={set("fixed")} placeholder="0" /></label>
              <label>{t("launch.launchCost")} ($)<Input type="number" value={f.launch_cost} onChange={set("launch_cost")} placeholder="—" /></label>
            </div>
            <label className="block">{t("launch.market")}
              <Select value={market} onChange={(e) => { setMarketTouched(true); set("market")(e); }} className="w-full">
                <option value="">{t("launch.marketAuto")}</option>
                {(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}
              </Select>
            </label>
            <Button className="w-full" disabled={busy || !f.title || !(Number(f.price) > 0)} onClick={() => run()} aria-busy={busy}>{busy ? "…" : t("launch.run")}</Button>
            <LiveStatus busy={busy} done={!!r} busyText={t("a11y.loading")} doneText={t("a11y.loaded")} />
            {err && <div className="text-xs text-bad">{err}</div>}
            <div className="text-[11px] text-ink-3">{t("launch.costNote")}</div>
          </div>
        </Card>

        {!r ? <Card><Empty title={t("launch.emptyTitle")}>{t("launch.emptySub")}</Empty></Card> : (
          <div className="space-y-4">
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3 text-sm">
                <div>{t("launch.placedIn")} <b>{mn(r.market)}</b> · <b>{r.placement.segment_label}</b>
                  <span className="text-ink-3"> · {r.placement.user_chosen ? t("launch.youChose") : t("launch.vote", { v: pct(r.placement.vote_share ?? 0) })} · {t("launch.similarity", { s: r.placement.top_similarity.toFixed(2) })}</span></div>
                <div className="flex items-center gap-2">
                  <Select value={r.placement.segment_id} onChange={(e) => run(e.target.value)} className="w-56 text-xs">
                    {r.placement.alternatives.some((a) => a.segment_id === r.placement.segment_id) ? null : <option value={r.placement.segment_id}>{r.placement.segment_label}</option>}
                    {r.placement.alternatives.map((a) => <option key={a.segment_id} value={a.segment_id}>{truncate(a.segment_label, 40)} ({pct(a.vote_share)})</option>)}
                  </Select>
                  <Button size="sm" variant="outline" onClick={async () => {
                    const pj = await post<{ id: string }>("/projects", { title: f.title, market: r.market, segment_id: r.placement.segment_id,
                      idea: { title: f.title, specs: f.specs, price: Number(f.price), unit_cost: n(f.unit_cost) } });
                    router.push(`/projects?selected=${pj.id}`);
                  }}>{t("launch.project")} →</Button>
                </div>
              </div>
              <div className="grid grid-cols-2 divide-x divide-line md:grid-cols-4">
                <div className="p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("launch.unitsMedian")}</div>
                  <div className="mt-1 font-mono text-xl">{num(r.units.median, 1)}</div><Range d={r.units} /><ModelFlag market={r.market} className="text-[11px]" /></div>
                <div className="p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("launch.revenueMedian")}</div>
                  <div className="mt-1 font-mono text-xl">{moneyShort(r.revenue.median)}</div><Range d={r.revenue} money /><ModelFlag market={r.market} className="text-[11px]" /></div>
                <div className="p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{r.profit ? t("launch.profitMean") : t("launch.profit")}</div>
                  {r.profit ? <><div className={`mt-1 font-mono text-xl ${r.profit.mean < 0 ? "text-bad" : ""}`}>{moneyShort(r.profit.mean)}</div><Range d={r.profit} money /></>
                    : <div className="mt-1 text-xs text-warn">{t("launch.needCost")}</div>}</div>
                <div className="p-4"><div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{r.profit ? t("launch.pProfit") : t("launch.pRevenueTarget", { v: moneyShort(r.revenue.target) })}</div>
                  <div className="mt-1 font-mono text-xl">{pct(r.profit ? r.profit.p_positive : r.revenue.p_target)}</div>
                  <div className="text-[11px] text-ink-3">{r.profit ? t("launch.pTarget", { v: moneyShort(r.profit.target), p: pct(r.profit.p_target) }) : ""}</div></div>
              </div>
              <div className="grid grid-cols-1 gap-x-6 gap-y-1 border-t border-line px-4 py-3 text-xs text-ink-2 md:grid-cols-2">
                <div>{t("launch.margin")}: <b className="font-mono">{r.economics.unit_margin == null ? "—" : `${money(r.economics.unit_margin, 2)} (${pct(r.economics.margin_rate ?? 0)})`}</b>
                  <span className="text-ink-3"> = {money(r.assumptions.price, 2)} × (1 − {pct(r.economics.referral_fee)}) − {money(r.economics.fulfilment_fee, 2)} − {r.economics.unit_cost == null ? "?" : money(r.economics.unit_cost, 2)}</span></div>
                <div>{t("launch.breakEven")}: <b className="font-mono">{r.break_even_units == null ? "—" : `${num(r.break_even_units, 1)} ${t("common.units")}/mo`}</b>
                  {r.p_break_even != null && <span className="text-ink-3"> · P = {pct(r.p_break_even)}</span>}
                  {r.payback_months != null && <span className="text-ink-3"> · {t("launch.payback", { m: r.payback_months.toFixed(1) })}</span>}</div>
                <div className="text-ink-3">{t("launch.fbaBasis")}: {r.economics.fulfilment_basis}</div>
                <div className="text-ink-3">{t("launch.costBasis")}: {r.economics.unit_cost_basis}</div>
                <div className="text-ink-3">{t("launch.assumed", { r: r.assumptions.rating?.toFixed(1) ?? "—", rb: r.assumptions.rating_basis, a: r.assumptions.age_days })}</div>
                <div className="text-ink-3">{t("launch.model", { f: r.assumptions.model_family, n: num(r.assumptions.model_listings), s: num(r.assumptions.simulations), o: r.assumptions.segment_offset.toFixed(2), k: r.assumptions.segment_offset_n })}</div>
              </div>
            </Card>

            <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[1.4fr_1fr]">
              <Card>
                <CardHeader title={t(r.price_curve?.optimise === "profit" ? "launch.curveProfit" : "launch.curveRevenue")}
                  subtitle={r.price_curve ? t("launch.curveSub", { p: money(r.price_curve.best_price, 2) }) : ""} />
                {curve ? <EChart option={curve} height={300} /> : <Empty title={t("common.notAvailable")} />}
                {r.price_curve && <div className="px-4 pb-3 text-[11px] text-warn">{r.price_curve.at_edge && <>{t("launch.atEdge")} </>}{t("launch.curveCaveat")}</div>}
              </Card>
              <Card>
                <CardHeader title={t("launch.risks")} subtitle={t("launch.risksSub")} />
                {r.risks.length === 0 ? <Empty title={t("launch.noRisk")} /> : (
                  <div className="divide-y divide-line">
                    {r.risks.map((k, i) => (
                      <div key={i} className="px-4 py-2 text-xs">
                        <div className="flex items-center gap-2"><Badge color={SEV[k.severity]}>{t(`launch.sev.${k.severity}`)}</Badge><b>{t(`launch.risk.${k.code}.0`)}</b></div>
                        <div className="mt-0.5 text-ink-3">{t(`launch.risk.${k.code}.1`, { v: typeof k.value === "number" ? (Math.abs(k.value) < 1 ? k.value.toFixed(2) : num(k.value, 1)) : k.value,
                          bar: k.bar?.toFixed(1) ?? "", brand: k.brand ?? "", lo: k.range ? num(k.range[0], 1) : "", hi: k.range ? num(k.range[1], 1) : "" })}</div>
                      </div>
                    ))}
                  </div>
                )}
                <div className="border-t border-line px-4 py-3 text-xs">
                  <div className="font-medium">{t("launch.entrantsTitle")}</div>
                  <div className="text-ink-3">{r.entrants_actual.n ? t("launch.entrantsLine", { n: r.entrants_actual.n, m: num(r.entrants_actual.median ?? 0, 1),
                    a: num(r.entrants_actual.p25 ?? 0, 1), b: num(r.entrants_actual.p75 ?? 0, 1), me: num(r.units.median, 1) }) : t("launch.noEntrants")}</div>
                </div>
              </Card>
            </div>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <Card>
                <CardHeader title={t("launch.gapsTitle")} subtitle={t("launch.gapsSub")} />
                {r.gaps.length === 0 ? <Empty title={t("launch.noGaps")} /> : (
                  <div className="divide-y divide-line">
                    {r.gaps.map((g) => (
                      <div key={g.feature} className="flex items-center justify-between gap-2 px-4 py-2 text-xs">
                        <span><Badge color={g.covered ? "var(--good)" : "var(--ink-3)"}>{g.covered ? t("launch.covered") : t("launch.missing")}</Badge> <b className="ml-1">{g.feature}</b></span>
                        <span className="font-mono text-ink-3">{t("common.lift")} {g.lift.toFixed(2)} ({g.lift_lo.toFixed(2)}–{g.lift_hi.toFixed(2)}) · q {g.q_value.toFixed(3)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {r.recommendation && <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">{t("launch.recLine", { f: r.recommendation.features.join(", ") || "—", p: r.recommendation.price_range })}</div>}
                <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">{lang === "zh" ? r.segment.segment_description_zh ?? r.segment.segment_description : r.segment.segment_description}</div>
              </Card>
              <Card>
                <CardHeader title={t("launch.comparables")} subtitle={t("launch.comparablesSub")} />
                <div className="max-h-[360px] overflow-auto scrollbar-thin" tabIndex={0}>
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-panel text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-1.5">{t("launch.product")}</th><th scope="col">{t("launch.price")}</th><th scope="col">{t("common.units")}/mo (95%)</th><th scope="col" className="pr-4">sim</th></tr></thead>
                    <tbody className="divide-y divide-line">
                      {r.comparables.map((c) => (
                        <tr key={c.id}>
                          <td className="max-w-[200px] px-4 py-1.5"><div className="truncate">{c.title}</div><div className="text-ink-3">{c.brand ?? "—"}{c.is_entrant ? ` · ${t("launch.entrant")}` : ""}</div></td>
                          <td className="font-mono">{money(c.price, 2)}</td>
                          <td className="whitespace-nowrap font-mono">{num(c.units_est, 1)} <span className="text-ink-3">{num(c.units_lo, 0)}–{num(c.units_hi, 0)}</span></td>
                          <td className="pr-4 font-mono">{c.similarity.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="border-t border-line px-4 py-2 text-[11px]"><Link className="text-accent" href={`/markets/${encodeURIComponent(r.market)}`}>{t("nav.markets")} →</Link></div>
              </Card>
            </div>

            {body && <Scenarios key={JSON.stringify(body)} body={body} />}
          </div>
        )}
      </div>
    </div>
  );
}

export default function LaunchPage() {
  return <Suspense><Simulator /></Suspense>;
}
