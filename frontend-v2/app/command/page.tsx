"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Card, CardHeader, Empty } from "@/components/ui/primitives";
import { ErrorNote } from "@/components/page";
import { OPPORTUNITY_RAMP } from "@/lib/colors";
import { moneyShort, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import type { GeoCountry } from "@/components/three/globe";
import { useMarketName } from "@/lib/market-name";

const Globe = dynamic(() => import("@/components/three/globe"), { ssr: false });
const MapboxLayer = dynamic(() => import("@/components/map/mapbox-layer"), { ssr: false });
const HAS_MAPBOX = !!process.env.NEXT_PUBLIC_MAPBOX_TOKEN;

type Overview = {
  markets: number; products: number; listings: number; monthly_revenue: number | null; datasets: number;
  records_ingested: number; suppliers: number; employees: number;
  graph: { backend: string; nodes: Record<string, number>; edges: Record<string, number> };
  revenue_est: number | null; revenue_floor: number | null; markets_with_v3: number;
  top_brands_v3: { brand: string; market: string; revenue_est: number; revenue_lo: number; revenue_hi: number; share_est: number; position: string }[];
};
export default function GlobalView() {
  const mn = useMarketName();
  const { t } = useI18n();
  const ov = useApi<Overview>("/overview");
  const geo = useApi<{ countries: GeoCountry[]; unresolved_countries: string[] }>("/geo");
  const [sel, setSel] = useState<GeoCountry | null>(null);
  const [view, setView] = useState<"globe" | "map">("globe");
  const o = ov.data;

  return (
    <div className="flex min-h-screen flex-col">
      <div className="flex items-center justify-between px-6 pt-5">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.2em] text-accent">{t("home.kicker")}</div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("nav.command")}</h1>
          <p className="mt-1 text-sm text-ink-3">{t("ia.commandDesc")}</p>
        </div>
        <Link href="/data" className="text-sm text-accent hover:underline">{t("home.upload")} →</Link>
      </div>
      <ErrorNote error={ov.error} />

      <div className="grid flex-1 grid-cols-1 gap-4 p-6 xl:grid-cols-[1fr_380px]">
        <Card className="relative min-h-[420px] overflow-hidden sm:min-h-[560px]">
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
