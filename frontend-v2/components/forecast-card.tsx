"use client";
import type { EChartsOption } from "echarts";
import { axis, EChart } from "@/components/charts/echart";
import { Badge, Card, CardHeader } from "@/components/ui/primitives";
import { moneyShort, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Fit = {
  status: "ok" | "needs_snapshots"; periods: number; min_periods?: number;
  growth_per_month?: number; growth_lo?: number; growth_hi?: number; growth_12m?: number; p_value?: number;
  direction?: "growing" | "declining" | "no_significant_trend"; tau?: number; method?: string;
  forecast?: { months_ahead: number; period: string; estimate: number; low: number; high: number; beyond_span?: boolean }[];
  span_months?: number; fitted?: { period: string; observed: number; observed_lo: number; observed_hi: number; fitted: number }[];
};
type SegFit = { label: string; segment_id: string; periods: number; growth_per_month: number; growth_lo: number; growth_hi: number;
  p_value: number; direction: Fit["direction"] };
export type ForecastV3 = { market: Fit; segments: SegFit[] };

const DIR_TONE: Record<string, string> = { growing: "var(--good)", declining: "var(--bad)", no_significant_trend: "var(--ink-3)" };
const signed = (v: number | undefined, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);

/** Growth & forecast on metrics v3: observed revenue per snapshot with its interval, the fitted trend, and
 *  forecasts with prediction intervals. Below the snapshot minimum it shows ``fallback`` instead. */
export function ForecastCard({ market, fallback }: { market: string; fallback: React.ReactNode }) {
  const { t } = useI18n();
  const d = useApi<ForecastV3>(`/markets/${encodeURIComponent(market)}/forecast-v3`, [market]);
  const f = d.data?.market;
  const ok = f?.status === "ok";

  let option: EChartsOption | null = null;
  if (ok && f.fitted && f.forecast) {
    const obs = f.fitted.map((r) => [r.period, r.observed, r.observed_lo, r.observed_hi]);
    const last = f.fitted.at(-1)!;
    const fc = [{ period: last.period, estimate: last.fitted, low: last.fitted, high: last.fitted }, ...f.forecast];
    option = {
      grid: { left: 60, right: 20, top: 24, bottom: 36 },
      legend: { top: 0, right: 0, data: [t("fc.observed"), t("fc.trend"), t("fc.forecast"), t("fc.band")], textStyle: { color: "#9fb3cf", fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
      tooltip: { trigger: "axis", valueFormatter: (v: unknown) => moneyShort(Number(v)) },
      xAxis: { ...axis, type: "time" },
      yAxis: { ...axis, type: "log", axisLabel: { color: "#62789a", formatter: (v: number) => moneyShort(v) } },
      series: [
        { name: "lo", type: "line", data: fc.map((r) => [r.period, r.low]), lineStyle: { opacity: 0 }, symbol: "none",
          stack: "band", stackStrategy: "all", tooltip: { show: false } },
        { name: t("fc.band"), type: "line", data: fc.map((r) => [r.period, r.high - r.low]), lineStyle: { opacity: 0 }, symbol: "none",
          stack: "band", stackStrategy: "all", areaStyle: { color: "rgba(57,135,229,0.18)" }, itemStyle: { color: "rgba(57,135,229,0.4)" },
          tooltip: { show: false } },
        { name: t("fc.trend"), type: "line", data: f.fitted.map((r) => [r.period, r.fitted]), symbol: "none",
          lineStyle: { width: 2, color: "#3987e5" }, itemStyle: { color: "#3987e5" } },
        { name: t("fc.forecast"), type: "line", data: fc.map((r) => [r.period, r.estimate]), symbolSize: 6,
          lineStyle: { width: 2, type: "dashed", color: "#3987e5" }, itemStyle: { color: "#3987e5" } },
        { name: t("fc.observed"), type: "custom", data: obs, itemStyle: { color: "#c98500" },
          encode: { x: 0, y: [1, 2, 3], tooltip: [1] },
          renderItem: (_: unknown, api: { value: (i: number) => number; coord: (p: [number, number]) => number[] }) => {
            const x = api.value(0);
            const [cx, cy] = api.coord([x, api.value(1)]);
            const lo = api.coord([x, api.value(2)])[1];
            const hi = api.coord([x, api.value(3)])[1];
            return { type: "group", children: [
              { type: "line", shape: { x1: cx, y1: lo, x2: cx, y2: hi }, style: { stroke: "#c98500", lineWidth: 2 } },
              { type: "line", shape: { x1: cx - 4, y1: lo, x2: cx + 4, y2: lo }, style: { stroke: "#c98500", lineWidth: 2 } },
              { type: "line", shape: { x1: cx - 4, y1: hi, x2: cx + 4, y2: hi }, style: { stroke: "#c98500", lineWidth: 2 } },
              { type: "circle", shape: { cx, cy, r: 4 }, style: { fill: "#c98500", stroke: "#0a1220", lineWidth: 2 } },
            ] };
          } },
      ],
    } as unknown as EChartsOption;
  }

  return (
    <Card>
      <CardHeader title={t("market.forecast")} subtitle={ok ? t("fc.sub", { n: f.periods }) : undefined}
        right={ok && f.direction ? <Badge color={DIR_TONE[f.direction]}>{t(`fc.dir.${f.direction}`)}</Badge> : undefined} />
      {!ok ? <div className="p-4 text-sm text-ink-2">{fallback}</div> : (
        <div className="pb-3">
          <div className="grid grid-cols-3 divide-x divide-line border-b border-line text-center">
            <div className="p-2"><div className="font-mono text-lg">{signed(f.growth_per_month)}</div>
              <div className="text-[11px] text-ink-3">{t("fc.perMonth")} · {signed(f.growth_lo)} – {signed(f.growth_hi)}</div></div>
            <div className="p-2"><div className="font-mono text-lg">{signed(f.growth_12m, 0)}</div>
              <div className="text-[11px] text-ink-3">{t("fc.perYear")}</div></div>
            <div className="p-2"><div className="font-mono text-lg">{f.p_value == null ? "—" : f.p_value < 0.001 ? "< 0.001" : f.p_value.toFixed(3)}</div>
              <div className="text-[11px] text-ink-3">{t("fc.pValue")}</div></div>
          </div>
          {option && <EChart option={option} height={240} />}
          <div className="grid grid-cols-3 gap-2 px-4 text-xs">
            {(f.forecast ?? []).map((x) => (
              <div key={x.months_ahead} className="rounded-lg border border-line p-2">
                <div className="text-ink-3">{t("fc.ahead", { n: x.months_ahead })} · {x.period.slice(0, 7)}
                  {x.beyond_span && <span className="ml-1 text-warn" title={t("fc.beyondTip")}>⚠</span>}</div>
                <div className="font-mono">{moneyShort(x.estimate)}</div>
                <div className="text-[11px] text-ink-3">{moneyShort(x.low)}–{moneyShort(x.high)}</div>
              </div>
            ))}
          </div>
          {!!d.data?.segments.length && (
            <div className="mt-3 px-4">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-3">{t("fc.segments")}</div>
              <div className="divide-y divide-line text-xs">
                {d.data.segments.slice(0, 8).map((s) => (
                  <div key={s.label} className="flex items-center gap-2 py-1.5">
                    <span className="min-w-0 flex-1 truncate">{truncate(s.label, 48)}</span>
                    <span className="font-mono">{signed(s.growth_per_month)}</span>
                    <span className="w-28 text-right text-[11px] text-ink-3">{signed(s.growth_lo)} – {signed(s.growth_hi)}</span>
                    <span className="w-24 text-right" style={{ color: DIR_TONE[s.direction ?? "no_significant_trend"] }}>{t(`fc.dir.${s.direction}`)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {f.forecast?.some((x) => x.beyond_span) && (
            <p className="mt-3 px-4 text-[11px] text-warn">⚠ {t("fc.beyond", { m: (f.span_months ?? 0).toFixed(0) })}</p>)}
          <p className="mt-2 px-4 text-[11px] text-ink-3">{t("fc.note", { tau: pct(f.tau ?? 0, 1) })}</p>
        </div>
      )}
    </Card>
  );
}
