"use client";
import Link from "next/link";
import { Activity, Command, Copy, FolderKanban, LayoutGrid, MessageSquareText, Rocket, Sparkles, Trophy, Upload, type LucideIcon } from "lucide-react";
import { Card, CardHeader, Empty, Skeleton } from "@/components/ui/primitives";
import { ErrorNote } from "@/components/page";
import { FirstRunGuide, GettingStarted } from "@/components/onboarding";
import { useMarket } from "@/components/market-switcher";
import { AnimatedNumber } from "@/components/v2/motion";
import { openPalette } from "@/components/v2/command-palette";
import { ActivityFeed, AttentionList, MarketCards, OpportunityLandscape, OpportunityList, useAttention, useMarkets, useOpportunities } from "@/components/v2/widgets";
import { moneyShort, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";
import { seq } from "@/lib/viz";

type Overview = { markets: number; products: number; listings: number; suppliers: number; datasets: number;
  revenue_est: number | null; revenue_floor: number | null; revenue_headline?: number | null; models_not_validated?: number;
  top_brands_v3: { brand: string; market: string; revenue_est: number; share_est: number }[] };

function Kpi({ label, value, sub, icon: Icon, href, accent }: { label: string; value: React.ReactNode; sub?: React.ReactNode; icon: LucideIcon; href?: string; accent?: string }) {
  const body = (
    <Card className={`relative h-full overflow-hidden p-4 ${href ? "lift" : ""}`}>
      <div aria-hidden className="pointer-events-none absolute -right-6 -top-6 h-20 w-20 rounded-full opacity-20 blur-2xl" style={{ background: accent ?? "var(--accent)" }} />
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-3">{label}</span>
        <Icon className="h-4 w-4 text-ink-3" aria-hidden />
      </div>
      <div className="mt-2 font-mono text-2xl text-ink">{value}</div>
      {sub && <div className="mt-1 truncate text-[11px] text-ink-3" title={typeof sub === "string" ? sub : undefined}>{sub}</div>}
    </Card>
  );
  return href ? <Link href={href} className="block h-full">{body}</Link> : body;
}

export default function MissionControl() {
  const { t, lang } = useI18n();
  const mn = useMarketName();
  const [market] = useMarket();
  const ov = useApi<Overview>("/overview");
  const mk = useMarkets();
  const opp = useOpportunities(mk.names);
  const att = useAttention(mk.names);
  const o = ov.data;
  const best = opp.rows[0];
  const hour = new Date().getHours();

  return (
    <div className="space-y-5 px-4 pb-8 pt-5 md:px-6">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-accent">
            <span className="live-dot inline-block h-1.5 w-1.5 rounded-full bg-good text-good" aria-hidden />{t("mc.kicker")}
          </div>
          <h1 className="gradient-text mt-1 text-3xl font-semibold tracking-tight">{t(hour < 12 ? "mc.morning" : hour < 18 ? "mc.afternoon" : "mc.evening")}</h1>
          <p className="mt-1 text-sm text-ink-3">{new Date().toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US", { weekday: "long", month: "long", day: "numeric" })}
            {" · "}{market ? t("mc.focusOn", { m: mn(market) }) : t("mc.allMarkets", { n: mk.names.length })}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {([["/data", "ia.act.upload", Upload], ["/launch", "ia.act.simulate", Rocket], ["/analyst", "ia.act.ask", MessageSquareText]] as const).map(([href, k, Icon]) => (
            <Link key={href} href={href} className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-line bg-panel-2/60 px-3 text-sm text-ink-2 hover:border-accent/60 hover:text-ink">
              <Icon className="h-4 w-4 text-accent" aria-hidden />{t(k)}</Link>
          ))}
          <button type="button" onClick={openPalette} className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-accent px-3 text-sm font-medium text-on-accent hover:bg-accent-hover">
            <Command className="h-4 w-4" aria-hidden />{t("mc.jump")}</button>
        </div>
      </section>

      <ErrorNote error={ov.error ?? mk.error} />
      {mk.data?.length === 0 && <FirstRunGuide />}
      {!!mk.data?.length && <div className="-mx-6 [&>div]:mt-0"><GettingStarted markets={mk.data} market={market} /></div>}

      <section className="stagger grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6" aria-label={t("mc.kpis")}>
        <Kpi label={t("home.markets")} icon={LayoutGrid} href="/markets" value={!o ? <Skeleton className="h-7 w-12" /> : <AnimatedNumber value={o.markets} format={(v) => num(v, 0)} />}
          sub={t("mc.datasets", { n: num(o?.datasets) })} />
        <Kpi label={t("home.products")} icon={Sparkles} value={!o ? <Skeleton className="h-7 w-16" /> : <AnimatedNumber value={o.products} format={(v) => num(v, 0)} />}
          sub={`${num(o?.listings)} ${t("common.listings")}`} accent="var(--accent-2)" />
        <Kpi label={t("home.revenue")} icon={Activity} value={!o ? <Skeleton className="h-7 w-20" /> : <AnimatedNumber value={o.revenue_headline ?? o.revenue_floor} format={(v) => moneyShort(v)} />}
          sub={o?.revenue_est != null ? `${t("size.observed")} · ${t("size.modelled", { v: moneyShort(o.revenue_est) })}${o.models_not_validated ? ` · ${t("home.notValidatedN", { n: o.models_not_validated })}` : ""}` : t("size.observed")}
          accent="var(--good)" />
        <Kpi label={t("mc.bestScore")} icon={Trophy} href="/opportunities"
          value={opp.loading ? <Skeleton className="h-7 w-10" /> : <AnimatedNumber value={best?.opportunity_score} format={(v) => v.toFixed(0)} />}
          sub={best ? best.label : t("kn.insufficient")} accent={seq((best?.opportunity_score ?? 0) / 100)} />
        <Kpi label={t("mc.toReview")} icon={Copy} href="/review" value={att.loading ? <Skeleton className="h-7 w-10" /> : <AnimatedNumber value={att.reviewOpen} format={(v) => num(v, 0)} />}
          sub={t("mc.toReviewSub")} accent="var(--warn)" />
        <Kpi label={t("mc.projects")} icon={FolderKanban} href="/projects" value={!att.projects ? <Skeleton className="h-7 w-10" /> : <AnimatedNumber value={att.projects.projects.length} format={(v) => num(v, 0)} />}
          sub={t("mc.suppliersN", { n: num(o?.suppliers) })} accent="var(--accent-2)" />
      </section>

      <section className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <OpportunityList rows={opp.rows} loading={opp.loading} colors={mk.colors} market={market || undefined} />
        <AttentionList items={att.items} loading={att.loading} />
      </section>

      <OpportunityLandscape rows={opp.rows} loading={opp.loading} names={mk.names} colors={mk.colors} />

      <section aria-label={t("mc.marketsTitle")}>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-ink-3">{t("mc.marketsTitle")}</h2>
          <Link href="/markets" className="text-xs text-accent hover:underline">{t("mc.allMarketsLink")} →</Link>
        </div>
        <MarketCards markets={mk.data ?? []} loading={!mk.data} />
      </section>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <ActivityFeed />
        <Card>
          <CardHeader title={t("home.brands")} subtitle={t("home.brandsSub")}
            right={<Link href="/command" className="whitespace-nowrap text-xs text-accent hover:underline">{t("mc.globalView")} →</Link>} />
          <ul className="divide-y divide-line">
            {!o && [0, 1, 2, 3].map((i) => <li key={i} className="px-4 py-2.5"><Skeleton className="h-4 w-full" /></li>)}
            {o && !o.top_brands_v3.length && <li><Empty title={t("common.notAvailable")} /></li>}
            {(o?.top_brands_v3 ?? []).slice(0, 8).map((b) => {
              const top = o?.top_brands_v3[0]?.revenue_est || 1;
              return (
                <li key={`${b.market}-${b.brand}`}>
                  <Link href={`/competitors?market=${encodeURIComponent(b.market)}`} className="block px-4 py-2 hover:bg-panel-2/60">
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span className="min-w-0 truncate">{b.brand} <span className="text-[11px] text-ink-3">· {mn(b.market)}</span></span>
                      <span className="shrink-0 font-mono text-xs">{moneyShort(b.revenue_est)} <span className="text-ink-3">{pct(b.share_est, 0)}</span></span>
                    </div>
                    <div className="mt-1 h-1 rounded-full bg-line"><div className="h-full rounded-full" style={{ width: `${(b.revenue_est / top) * 100}%`, background: mk.colors.get(b.market) }} /></div>
                  </Link>
                </li>
              );
            })}
          </ul>
        </Card>
      </section>
    </div>
  );
}
