"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Card, CardHeader, Empty, Select, Stat } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { headlineOf } from "@/components/v2/market-size";
import type { Action, Focus, MarketRow } from "@/lib/api";
import { opportunityColor, trendColor } from "@/lib/colors";
import { money, moneyShort, num, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Emp = { id: string; name: string; categories: number; markets: number; source_listings: number };
type Dash = {
  id: string; name: string; markets: string[];
  categories: { id: string; label: string; market: string | null; source_listing_count: number | null }[];
  kpis: { categories: number; markets: number; opportunities: number; potential_products: number; supplier_matches: number; market_size_monthly: number | null };
  potential_products: { product_id: string; title: string; price: number | null; monthly_sales: number | null; opportunity_score: number; image: string | null; market: string }[];
};

export default function Portfolio() {
  const mn = useMarketName();
  const { t } = useI18n();
  const emps = useApi<Emp[]>("/employees");
  const [picked, setWho] = useState<string>("");
  const who = picked || emps.data?.[0]?.id || "";
  const dash = useApi<Dash>(who ? `/employees/${who}/dashboard` : null, [who]);
  const focus = useApi<Focus>(who ? `/employees/${who}/focus` : null, [who]);
  const mk = useApi<MarketRow[]>("/markets");
  const v3 = new Map((mk.data ?? []).map((m) => [m.name, m]));
  const d = dash.data;
  // portfolio size = sum of each market's headline (observed floor), as on the market pages; the modelled sum beside it
  const headSize = d ? d.markets.reduce((s, m) => s + (headlineOf(v3.get(m) ?? {}) ?? 0), 0) : 0;
  const estSize = d ? d.markets.reduce((s, m) => s + (v3.get(m)?.revenue_est ?? 0), 0) : 0;
  return (
    <div className="pb-8">
      <PageHeader title={t("portfolio.title")} subtitle={t("portfolio.subtitle")}
        right={<Select value={who} onChange={(e) => setWho(e.target.value)} className="w-64">
          {(emps.data ?? []).map((e) => <option key={e.id} value={e.id}>{e.name} · {t("portfolio.nCats", { n: e.categories })}{e.markets ? ` · ${t("portfolio.nMarkets", { n: e.markets })}` : ""}</option>)}
        </Select>} />
      <ErrorNote error={emps.error} />
      {emps.data?.length === 0 && <Card className="mx-6"><Empty title={t("portfolio.noEmp")}>{t("portfolio.noEmpSub")}</Empty></Card>}
      {d && (
        <>
          <Card className="mx-6 grid grid-cols-2 divide-x divide-line md:grid-cols-6">
            <Stat label={t("portfolio.categories")} value={num(d.kpis.categories)} />
            <Stat label={t("portfolio.analysed")} value={num(d.kpis.markets)} />
            <Stat label={t("portfolio.opps")} value={num(d.kpis.opportunities)} sub={t("portfolio.oppsSub")} />
            <Stat label={t("portfolio.potential")} value={num(d.kpis.potential_products)} />
            <Stat label={t("portfolio.supMatches")} value={num(d.kpis.supplier_matches)} />
            <Stat label={t("portfolio.size")} value={moneyShort(headSize || d.kpis.market_size_monthly)}
              sub={estSize ? `${t("size.observed")} · ${t("size.modelled", { v: moneyShort(estSize) })}` : t("portfolio.sizeObs")} />
          </Card>
          {focus.data && <FocusPanel f={focus.data} v3={v3} />}
          <div className="grid grid-cols-1 gap-4 px-6 pt-4 xl:grid-cols-[360px_1fr]">
            <Card>
              <CardHeader title={t("portfolio.resp")} subtitle={t("portfolio.respSub")} />
              <div className="max-h-[520px] divide-y divide-line overflow-y-auto scrollbar-thin">
                {[...d.categories].sort((a, b) => Number(!!b.market) - Number(!!a.market) || (b.source_listing_count ?? 0) - (a.source_listing_count ?? 0)).map((c) => (
                  <div key={c.id} className="flex items-center justify-between px-4 py-2 text-sm">
                    <span className="truncate">{c.label}</span>
                    {c.market ? <Link href={`/markets/${encodeURIComponent(c.market)}`}><Badge color="var(--accent)">{c.market} →</Badge></Link>
                      : <span className="text-xs text-ink-3">{num(c.source_listing_count)} {t("common.listings")} · {t("portfolio.notAnalysed")}</span>}
                  </div>
                ))}
              </div>
            </Card>
            <Card>
              <CardHeader title={t("portfolio.potential")} subtitle={t("portfolio.potentialSub")} />
              {d.potential_products.length === 0 ? <Empty title={t("portfolio.noPotential")}>{t("portfolio.noPotentialSub")}</Empty> : (
                <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 2xl:grid-cols-3">
                  {d.potential_products.map((p) => (
                    <Link key={p.product_id} href={`/products/${p.product_id}`} className="flex gap-3 rounded-lg border border-line p-2 hover:border-accent">
                      <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded bg-white">{p.image && <img src={p.image} alt="" className="max-h-14 object-contain" onError={(e) => { e.currentTarget.style.display = "none"; }} />}</div>
                      <div className="min-w-0 text-xs">
                        <div className="line-clamp-2 text-sm">{truncate(p.title, 90)}</div>
                        <div className="text-ink-3">{mn(p.market)} · {money(p.price, 2)}</div>
                        <div className="font-mono" style={{ color: opportunityColor(p.opportunity_score) }}>{t("portfolio.oppIndex")} {p.opportunity_score.toFixed(0)}</div>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

const PRIO: Record<string, string> = { high: "var(--bad)", medium: "var(--warn)", low: "var(--ink-3)" };

function actionHref(a: Action): string | null {
  const l = a.link;
  if (!l) return null;
  const m = l.market ? encodeURIComponent(l.market) : "";
  switch (l.page) {
    case "launch": return `/launch?market=${m}&segment_id=${encodeURIComponent(l.segment_id ?? "")}&title=${encodeURIComponent(l.title ?? "")}`;
    case "alerts": return "/alerts";
    case "suppliers": return "/suppliers";
    case "data": return "/data";
    case "competitors": return `/competitors?market=${m}`;
    case "accuracy": return "/accuracy";
    default: return null;
  }
}

function FocusPanel({ f, v3 }: { f: Focus; v3: Map<string, MarketRow> }) {
  const mn = useMarketName();
  const { t, lang } = useI18n();
  return (
    <div className="grid grid-cols-1 gap-4 px-6 pt-4 2xl:grid-cols-[1fr_1.2fr]">
      <Card>
        <CardHeader title={t("portfolio.focus")} subtitle={t("portfolio.focusSub", { a: f.recommended_actions.length, n: f.new_alerts })} />
        <div className="max-h-[520px] divide-y divide-line overflow-y-auto scrollbar-thin">
          {f.recommended_actions.length === 0 ? <Empty title={t("portfolio.nothing")} /> : f.recommended_actions.map((a, i) => {
            const href = actionHref(a);
            const body = (
              <div className="px-4 py-2.5 text-sm hover:bg-panel-2">
                <div className="flex items-center gap-2"><Badge color={PRIO[a.priority]}>{t(`portfolio.prio.${a.priority}`)}</Badge><span className="font-medium">{lang === "zh" && a.action_zh ? a.action_zh : a.action}</span></div>
                <div className="mt-0.5 text-xs text-ink-3">{lang === "zh" && a.reason_zh ? a.reason_zh : a.reason}</div>
              </div>
            );
            return href ? <Link key={i} href={href}>{body}</Link> : <div key={i}>{body}</div>;
          })}
        </div>
      </Card>
      <Card>
        <CardHeader title={t("portfolio.status")} subtitle={t("portfolio.statusSub")} />
        <div className="grid grid-cols-1 gap-3 p-4 lg:grid-cols-2">
          {f.markets.map((m) => (
            <Link key={m.market} href={`/markets/${encodeURIComponent(m.market)}`} className="rounded-lg border border-line p-3 text-xs hover:border-accent">
              <div className="flex items-center justify-between text-sm"><b>{mn(m.market)}</b>
                {v3.get(m.market)?.evidence_grade ? <Badge>{t("common.evidenceGrade")} {v3.get(m.market)?.evidence_grade}</Badge> : <span style={{ color: trendColor(m.trend?.trend) }}>{m.trend?.trend ?? "—"}</span>}</div>
              <div className="mt-2 grid grid-cols-3 gap-2">
                <div>{t("portfolio.sizeShort")}<div className="font-mono text-sm">{moneyShort(headlineOf(v3.get(m.market) ?? {}) ?? m.market_size.monthly_revenue)}</div></div>
                <div>{t("home.products")}<div className="font-mono text-sm">{num(m.products)}</div></div>
                <div>{t("portfolio.oppIndex")}<div className="font-mono text-sm" style={{ color: opportunityColor(v3.get(m.market)?.top_segment?.opportunity_score ?? null) }}>{v3.get(m.market)?.top_segment?.opportunity_score?.toFixed(0) ?? "—"}</div></div>
              </div>
              {v3.get(m.market)?.recommendation ? <div className="mt-2 text-ink-2">{t("portfolio.recommend")}: <b className="text-ink">{(v3.get(m.market)?.recommendation?.features ?? []).join(", ") || t("home.bestBand")}</b> · {truncate(v3.get(m.market)?.recommendation?.segment_label ?? "", 50)}</div>
                : m.suggested_development && <div className="mt-2 text-ink-2">{t("portfolio.recommend")}: <b className="text-ink">{truncate(m.suggested_development, 90)}</b></div>}
              {m.competitors[0] && <div className="mt-1 text-ink-3">{t("portfolio.leader")}: {m.competitors[0].brand} ({(m.competitors[0].share * 100).toFixed(0)}%){m.competitors[0].weakness ? ` — ${m.competitors[0].weakness}` : ""}</div>}
              <div className="mt-1 text-ink-3">{t("home.suppliers")}: {m.suppliers.length ? m.suppliers.slice(0, 3).map((s) => s.name).join(", ") : t("portfolio.noSup")}</div>
            </Link>
          ))}
        </div>
      </Card>
    </div>
  );
}
