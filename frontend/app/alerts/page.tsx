"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post, type AlertRow, type EventRow, type MarketRow } from "@/lib/api";
import { severityColor } from "@/lib/colors";
import { moneyShort, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Emp = { id: string; name: string; markets: number };
type Conn = { name: string; configured: boolean; description: string; missing: string[] };
type Sig = { z: number | null; p_value: number; q_value: number; ratio: number; significant: boolean; before: number[]; after: number[]; method: string; exact?: boolean };

function useDescribe() {
  const { t } = useI18n();
  return (e: EventRow) => {
    const p = e.payload as Record<string, number | string | undefined>;
    const n = (k: string) => Number(p[k]);
    switch (e.kind) {
      case "competitor.new_brand": return t("alerts.d.new_brand", { b: e.subject ?? "", s: pct(n("share"), 1), p: String(p.position ?? "") });
      case "competitor.price_change": return t("alerts.d.price", { b: e.subject ?? "", c: (n("change") * 100).toFixed(1) });
      case "competitor.share_change": return t("alerts.d.share", { b: e.subject ?? "", c: (n("change") * 100).toFixed(1) });
      case "competitor.position_change": return t("alerts.d.position", { b: e.subject ?? "", a: String(p.before), z: String(p.after) });
      case "opportunity.change": return t("alerts.d.opportunity", { s: e.subject ?? "", a: String(p.before), z: String(p.after) });
      case "opportunity.new_segment": return t("alerts.d.new_segment", { s: e.subject ?? "", v: String(p.opportunity_score) });
      case "trend.change": return t("alerts.d.trend", { a: String(p.before), z: String(p.after) });
      case "product.new_launch": return t("alerts.d.launch", { s: truncate(e.subject ?? "", 80) });
      case "segment.demand_change": return t("alerts.d.segment", { s: e.subject ?? "" });
      case "market.size_change": return t("alerts.d.size", { s: e.subject ?? "" });
      default: return e.subject ?? e.kind;
    }
  };
}

function SigLine({ s }: { s?: Sig }) {
  const { t } = useI18n();
  if (!s) return null;
  const money = s.before[0] > 1.5;
  const f = (v: number) => (money ? moneyShort(v) : pct(v, 1));
  return (
    <div className={`mt-0.5 text-[11px] ${s.significant ? "text-good" : "text-ink-3"}`}>
      {s.significant ? t("alerts.significant") : t("alerts.notSignificant")} · {f(s.before[0])} ({f(s.before[1])}–{f(s.before[2])}) → {f(s.after[0])} ({f(s.after[1])}–{f(s.after[2])})
      · {s.exact || s.z == null ? t("alerts.exact") : `z = ${s.z.toFixed(2)}`} · q = {s.q_value.toFixed(4)}
    </div>
  );
}

export default function Alerts() {
  const mn = useMarketName();
  const { t } = useI18n();
  const describe = useDescribe();
  const emps = useApi<Emp[]>("/employees");
  const markets = useApi<MarketRow[]>("/markets");
  const [who, setWho] = useState("");
  const [market, setMarket] = useState("");
  const [showInfo, setShowInfo] = useState(false);
  const [nonce, setNonce] = useState(0);
  const alerts = useApi<AlertRow[]>(`/alerts${who ? `?employee_id=${who}` : ""}`, [who, nonce]);
  const events = useApi<EventRow[]>(`/events?limit=300${market ? `&market=${encodeURIComponent(market)}` : ""}`, [nonce, market]);
  const conns = useApi<Conn[]>("/connectors");
  const mark = async (id: string, status: string) => { await post(`/alerts/${id}`, { status }); setNonce((x) => x + 1); };
  const evs = (events.data ?? []).filter((e) => showInfo || e.severity !== "info");
  const tested = (events.data ?? []).filter((e) => (e.payload as { significance?: Sig }).significance);
  const demoted = tested.filter((e) => !(e.payload as { significance: Sig }).significance.significant).length;

  return (
    <div className="pb-8">
      <PageHeader title={t("alerts.title")} subtitle={t("alerts.subtitle")}
        right={<div className="flex gap-2">
          <Select aria-label={t("a11y.market")} value={market} onChange={(e) => setMarket(e.target.value)} className="w-48">
            <option value="">{t("alerts.allMarkets")}</option>
            {(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}
          </Select>
          <Select aria-label={t("a11y.employee")} value={who} onChange={(e) => setWho(e.target.value)} className="w-56">
            <option value="">{t("alerts.allEmployees")}</option>
            {(emps.data ?? []).filter((e) => e.markets > 0).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </Select>
        </div>} />
      <ErrorNote error={events.error} />
      <Card className="mx-6 mb-4">
        <div className="grid grid-cols-1 gap-2 px-4 py-3 text-xs text-ink-2 md:grid-cols-3">
          <div>{t("alerts.rule1")}</div><div>{t("alerts.rule2")}</div><div>{t("alerts.rule3", { n: tested.length, d: demoted })}</div>
        </div>
      </Card>
      <div className="grid grid-cols-1 gap-4 px-6 xl:grid-cols-2">
        <Card>
          <CardHeader title={t("alerts.alerts")} subtitle={t("alerts.alertsSub")} />
          {alerts.data?.length === 0 ? <Empty title={t("alerts.noAlerts")}>{t("alerts.noAlertsSub")}</Empty> : (
            <div className="max-h-[640px] divide-y divide-line overflow-y-auto scrollbar-thin">
              {(alerts.data ?? []).filter((a) => !market || a.event.market_name === market).map((a) => (
                <div key={a.id} className={`px-4 py-2.5 text-sm ${a.status === "new" ? "" : "opacity-60"}`}>
                  <div className="flex items-center gap-2">
                    <Badge color={severityColor(a.event.severity)}>{t(`alerts.sev.${a.event.severity}`)}</Badge>
                    <span className="min-w-0 flex-1 truncate">{describe(a.event)}</span>
                    {a.status === "new" && <Button size="sm" variant="ghost" onClick={() => mark(a.id, "read")}>{t("alerts.markRead")}</Button>}
                  </div>
                  <SigLine s={(a.event.payload as { significance?: Sig }).significance} />
                  <div className="mt-0.5 text-[11px] text-ink-3">{a.employee} · {a.event.market_name && <Link className="text-accent" href={`/competitors?market=${encodeURIComponent(a.event.market_name)}`}>{a.event.market_name}</Link>} · {new Date(a.event.created_at).toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card>
          <CardHeader title={t("alerts.log")} subtitle={t("alerts.logSub")}
            right={<label className="flex items-center gap-1 text-xs text-ink-3"><input type="checkbox" checked={showInfo} onChange={(e) => setShowInfo(e.target.checked)} />{t("alerts.showInfo")}</label>} />
          {evs.length === 0 ? <Empty title={t("alerts.noEvents")} /> : (
            <div className="max-h-[640px] divide-y divide-line overflow-y-auto scrollbar-thin">
              {evs.map((e) => (
                <div key={e.id} className="px-4 py-2 text-sm">
                  <div className="flex items-center gap-2"><Badge color={severityColor(e.severity)}>{t(`alerts.sev.${e.severity}`)}</Badge>
                    <code className="text-[11px] text-ink-3">{e.kind}</code><span className="min-w-0 flex-1 truncate">{describe(e)}</span></div>
                  <SigLine s={(e.payload as { significance?: Sig }).significance} />
                  <div className="text-[11px] text-ink-3">{e.market_name ?? t("alerts.allMarkets")} · {e.source} · {new Date(e.created_at).toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <Card className="mx-6 mt-4">
        <CardHeader title={t("alerts.sources")} subtitle={t("alerts.sourcesSub")} />
        <div className="grid grid-cols-1 divide-y divide-line md:grid-cols-2 md:divide-y-0">
          {(conns.data ?? []).map((c) => (
            <div key={c.name} className="px-4 py-3 text-sm">
              <div className="flex items-center gap-2"><b>{c.name}</b><Badge color={c.configured ? "var(--good)" : undefined}>{c.configured ? t("alerts.configured") : t("alerts.notConfigured")}</Badge></div>
              <div className="text-xs text-ink-3">{c.description}{!c.configured && ` — ${t("alerts.set")} ${c.missing.join(", ")}`}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
