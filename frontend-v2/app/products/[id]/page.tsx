"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ImageOff } from "lucide-react";
import { Badge, Card, CardHeader, Empty, Meter, Stat } from "@/components/ui/primitives";
import { ErrorNote } from "@/components/page";
import type { Product, Reason } from "@/lib/api";
import { confidenceColor, opportunityColor } from "@/lib/colors";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { basisZh } from "@/lib/basis-zh";
import { ComponentBar, Explain, fmt, type MetricRow } from "@/components/metric";
import { type Position, PositionCard } from "@/components/product-position";
import { useMarketName } from "@/lib/market-name";
import { ModelFlag } from "@/components/v2/market-size";
import { LiveReviews, WatchButton } from "@/components/v2/live-data";
import { TranslateText } from "@/components/v2/translate";

type Listing = { id: string; title: string; price: number | null; sales: number | null; revenue: number | null; rating: number | null;
  url: string | null; is_best_listing: boolean; data_confidence: number; relevance_score: number };
type Complaint = { aspect: string; mentions: number; share_of_reviews: number; opportunity: string; example: string };
type Pain = { status: string; reviews: number; complaints: Complaint[]; advantages: { aspect: string; mentions: number }[];
  missing_features: { feature: string; mentions: number }[] };
type Detail = {
  market: string;
  product: Product & { attributes: Record<string, unknown>; listing_ids: string[]; model_label: string; url: string | null; sales_coverage: number; best_listing_basis: string;
    variant_label?: string; model_basis?: string; confidence_score?: number | null; confidence_level?: string;
    confidence_components?: Record<string, number | null>; confidence_reasons?: Reason[]; trend_label?: string | null };
  listings: Listing[]; segment: { segment_label: string; opportunity_score: number; opportunity_level: string; products: number; monthly_revenue: number | null; concentration: string };
  competitors: Product[]; suppliers: { id: string; name: string; country: string | null; score: number | null; match_score: number; oem: boolean; odm: boolean; certifications: string | null }[];
  customer_pain: { product?: Pain; segment?: Pain };
  opportunity: { score: number | null; segment_score: number | null; level: string; coverage: number; drivers: string; factors: Record<string, number | null> };
  similar: { product_id: string; title: string; brand: string | null; price: number | null; score: number; market: string }[];
};


type ExplainListing = { id: string; title: string; price: number | null; rating: number | null; sales: number | null; sales_observation: string;
  units_floor: number; units_est: number; units_lo: number; units_hi: number; units_ceiling: number | null; revenue_est: number;
  unit_cost: number | null; unit_cost_issue: string | null; fulfilment_fee: number | null; unit_margin: number | null; margin_rate: number | null;
  is_entrant: boolean; age_days: number | null; price_band: number | null; price_band_lift: number | null };
type ProductExplain = { product: Record<string, number | string | null>; listings: ExplainListing[]; segment_metrics: MetricRow[]; position?: Position;
  components: Record<"segment" | "price_band" | "quality_room", { weight: number; value: number | null }> };

/** The listing's image; a blocked or missing image shows a quiet placeholder instead of an empty white box. */
function ProductImage({ src, alt, noImage }: { src: string | null | undefined; alt: string; noImage: string }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return (
    <div className="flex h-40 flex-col items-center justify-center gap-2 bg-panel-2 text-ink-3">
      <ImageOff className="h-8 w-8" /><span className="text-xs">{noImage}</span>
    </div>
  );
  return (
    <div className="flex h-64 items-center justify-center bg-white">
      <img src={src} alt={alt} className="max-h-60 object-contain" onError={() => setFailed(true)} />
    </div>
  );
}

