"use client";
import { useState } from "react";
import { Eye, RefreshCw, Trash2, TrendingDown, TrendingUp } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input, Select, Skeleton } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import { del, post, type MarketRow } from "@/lib/api";
import { money, num, pct, timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Metric = "price" | "sales" | "bsr" | "rating" | "reviews";
type Point = { observed_at: string; source: string } & Record<Metric, number | null>;
type Change = { metric: Metric; from: number; to: number; change: number | null; to_at: string; badge_tier: boolean };
type Item = { id: string; asin: string; market_name: string | null; label: string | null; note: string | null; added_by: string | null;
  created_at: string; latest: Record<Metric, number | null>; observed_at: string | null; observations: number; changes: Change[]; points: Point[] };
type Alerts = { price_move: number; rating_drop: number; review_surge: number; bsr_move: number; sales_move: number };
type WatchList = { items: Item[]; settings: { alerts: Alerts; every_hours: number; max_items: number } };
type AcqStatus = { active: Record<string, string | null> };

/** A small price line (oldest → newest); the title carries every value for hover and screen readers. */
function Spark({ points, label }: { points: Point[]; label: string }) {
  const pts = points.filter((p) => p.price != null);
  if (pts.length < 2) return <span className="text-[11px] text-ink-3">—</span>;
  const vs = pts.map((p) => p.price as number);
  const lo = Math.min(...vs), hi = Math.max(...vs), W = 96, H = 28, pad = 3;
  const x = (i: number) => pad + (i * (W - 2 * pad)) / (pts.length - 1);
  const y = (v: number) => (hi === lo ? H / 2 : H - pad - ((v - lo) * (H - 2 * pad)) / (hi - lo));
  const d = vs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const title = `${label}: ${pts.map((p) => `${p.observed_at.slice(0, 10)} ${money(p.price, 2)}`).join(", ")}`;
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} className="overflow-visible">
      <title>{title}</title>
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(pts.length - 1)} cy={y(vs[vs.length - 1])} r={3} fill="var(--accent)" stroke="var(--panel)" strokeWidth={2} />
    </svg>
  );
}

function ChangeBadge({ c }: { c: Change }) {
  const { t } = useI18n();
  // up is good for sales/rating/reviews, a lower rank number is better, a price move is neutral news
  const good = c.metric === "bsr" ? c.to < c.from : c.metric === "price" ? null : c.to > c.from;
  const color = good == null ? "var(--warn)" : good ? "var(--good)" : "var(--bad)";
  const Icon = c.to > c.from ? TrendingUp : TrendingDown;
  const val = c.metric === "price" ? `${money(c.from, 2)} → ${money(c.to, 2)}`
    : c.metric === "rating" ? `${c.from.toFixed(1)} → ${c.to.toFixed(1)}`
    : `${num(c.from)} → ${num(c.to)}`;
  return (
    <Badge color={color} className="text-left">
      <Icon className="mr-1 inline h-3 w-3 align-[-2px]" aria-hidden />
      {t(`watch.m.${c.metric}`)} {val}{c.change != null && c.metric !== "rating" ? ` (${c.change > 0 ? "+" : ""}${pct(c.change, 0)})` : ""}
      {c.badge_tier ? ` · ${t("watch.badge")}` : ""}
    </Badge>
  );
}

