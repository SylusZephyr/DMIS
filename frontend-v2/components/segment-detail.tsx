"use client";
import type { EChartsOption } from "echarts";
import { axis, EChart } from "@/components/charts/echart";
import { money, num, pct } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

export type SegBrand = { brand: string; share_est: number; share_lo: number; share_hi: number; revenue_est: number; p_top: number };
export type PriceBand = { band: number; bands: number; listings: number; price_min: number; price_max: number; price_median: number;
  units_mean: number; units_lo: number; units_hi: number; revenue_share: number | null; revenue_share_lo: number | null; revenue_share_hi: number | null };
export type Momentum = { recent: number; previous: number; rate_ratio: number; p_value: number | null; q_value: number | null;
  direction: "accelerating" | "slowing" | "no_significant_change"; significant: boolean };

const MOM_TONE = { accelerating: "var(--good)", slowing: "var(--bad)", no_significant_change: "var(--ink-3)" };

/** Inside one sub-category: who holds the revenue (share intervals, P(#1)), how demand per listing changes
 *  across price bands (bootstrap intervals), and whether launches are speeding up (exact binomial test). */
export function SegmentDetail({ brands, bands, momentum }: { brands: SegBrand[]; bands: PriceBand[]; momentum: Momentum | null }) {
  const { t } = useI18n();
  const maxShare = Math.max(0.0001, ...brands.map((b) => b.share_hi));
  const option: EChartsOption = {
    grid: { left: 48, right: 12, top: 16, bottom: 40 },
    tooltip: { trigger: "item", formatter: (p: unknown) => {
      const b = bands[(p as { dataIndex: number }).dataIndex];
      return `${money(b.price_min, 2)}–${money(b.price_max, 2)} · ${b.listings} ${t("common.listings")}<br/>`
        + `${t("seg.unitsPerListing")}: ${num(b.units_mean, 0)} (${num(b.units_lo, 0)}–${num(b.units_hi, 0)})<br/>`
        + `${t("seg.revShare")}: ${b.revenue_share == null ? "—" : pct(b.revenue_share, 1)}`
        + (b.revenue_share_lo == null ? "" : ` (${pct(b.revenue_share_lo, 1)}–${pct(b.revenue_share_hi ?? 0, 1)})`);
    } },
    xAxis: { ...axis, type: "category", data: bands.map((b) => `${money(b.price_min, 0)}–${money(b.price_max, 0)}`),
      axisLabel: { color: "#62789a", fontSize: 10, interval: 0 } },
    yAxis: { ...axis, type: "value", name: t("seg.unitsPerListing"), nameTextStyle: { color: "#62789a", fontSize: 10, align: "left" } },
    series: [
      { type: "bar", barWidth: "45%", data: bands.map((b) => b.units_mean),
        itemStyle: { color: "#3987e5", borderRadius: [4, 4, 0, 0] } },
      { type: "custom", data: bands.map((b, i) => [i, b.units_lo, b.units_hi]), z: 3, tooltip: { show: false },
        renderItem: (_: unknown, api: { value: (i: number) => number; coord: (p: [number, number]) => number[] }) => {
          const [x, lo] = api.coord([api.value(0), api.value(1)]);
          const hi = api.coord([api.value(0), api.value(2)])[1];
          const st = { stroke: "#9fb3cf", lineWidth: 1.5 };
          return { type: "group", children: [
            { type: "line", shape: { x1: x, y1: lo, x2: x, y2: hi }, style: st },
            { type: "line", shape: { x1: x - 4, y1: lo, x2: x + 4, y2: lo }, style: st },
            { type: "line", shape: { x1: x - 4, y1: hi, x2: x + 4, y2: hi }, style: st },
          ] };
        } },
    ],
  } as unknown as EChartsOption;

  return (
    <div className="grid grid-cols-1 gap-4 lg:col-span-2 lg:grid-cols-3">
      <div>
        <div className="mb-1 text-[11px] uppercase text-ink-3">{t("seg.brands")}</div>
        {!brands.length ? <div className="text-xs text-ink-3">{t("common.notAvailable")}</div> : (
          <div className="space-y-1.5">
            {brands.map((b) => (
              <div key={b.brand} className="text-xs" title={t("seg.brandTip", { lo: pct(b.share_lo, 1), hi: pct(b.share_hi, 1), p: pct(b.p_top, 0) })}>
                <div className="flex justify-between gap-2"><span className="truncate">{b.brand}</span>
                  <span className="font-mono">{pct(b.share_est, 1)} <span className="text-ink-3">· P(#1) {pct(b.p_top, 0)}</span></span></div>
                <div className="relative mt-0.5 h-1.5 rounded bg-line">
                  <div className="absolute inset-y-0 left-0 rounded bg-[#3987e5]" style={{ width: `${(b.share_est / maxShare) * 100}%` }} />
                  <div className="absolute -top-0.5 h-2.5 border-x border-ink-2"
                    style={{ left: `${(b.share_lo / maxShare) * 100}%`, width: `${Math.max(0.5, ((b.share_hi - b.share_lo) / maxShare) * 100)}%` }} />
                </div>
              </div>
            ))}
            <div className="text-[10px] text-ink-3">{t("seg.brandsNote")}</div>
          </div>
        )}
      </div>
      <div className="lg:col-span-1">
        <div className="mb-1 text-[11px] uppercase text-ink-3">{t("seg.bands")}</div>
        {bands.length < 2 ? <div className="text-xs text-ink-3">{t("seg.oneBand")}</div> : <EChart option={option} height={190} />}
        <div className="text-[10px] text-ink-3">{t("seg.bandsNote")}</div>
      </div>
      <div>
        <div className="mb-1 text-[11px] uppercase text-ink-3">{t("seg.momentum")}</div>
        {!momentum ? <div className="text-xs text-ink-3">{t("common.notAvailable")}</div> : (
          <div className="text-xs">
            <div className="flex items-baseline gap-3">
              <div><div className="font-mono text-lg">{momentum.recent}</div><div className="text-[10px] text-ink-3">{t("seg.recent")}</div></div>
              <div className="text-ink-3">vs</div>
              <div><div className="font-mono text-lg">{momentum.previous}</div><div className="text-[10px] text-ink-3">{t("seg.previous")}</div></div>
            </div>
            <div className="mt-1" style={{ color: MOM_TONE[momentum.direction] }}>{t(`seg.dir.${momentum.direction}`)}</div>
            <div className="text-[10px] text-ink-3">
              {momentum.q_value == null ? t("seg.tooFew") : t("seg.test", { r: momentum.rate_ratio.toFixed(2), q: momentum.q_value.toFixed(3) })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