export default function ProductDetail() {
  const mn = useMarketName();
  const id = useParams<{ id: string }>().id;
  const { t, lang } = useI18n();
  const { data: d, error } = useApi<Detail>(`/products/${id}`);
  const v3 = useApi<ProductExplain>(`/products/${id}/explain`);
  const eng = useApi<{ rows: { scope: string; scope_id: string; opportunity_score: number | null }[] }>(
    d?.market ? `/markets/${encodeURIComponent(d.market)}/opportunities/explained` : null, [d?.market]);
  const segEngine = eng.data?.rows.find((r) => r.scope === "segment" && String(r.scope_id) === String(d?.product.segment_id));
  if (error) return <div className="pt-6"><ErrorNote error={error} /></div>;
  if (!d) return <div className="p-6 text-sm text-ink-3">{t("common.loading")}</div>;
  const p = d.product;
  const pain = d.customer_pain.product ?? d.customer_pain.segment;
  return (
    <div className="space-y-4 p-6">
      <div className="text-xs text-ink-3">
        <Link className="hover:text-accent" href={`/markets/${encodeURIComponent(d.market)}`}>{mn(d.market)}</Link> / {d.segment.segment_label}
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_1fr]">
        <Card className="overflow-hidden">
          <ProductImage src={p.image} alt={p.title} noImage={t("product.noImage")} />
          <div className="space-y-2 p-4">
            <h1 className="text-base font-semibold leading-snug">{p.title}</h1>
            <TranslateText key={`${p.product_id}-${lang}`} text={p.title} refId={p.product_id} />
            <div className="flex flex-wrap gap-1.5">
              {p.brand && <Badge>{p.brand}</Badge>}
              {p.model_label && p.model_label !== "other" && <Badge color="var(--accent-2)">{p.model_label}</Badge>}
              {p.price_tier && <Badge>{t("product.tier", { v: p.price_tier })}</Badge>}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              {p.url && <a href={p.url} target="_blank" className="text-xs text-accent hover:underline">{t("product.bestListing", { id: p.best_listing ?? "" })} ↗</a>}
              <WatchButton asin={p.best_listing} market={d.market} label={p.title} />
            </div>
          </div>
        </Card>
        <div className="space-y-4">
          <Card className="grid grid-cols-2 divide-x divide-line md:grid-cols-3 xl:grid-cols-6">
            <Stat label={t("product.price")} value={money(p.price, 2)} sub={p.price_min !== p.price_max ? `${money(p.price_min, 2)} – ${money(p.price_max, 2)}` : undefined} />
            <Stat label={t("product.units")} value={num(v3.data?.product.units_est as number)}
              sub={<>{`${t("common.interval")} ${num(v3.data?.product.units_lo as number)}–${num(v3.data?.product.units_hi as number)} · ${t("common.floor")} ${num(v3.data?.product.units_floor as number)}`}<ModelFlag market={d.market} /></>} />
            <Stat label={t("product.revenue")} value={moneyShort(v3.data?.product.revenue_est as number)}
              sub={<>{`${moneyShort(v3.data?.product.revenue_lo as number)}–${moneyShort(v3.data?.product.revenue_hi as number)}`}<ModelFlag market={d.market} /></>} />
            <Stat label={t("product.rating")} value={p.rating?.toFixed(2) ?? "—"} sub={t("product.reviews", { n: num(p.reviews) })} />
            <Stat label={t("product.listings")} value={num(p.listing_count)} sub={t("product.bestBy", { b: t(`product.basis.${p.best_listing_basis}`).startsWith("product.") ? p.best_listing_basis : t(`product.basis.${p.best_listing_basis}`) })} />
            <Stat label={t("metric.opportunity_index.0")} value={<span style={{ color: opportunityColor(p.opportunity_score) }}>{p.opportunity_score?.toFixed(0) ?? "—"}</span>}
              sub={<span title={`${t("product.segScoreTip")} · ${t("product.segmentIndex")} ${d.opportunity.segment_score?.toFixed(0) ?? "—"}`}>{t("product.segScore")} {segEngine?.opportunity_score?.toFixed(0) ?? "—"}</span>} />
          </Card>
          {v3.data?.position && <PositionCard pos={v3.data.position} productId={p.product_id}
            launchHref={`/launch?${new URLSearchParams({ title: p.title, price: String(p.price ?? ""), market: d.market, segment_id: p.segment_id ?? "" })}`} />}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {d.product.confidence_score != null && (
              <Card className="lg:col-span-2">
                <CardHeader title={t("product.confidence", { v: d.product.confidence_score.toFixed(0) })}
                  subtitle={t("product.confidenceSub", { l: t(`product.level.${d.product.confidence_level}`).startsWith("product.") ? d.product.confidence_level ?? "" : t(`product.level.${d.product.confidence_level}`) })} />
                <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-[220px_1fr]">
                  <div className="space-y-2 text-xs">
                    {Object.entries(d.product.confidence_components ?? {}).map(([k, v]) => (
                      <div key={k}><div className="flex justify-between"><span>{t(`product.cc.${k}`).startsWith("product.") ? k.replace(/_/g, " ") : t(`product.cc.${k}`)}</span><span className="font-mono">{v == null ? "n/a" : v.toFixed(0)}</span></div>
                        <Meter value={v ?? 0} max={100} color={confidenceColor(d.product.confidence_level)} /></div>
                    ))}
                  </div>
                  <ul className="space-y-1 text-sm">
                    {(d.product.confidence_reasons ?? []).map((r) => (
                      <li key={r.signal} className="flex gap-2"><span style={{ color: r.ok ? "var(--good)" : "var(--ink-3)" }}>{r.ok ? "✓" : "✗"}</span><span className={r.ok ? "" : "text-ink-3"}>{lang === "zh" ? basisZh(r.detail) : r.detail}</span></li>
                    ))}
                  </ul>
                  {d.product.variant_label && <div className="text-xs text-ink-3 md:col-span-2">{t("product.hierarchy")}: {d.segment.segment_label} → {t("product.model")} <b className="text-ink">{d.product.model_label}</b>{d.product.model_basis ? ` (${d.product.model_basis})` : ""} → {t("product.variant")} <b className="text-ink">{d.product.variant_label}</b> → {d.listings.length} {t("common.listings")}</div>}
                </div>
              </Card>
            )}
            <Card>
              <CardHeader title={t("product.breakdown")} subtitle={t("product.breakdownSub")} />
              <div className="space-y-3 p-4">
                {v3.data && (["segment", "price_band", "quality_room"] as const).map((k) => (
                  <div key={k}>
                    <ComponentBar id={`opp_p_${k}`} value={v3.data?.components[k].value} />
                    <div className="text-[10px] text-ink-3">{t("product.weight")} {v3.data?.components[k].weight}</div>
                  </div>
                ))}
                <div className="border-t border-line pt-2 text-[11px] text-ink-3">{t("product.segmentWhy")}</div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  {(v3.data?.segment_metrics ?? []).filter((r) => ["revenue_month", "hhi", "entrant_success_rate", "quality_gap_share", "rating_bar",
                    "entrant_revenue_expected", "entrant_profit_expected", "margin_rate_median"].includes(r.metric)).map((r) => (
                    <div key={r.metric} className="flex justify-between gap-2"><span className="text-ink-2">{t(`metric.${r.metric}.0`)} <Explain row={r} /></span>
                      <span className="font-mono">{fmt(r.value, r.unit)}</span></div>
                  ))}
                </div>
              </div>
            </Card>
            <Card>
              <CardHeader title={t("product.listingsTitle", { n: d.listings.length })} subtitle={t("product.listingsSub")} />
              <div className="overflow-x-auto" tabIndex={0}>
                <table className="w-full text-xs">
                  <thead className="text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-1.5">ASIN</th><th scope="col">{t("product.observed")}</th><th scope="col">{t("product.units")}</th><th scope="col">{t("common.interval")}</th><th scope="col">{t("product.margin")}</th><th scope="col" className="pr-4">{t("product.age")}</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {(v3.data?.listings ?? []).map((l) => (
                      <tr key={l.id}>
                        <td className="px-4 py-1.5 font-mono">{l.id}</td>
                        <td>{l.sales_observation === "badge" ? t("product.badge", { n: num(l.units_floor) }) : l.sales_observation}</td>
                        <td className="font-mono">{num(l.units_est)}</td>
                        <td className="font-mono text-ink-3">{num(l.units_lo)}–{num(l.units_hi)}</td>
                        <td className="font-mono">{l.unit_margin != null ? `${money(l.unit_margin, 2)} (${pct(l.margin_rate)})` : <span className="text-ink-3" title={l.unit_cost_issue ?? ""}>{l.unit_cost_issue ?? "—"}</span>}</td>
                        <td className="pr-4 font-mono">{l.age_days != null ? `${t("product.months", { n: Math.round(l.age_days / 30) })}${l.is_entrant ? ` · ${t("product.entrant")}` : ""}` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title={t("product.competition")} subtitle={t("product.competitionSub", { c: d.segment.concentration })} />
          <div className="divide-y divide-line">
            {d.competitors.length === 0 && <Empty title={t("product.noCompetitors")} />}
            {d.competitors.map((c) => (
              <Link key={c.product_id} href={`/products/${c.product_id}`} className="flex items-center gap-2 px-4 py-2 text-sm hover:bg-panel-2">
                <span className="min-w-0 flex-1 truncate">{c.title}</span>
                <span className="font-mono text-xs text-ink-2">{money(c.price, 2)}</span>
                <span className="w-14 text-right font-mono text-xs text-ink-3">{num(c.units_est ?? c.monthly_sales)}/mo</span>
              </Link>
            ))}
          </div>
        </Card>
        <Card>
          <CardHeader title={t("product.suppliers")} subtitle={t("product.suppliersSub")} />
          {d.suppliers.length === 0 ? <Empty title={t("product.noSuppliers")}><Link className="text-accent" href="/suppliers">{t("product.importSuppliers")}</Link>{t("product.thenReprocess")}</Empty> : (
            <div className="divide-y divide-line">
              {d.suppliers.map((s) => (
                <div key={s.id} className="px-4 py-2 text-sm">
                  <div className="flex justify-between"><b className="truncate">{s.name}</b><span className="font-mono text-xs">{s.score?.toFixed(0) ?? "—"}</span></div>
                  <div className="text-xs text-ink-3">{s.country ?? "—"} · {s.oem ? "OEM" : ""} {s.odm ? "ODM" : ""} · {t("product.match")} {pct(s.match_score)}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
        <LiveReviews market={d.market} asin={p.best_listing} />
        <Card>
          <CardHeader title={t("product.complaints")} subtitle={pain?.status === "ok" ? t(d.customer_pain.product ? "product.complaintsSubP" : "product.complaintsSubS", { n: pain.reviews }) : undefined} />
          {pain?.status !== "ok" ? <Empty title={t("product.noReviews")}>{t("product.noReviewsSub")}</Empty> : (
            <div className="space-y-3 p-4 text-sm">
              {pain.complaints.slice(0, 5).map((c) => (
                <div key={c.aspect}>
                  <div className="flex justify-between"><span>{c.aspect.replace(/_/g, " ")}</span><span className="font-mono text-bad">{pct(c.share_of_reviews)}</span></div>
                  <Meter value={c.share_of_reviews} color="var(--bad)" />
                  <div className="mt-0.5 text-xs text-ink-3">→ {c.opportunity}</div>
                </div>
              ))}
              {pain.missing_features.length > 0 && <div className="text-xs text-ink-2">{t("product.missing")}: {pain.missing_features.map((m) => m.feature).join(", ")}</div>}
            </div>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader title={t("product.similar")} subtitle={t("product.similarSub")} />
        <div className="grid grid-cols-1 divide-line sm:grid-cols-2 xl:grid-cols-4">
          {d.similar.map((s) => (
            <Link key={s.product_id} href={`/products/${s.product_id}`} className="border-b border-line px-4 py-3 text-sm hover:bg-panel-2">
              <div className="truncate">{truncate(s.title, 70)}</div>
              <div className="text-xs text-ink-3">{mn(s.market)} · {s.brand ?? "—"} · {money(s.price, 2)} · {t("product.sim")} {s.score.toFixed(2)}</div>
            </Link>
          ))}
        </div>
      </Card>
    </div>
  );
}