export default function Watchlist() {
  const { t, lang } = useI18n();
  const mn = useMarketName();
  const [ctx] = useMarket();
  const markets = useApi<MarketRow[]>("/markets");
  const list = useApi<WatchList>("/watchlist");
  const acq = useApi<AcqStatus>("/acquire/status");
  const [asin, setAsin] = useState("");
  const [label, setLabel] = useState("");
  const [market, setMarket] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const chosen = market ?? ctx ?? "";
  const live = !!acq.data?.active.amazon_detail;
  const a = list.data?.settings.alerts;

  const add = async () => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      await post("/watchlist", { asin: asin.trim().toUpperCase(), market: chosen || null, label: label.trim() || null });
      setAsin(""); setLabel(""); setMsg(t("watch.added")); list.reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const remove = async (id: string) => {
    try { await del(`/watchlist/${id}`); list.reload(); } catch (e) { setErr((e as Error).message); }
  };
  const refresh = async () => {
    setErr(null);
    try { await post("/watchlist/refresh", {}); setMsg(t("watch.refreshStarted")); setTimeout(list.reload, 8000); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <div className="pb-8">
      <PageHeader title={t("watch.title")} subtitle={t("watch.subtitle")}
        right={<Button variant="outline" onClick={refresh} disabled={!live} title={live ? undefined : t("watch.needProvider")}>
          <RefreshCw className="h-4 w-4" aria-hidden />{t("watch.refresh")}</Button>} />
      <ErrorNote error={list.error} />
      <div className="space-y-5 px-4 md:px-6">
        <Card>
          <CardHeader title={t("watch.add")} subtitle={a ? t("watch.thresholds", { p: pct(a.price_move, 0), r: a.rating_drop, v: pct(a.review_surge, 0),
            b: pct(a.bsr_move, 0), s: pct(a.sales_move, 0) }) : undefined} />
          <form className="grid grid-cols-1 gap-3 p-4 text-sm sm:grid-cols-2 lg:grid-cols-[10rem_minmax(0,1fr)_minmax(0,14rem)_auto] lg:items-end"
            onSubmit={(e) => { e.preventDefault(); if (asin.trim().length === 10) void add(); }}>
            <label className="block">{t("watch.asin")}
              <Input className="mt-1 font-mono uppercase" value={asin} maxLength={10} onChange={(e) => setAsin(e.target.value)} placeholder="B0XXXXXXXX" /></label>
            <label className="block">{t("watch.label")}
              <Input className="mt-1" value={label} onChange={(e) => setLabel(e.target.value)} placeholder={t("watch.labelPh")} /></label>
            <label className="block">{t("watch.market")}
              <Select className="mt-1 w-full" value={chosen} onChange={(e) => setMarket(e.target.value)}>
                <option value="">{t("watch.anyMarket")}</option>
                {(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}
              </Select></label>
            <Button type="submit" disabled={busy || asin.trim().length !== 10}><Eye className="h-4 w-4" aria-hidden />{t("watch.addBtn")}</Button>
          </form>
          {!live && acq.data && <p className="border-t border-line px-4 py-2 text-xs text-ink-3">{t("watch.needProvider")}</p>}
          {msg && <p role="status" className="px-4 pb-3 text-sm text-good">{msg}</p>}
          {err && <p role="alert" className="px-4 pb-3 text-sm text-bad">{err}</p>}
        </Card>

        <Card>
          {!list.data && <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14 rounded-lg" />)}</div>}
          {list.data?.items.length === 0 && <Empty title={t("watch.empty")}>{t("watch.emptySub")}</Empty>}
          {!!list.data?.items.length && (
            <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={t("watch.title")}>
              <table className="w-full text-sm">
                <thead className="text-left text-[10px] uppercase tracking-wide text-ink-3 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-2">
                  <tr><th scope="col">{t("watch.asin")}</th><th scope="col">{t("watch.trend")}</th>
                    <th scope="col" className="text-right">{t("watch.price")}</th><th scope="col" className="text-right">{t("watch.sales")}</th>
                    <th scope="col" className="text-right">{t("watch.bsr")}</th><th scope="col" className="text-right">{t("watch.rating")}</th>
                    <th scope="col" className="text-right">{t("watch.reviews")}</th><th scope="col" className="min-w-[15rem]">{t("watch.moved")}</th><th scope="col"><span className="sr-only">{t("watch.remove")}</span></th></tr>
                </thead>
                <tbody className="divide-y divide-line [&_td]:px-3 [&_td]:py-2.5 [&_td]:align-top">
                  {list.data.items.map((it) => (
                    <tr key={it.id}>
                      <td className="min-w-[12rem] max-w-[16rem]">
                        <div className="truncate font-medium text-ink" title={it.label ?? it.asin}>{it.label ?? it.asin}</div>
                        <div className="text-[11px] text-ink-3">
                          <a className="font-mono hover:text-accent" href={`https://www.amazon.com/dp/${it.asin}`} target="_blank" rel="noreferrer">{it.asin}</a>
                          {it.market_name && <> · {mn(it.market_name)}</>}
                        </div>
                        <div className="text-[11px] text-ink-3">
                          {t("watch.observed")}: {it.observed_at ? timeAgo(it.observed_at, lang) : "—"} · {t("watch.observations", { n: it.observations })}
                        </div>
                      </td>
                      <td><Spark points={it.points} label={`${it.label ?? it.asin} ${t("watch.price")}`} /></td>
                      <td className="text-right font-mono">{money(it.latest.price, 2)}</td>
                      <td className="text-right font-mono">{num(it.latest.sales)}</td>
                      <td className="text-right font-mono">{num(it.latest.bsr)}</td>
                      <td className="text-right font-mono">{it.latest.rating != null ? it.latest.rating.toFixed(1) : "—"}</td>
                      <td className="text-right font-mono">{num(it.latest.reviews)}</td>
                      <td><div className="flex max-w-[26rem] flex-wrap gap-1">
                        {it.changes.length ? it.changes.map((c) => <ChangeBadge key={c.metric} c={c} />) : <span className="text-xs text-ink-3">{t("watch.nothing")}</span>}
                      </div></td>
                      <td><Button size="sm" variant="ghost" onClick={() => remove(it.id)} aria-label={`${t("watch.remove")}: ${it.label ?? it.asin}`}>
                        <Trash2 className="h-4 w-4" aria-hidden /></Button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
