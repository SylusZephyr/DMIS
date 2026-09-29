"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Badge, Card, CardHeader, Empty, Stat } from "@/components/ui/primitives";
import { ErrorNote } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import { FirstRunGuide, GettingStarted } from "@/components/onboarding";
import type { MarketRow } from "@/lib/api";
import { OPPORTUNITY_RAMP } from "@/lib/colors";
import { money, moneyShort, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { seq } from "@/lib/viz";
import type { GeoCountry } from "@/components/three/globe";
import { useMarketName } from "@/lib/market-name";
import { headlineOf, ModelledNote } from "@/components/market-size";

const Globe = dynamic(() => import("@/components/three/globe"), { ssr: false });
const MapboxLayer = dynamic(() => import("@/components/map/mapbox-layer"), { ssr: false });
const HAS_MAPBOX = !!process.env.NEXT_PUBLIC_MAPBOX_TOKEN;

type Overview = {
  markets: number; products: number; listings: number; monthly_revenue: number | null; datasets: number;
  records_ingested: number; suppliers: number; employees: number;
  graph: { backend: string; nodes: Record<string, number>; edges: Record<string, number> };
  revenue_est: number | null; revenue_floor: number | null; revenue_headline?: number | null; models_not_validated?: number; markets_with_v3: number;
  top_brands_v3: { brand: string; market: string; revenue_est: number; revenue_lo: number; revenue_hi: number; share_est: number; position: string }[];
};
const GRADE_COLOR: Record<string, string> = { A: "var(--good)", B: "#3987e5", C: "var(--warn)", D: "var(--ink-3)" };

function priceText(p: number[] | string | undefined): string {
  if (Array.isArray(p) && p.length === 2) return `${money(p[0], 2)}–${money(p[1], 2)}`;
  return typeof p === "string" ? p : "";
}

export default function CommandCenter() {
  const mn = useMarketName();
  const { t } = useI18n();
  const ov = useApi<Overview>("/overview");
  const geo = useApi<{ countries: GeoCountry[]; unresolved_countries: string[] }>("/geo");
  const mk = useApi<MarketRow[]>("/markets");
  const [market] = useMarket();
  const [sel, setSel] = useState<GeoCountry | null>(null);
  const [view, setView] = useState<"globe" | "map">("globe");
  const o = ov.data;
  const nodes = o ? Object.values(o.graph.nodes).reduce((a, b) => a + b, 0) : 0;
  // the explainable opportunity engine's score ranks markets (the board's order); the older index only fills in
  const oppOf = (m: MarketRow) => m.top_opportunity_score ?? m.top_segment?.opportunity_index ?? null;
  const markets = [...(mk.data ?? [])].sort((a, b) => (oppOf(b) ?? -1) - (oppOf(a) ?? -1));

  return (
    <div className="flex min-h-screen flex-col">
      <div className="flex items-center justify-between px-6 pt-5">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-accent">{t("home.kicker")}</div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("home.title")}</h1>
        </div>
        <Link href="/data" className="text-sm text-accent hover:underline">{t("home.upload")} →</Link>
      </div>
      <ErrorNote error={ov.error} />
      {mk.data?.length === 0 && <FirstRunGuide />}
      {!!mk.data?.length && <GettingStarted markets={mk.data} market={market} />}

      <Card className="mx-6 mt-4 grid grid-cols-2 divide-x divide-line sm:grid-cols-3 xl:grid-cols-6">
        <Stat label={t("home.markets")} value={num(o?.markets)} sub={t("home.withV3", { n: o?.markets_with_v3 ?? 0 })} />
        <Stat label={t("home.products")} value={num(o?.products)} sub={`${num(o?.listings)} ${t("common.listings")}`} />
        {/* observed floor first (what the data certainly shows); the modelled estimate beside it, flagged when not validated */}
        <Stat label={t("home.revenue")} value={moneyShort(o?.revenue_headline ?? o?.revenue_floor)}
          sub={`${t("home.modelled", { v: moneyShort(o?.revenue_est) })}${o?.models_not_validated ? ` · ${t("home.notValidatedN", { n: o.models_not_validated })}` : ""}`} />
        <Stat label={t("home.records")} value={num(o?.records_ingested)} sub={t("home.datasets", { n: num(o?.datasets) })} />
        <Stat label={t("home.suppliers")} value={num(o?.suppliers)} />
        <Stat label={t("home.graph")} value={num(nodes)} sub={o?.graph.backend ?? ""} />
      </Card>

      <div className="px-6 pt-4">
        <Card>
          <CardHeader title={t("home.actTitle")} subtitle={t("home.actSub")} />
          {mk.data?.length === 0 ? <Empty title={t("home.noMarkets")}>{t("home.noMarketsSub")}</Empty> : (
            <div className="grid grid-cols-1 divide-y divide-line lg:grid-cols-3 lg:divide-x lg:divide-y-0">
              {markets.map((m) => {
                const ts = m.top_segment;
                const rec = m.recommendation;
                return (
                  <Link key={m.name} href={`/markets/${encodeURIComponent(m.name)}`} className="block p-4 hover:bg-panel-2">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{mn(m.name)}</span>
                      {m.evidence_grade && <Badge color={GRADE_COLOR[m.evidence_grade]}>{t("common.evidenceGrade")} {m.evidence_grade}</Badge>}
                    </div>
                    {/* observed floor as the headline (as on the market page); the modelled estimate beside it, flagged */}
                    <div className="mt-1 font-mono text-lg">{moneyShort(headlineOf(m))}<span className="text-xs text-ink-3">/mo</span></div>
                    <ModelledNote m={m} className="block text-[11px] text-ink-3" />
                    {(m.top_opportunity_label || ts) && (
                      <div className="mt-2 flex items-center gap-2 text-xs">
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: seq((oppOf(m) ?? 0) / 100) }} />
                        <span className="min-w-0 flex-1 truncate text-ink-2" title={m.top_opportunity_label ?? ts?.segment_label ?? ""}>{t("home.bestOpp")}: {m.top_opportunity_label ?? ts?.segment_label}</span>
                        <span className="font-mono" title={t("home.scoreTip")}>{oppOf(m)?.toFixed(0)}</span>
                      </div>
                    )}
                    {rec && (
                      <div className="mt-1 text-xs text-ink-3">{t("home.recommend")}: <span className="text-ink-2">{rec.features?.length ? rec.features.join(", ") : t("home.bestBand")}</span>
                        {" · "}{priceText(rec.price_range ?? undefined)}{rec.expected_units != null && <> · ~{num(rec.expected_units, 0)} {t("common.units")}/mo</>}</div>
                    )}
                    <div className="mt-1 text-[11px] text-ink-3">{num(m.products)} {t("common.products")} · {t("home.hhi")} {num(m.hhi)}</div>
                  </Link>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      <div className="grid flex-1 grid-cols-1 gap-4 p-6 xl:grid-cols-[1fr_380px]">
        <Card className="relative min-h-[320px] overflow-hidden sm:min-h-[440px]">
          <div className="absolute inset-0">
            {geo.data && (view === "map" && HAS_MAPBOX
              ? <MapboxLayer countries={geo.data.countries} onSelect={setSel} />
              : <Globe ariaLabel={t("a11y.view3dNote")} countries={geo.data.countries} onSelect={setSel} selected={sel?.iso2}
                  tip={(c) => `${t("home.nSuppliers", { n: c.suppliers })}${c.markets.length ? ` · ${t("home.nMarkets", { n: c.markets.length })}` : ""}`} />)}
          </div>
          <div className="pointer-events-none absolute left-4 top-4 max-w-sm">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">{t("home.world")}</div>
            <div className="text-xs text-ink-3">{t("home.worldSub")}</div>
          </div>
          {HAS_MAPBOX && (
            <div className="absolute bottom-4 right-4 flex overflow-hidden rounded-lg border border-line text-xs">
              {(["globe", "map"] as const).map((v) => (
                <button key={v} onClick={() => setView(v)}
                  className={`px-3 py-1.5 ${view === v ? "bg-accent/15 text-accent" : "bg-panel text-ink-2 hover:text-ink"}`}>{t(`home.view.${v}`)}</button>
              ))}
            </div>
          )}
          <div className="pointer-events-none absolute bottom-4 left-4 flex items-center gap-2 text-[10px] text-ink-3">
            {t("home.opportunity")}
            {OPPORTUNITY_RAMP.map((s) => <span key={s.v} className="h-2 w-5 rounded-sm" style={{ background: s.c }} />)}
            <span>{OPPORTUNITY_RAMP[0].v}–{OPPORTUNITY_RAMP.at(-1)!.v}+</span>
          </div>
          {sel && (
            <Card className="absolute right-4 top-4 w-72 p-4">
              <div className="flex items-center justify-between"><b>{sel.country}</b><button className="text-ink-3" onClick={() => setSel(null)}>✕</button></div>
              <div className="mt-2 space-y-1 text-sm text-ink-2">
                <div>{t("home.suppliers")}: <b className="text-ink">{sel.suppliers}</b>{sel.avg_supplier_score != null && ` · ${t("home.avgScore")} ${sel.avg_supplier_score}`}</div>
                <div>{t("home.markets")}: <b className="text-ink">{sel.markets.length}</b></div>
                {sel.markets.map((m) => <Link key={m} href={`/markets/${encodeURIComponent(m)}`} className="block text-accent hover:underline">{m}</Link>)}
                {sel.suppliers > 0 && <Link href={`/suppliers?country=${encodeURIComponent(sel.country)}`} className="block text-accent hover:underline">{t("home.viewSuppliers")} →</Link>}
              </div>
            </Card>
          )}
          {geo.data && geo.data.countries.length === 0 && (
            <div className="absolute inset-x-0 bottom-16 text-center text-xs text-ink-3">{t("home.noGeo")}</div>
          )}
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader title={t("home.brands")} subtitle={t("home.brandsSub")} />
            <div className="divide-y divide-line">
              {(o?.top_brands_v3 ?? []).slice(0, 10).map((b) => (
                <Link key={`${b.market}-${b.brand}`} href={`/competitors?market=${encodeURIComponent(b.market)}`} className="flex items-center justify-between gap-2 px-4 py-2 text-sm hover:bg-panel-2">
                  <span className="min-w-0 flex-1 truncate">{b.brand} <span className="text-[11px] text-ink-3">· {mn(b.market)}</span></span>
                  <span className="text-right font-mono text-xs">{moneyShort(b.revenue_est)}<div className="text-[10px] text-ink-3">{pct(b.share_est, 1)} {t("home.ofMarket")}</div></span>
                </Link>
              ))}
              {o && !o.top_brands_v3.length && <Empty title={t("common.notAvailable")} />}
            </div>
          </Card>
          {[["/opportunities", "home.toBoard"], ["/universe", "home.toUniverse"], ["/analyst", "home.toAnalyst"], ["/methodology", "home.toMethod"]].map(([href, key]) => (
            <Link key={href} href={href} className="flex items-center justify-between rounded-xl border border-line bg-panel px-4 py-3 text-sm hover:border-accent">
              {t(key)} <ArrowUpRight className="h-4 w-4 text-accent" />
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
