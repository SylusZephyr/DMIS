"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { EChartsOption } from "echarts";
import { Rocket } from "lucide-react";
import { axis, EChart } from "@/components/charts/echart";
import { Card, CardHeader } from "@/components/ui/primitives";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

type Pm = { value: number; percentile: number; median: number; n: number };
type Pt = { product_id: string; title: string; price: number | null; units_est: number | null; revenue_est: number | null };
export type Position = { peers: number; metrics: Partial<Record<"price" | "units_est" | "revenue_est" | "rating" | "reviews", Pm>>;
  revenue_share?: number | null; points: Pt[] };

const FMT: Record<string, (v: number) => string> = {
  price: (v) => money(v, 2), units_est: (v) => num(v, 0), revenue_est: (v) => moneyShort(v), rating: (v) => v.toFixed(2), reviews: (v) => num(v, 0),
};

/** The product among the products of its sub-category: percentile per metric (mid-rank) against the
 *  sub-category median, and a price x demand scatter with this product highlighted. */
export function PositionCard({ pos, productId, launchHref }: { pos: Position; productId: string; launchHref: string }) {
  const { t } = useI18n();
  const router = useRouter();
  const pts = pos.points.filter((p) => p.price != null && p.units_est != null && p.units_est > 0);
  const me = pts.find((p) => p.product_id === productId);
  const others = pts.filter((p) => p.product_id !== productId);
  const option: EChartsOption = {
    grid: { left: 52, right: 16, top: 12, bottom: 40 },
    tooltip: { trigger: "item", formatter: (p: unknown) => {
      const r = (p as { data: { raw: Pt } }).data.raw;
      return `${truncate(r.title, 60)}<br/>${money(r.price, 2)} · ${num(r.units_est, 0)} ${t("common.units")}/mo`;
    } },
    xAxis: { ...axis, type: "log", min: (v: { min: number }) => v.min / 1.25, max: (v: { max: number }) => v.max * 1.25, name: t("posn.price"), nameLocation: "middle", nameGap: 24, axisLabel: { color: "#62789a", hideOverlap: true, formatter: (v: number) => money(v, 0) } },
    yAxis: { ...axis, type: "log", min: (v: { min: number }) => v.min / 1.5, max: (v: { max: number }) => v.max * 1.5, name: t("posn.units"), nameTextStyle: { color: "#62789a", fontSize: 10, align: "left" },
      axisLabel: { color: "#62789a", hideOverlap: true, formatter: (v: number) => num(v, 0) } },
    series: [
      { type: "scatter", symbolSize: 7, itemStyle: { color: "#5b6b82", opacity: 0.7 },
        data: others.map((r) => ({ value: [r.price, r.units_est], raw: r })) },
      ...(me ? [{ type: "scatter" as const, symbolSize: 14, z: 5, itemStyle: { color: "#c98500", borderColor: "#0a1220", borderWidth: 2 },
        data: [{ value: [me.price, me.units_est], raw: me }] }] : []),
    ],
  } as unknown as EChartsOption;

  return (
    <Card>
      <CardHeader title={t("posn.title")} subtitle={t("posn.sub", { n: pos.peers })}
        right={<Link href={launchHref} className="inline-flex items-center gap-1.5 rounded-lg border border-accent/50 px-3 py-1.5 text-xs text-accent hover:bg-accent/10">
          <Rocket className="h-3.5 w-3.5" />{t("posn.launch")}</Link>} />
      <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-[1fr_1.3fr]">
        <div className="space-y-2.5">
          {(["price", "units_est", "revenue_est", "rating", "reviews"] as const).map((k) => {
            const m = pos.metrics[k];
            if (!m) return null;
            return (
              <div key={k} className="text-xs">
                <div className="flex justify-between gap-2">
                  <span className="text-ink-2">{t(`posn.m.${k}`)}</span>
                  <span className="font-mono">{FMT[k](m.value)} <span className="text-ink-3">· {t("posn.median")} {FMT[k](m.median)}</span></span>
                </div>
                <div className="relative mt-1 h-1.5 rounded bg-line">
                  <div className="absolute inset-y-0 left-1/2 w-px bg-ink-3" />
                  <div className="absolute -top-1 h-3.5 w-1.5 -translate-x-1/2 rounded-sm bg-[#c98500]" style={{ left: `${m.percentile * 100}%` }} />
                </div>
                <div className="mt-0.5 text-[10px] text-ink-3">{t("posn.pctile", { p: Math.round(m.percentile * 100), n: m.n })}</div>
              </div>
            );
          })}
          {pos.revenue_share != null && <div className="border-t border-line pt-2 text-xs text-ink-2">{t("posn.share", { s: pct(pos.revenue_share, 1) })}</div>}
        </div>
        <div>
          {pts.length < 2 ? <div className="text-xs text-ink-3">{t("common.notAvailable")}</div>
            : <EChart option={option} height={230} onEvents={{ click: (p) => {
              const r = (p as { data?: { raw?: Pt } }).data?.raw; if (r && r.product_id !== productId) router.push(`/products/${r.product_id}`); } }} />}
          <div className="text-[10px] text-ink-3">{t("posn.chartNote")}</div>
        </div>
      </div>
    </Card>
  );
}
