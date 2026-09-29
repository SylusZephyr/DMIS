"use client";
import { useEffect, useState } from "react";
import { CheckCircle2, CircleSlash, Play, PlugZap, TriangleAlert } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Skeleton } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import { post } from "@/lib/api";
import { num, timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Provider = { name: string; label: string; capabilities: string[]; configured: boolean; missing: string[]; cost_usd_per_request: number | null };
type AcqStatus = { providers: Provider[]; active: Record<string, string | null>; ready: boolean; direct_html_enabled: boolean;
  run: Record<string, number>; schedule_every_days: number };
type Run = { id: string; kind: string; market_name: string | null; status: string; started_at: string; finished_at: string | null;
  providers: Record<string, string | null> | null; error: string | null;
  result: { listings?: number; badged?: number; reviews?: number; history_months?: number; notes?: string[] } | null;
  ledger: { requests?: number; cached?: number; cost_usd?: number } | null };
type Platform = { name: string; label: string; kind: string; configured: boolean; missing: string[]; currency: string | null };
type SrcStatus = { platforms: Platform[]; ready: boolean; fx: { rates_to_usd: Record<string, number>; rates_as_of: string } };

const TONE: Record<string, string> = { done: "var(--good)", partial: "var(--warn)", failed: "var(--bad)", running: "var(--accent)", queued: "var(--ink-3)" };

function StatusDot({ ok }: { ok: boolean }) {
  return ok ? <CheckCircle2 className="h-4 w-4 text-good" aria-hidden /> : <CircleSlash className="h-4 w-4 text-ink-3" aria-hidden />;
}

export default function Connectors() {
  const { t, lang } = useI18n();
  const mn = useMarketName();
  const [market] = useMarket();
  const acq = useApi<AcqStatus>("/acquire/status");
  const src = useApi<SrcStatus>("/sourcing/status");
  const runs = useApi<Run[]>("/acquire/runs?limit=30");
  const [history, setHistory] = useState(true);
  const [reviews, setReviews] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const active = (runs.data ?? []).some((r) => r.status === "running" || r.status === "queued");
  const { reload } = runs;
  useEffect(() => {
    if (!active) return;
    const h = setInterval(reload, 4000);
    return () => clearInterval(h);
  }, [active, reload]);

  const start = async () => {
    if (!market) return;
    setBusy(true); setErr(null);
    try { await post(`/acquire/markets/${encodeURIComponent(market)}/run`, { history, reviews }); runs.reload(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="pb-8">
      <PageHeader title={t("conn.title")} subtitle={t("conn.subtitle")} />
      <ErrorNote error={acq.error ?? src.error} />
      <div className="space-y-5 px-4 md:px-6">
        <Card>
          <CardHeader title={t("conn.amazon")} subtitle={t("conn.amazonSub")}
            right={acq.data && <Badge color={acq.data.ready ? "var(--good)" : "var(--warn)"}>{acq.data.ready ? t("conn.ready") : t("conn.notReady")}</Badge>} />
          <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 xl:grid-cols-4">
            {!acq.data && [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
            {acq.data?.providers.map((p) => (
              <div key={p.name} className="rounded-xl border border-line bg-panel-2/40 p-3">
                <div className="flex items-center gap-2"><StatusDot ok={p.configured} /><span className="text-sm font-semibold">{p.label}</span></div>
                <div className="mt-2 flex flex-wrap gap-1">{p.capabilities.map((c) => <Badge key={c}>{t(`conn.cap.${c}`)}</Badge>)}</div>
                {p.configured ? <p className="mt-2 text-xs text-good">{t("conn.configured")}</p> : (
                  <p className="mt-2 text-xs text-ink-3">{t("conn.missing")}: {p.missing.map((m) => <code key={m} className="mr-1 break-all font-mono text-[11px] text-ink-2">{m}</code>)}</p>)}
                {p.cost_usd_per_request != null && p.cost_usd_per_request > 0 && <p className="mt-1 text-[11px] text-ink-3">{t("conn.cost", { c: p.cost_usd_per_request.toFixed(3) })}</p>}
              </div>
            ))}
          </div>
          {acq.data && (
            <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-line px-4 py-3 text-xs text-ink-3">
              {Object.entries(acq.data.active).map(([cap, prov]) => (
                <span key={cap}>{t(`conn.cap.${cap}`)}: <b className={prov ? "text-ink" : "text-ink-3"}>{prov ?? t("conn.none")}</b></span>))}
            </div>
          )}
          {acq.data && !acq.data.direct_html_enabled && (
            <p className="flex items-start gap-2 border-t border-line px-4 py-3 text-xs text-ink-3"><TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />{t("conn.directNote")}</p>
          )}
        </Card>

        <Card>
          <CardHeader title={t("conn.runTitle")} subtitle={t("conn.runSub", { d: acq.data?.schedule_every_days ?? 7 })} />
          <div className="flex flex-wrap items-center gap-4 p-4 text-sm">
            <span>{t("mkt.label")}: <b>{market ? mn(market) : t("conn.pickMarket")}</b></span>
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} />{t("conn.history", { n: acq.data?.run.history_months ?? 12 })}</label>
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={reviews} onChange={(e) => setReviews(e.target.checked)} />{t("conn.reviews")}</label>
            <Button onClick={start} disabled={!market || busy || !acq.data?.ready} aria-busy={busy}><Play className="h-4 w-4" aria-hidden />{t("conn.runNow")}</Button>
            {acq.data && !acq.data.ready && <span className="text-xs text-ink-3">{t("conn.needProvider")}</span>}
            {acq.data && <span className="text-xs text-ink-3">{t("conn.limits", { l: acq.data.run.max_listings_per_market, b: acq.data.run.budget_usd_per_run })}</span>}
          </div>
          {err && <p role="alert" className="px-4 pb-3 text-sm text-bad">{err}</p>}
          <div className="overflow-x-auto border-t border-line" tabIndex={0}>
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase tracking-wide text-ink-3 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-2">
                <tr><th scope="col">{t("conn.when")}</th><th scope="col">{t("mkt.label")}</th><th scope="col">{t("conn.kind")}</th><th scope="col">{t("conn.status")}</th>
                  <th scope="col" className="text-right">{t("conn.listings")}</th><th scope="col" className="text-right">{t("conn.badged")}</th>
                  <th scope="col" className="text-right">{t("conn.reviewsCol")}</th><th scope="col" className="text-right">{t("conn.months")}</th>
                  <th scope="col" className="text-right">{t("conn.requests")}</th><th scope="col" className="text-right">{t("conn.costCol")}</th><th scope="col">{t("conn.notes")}</th></tr>
              </thead>
              <tbody className="divide-y divide-line [&_td]:px-3 [&_td]:py-2">
                {(runs.data ?? []).map((r) => (
                  <tr key={r.id} className="align-top">
                    <td className="whitespace-nowrap text-ink-3">{timeAgo(r.started_at, lang)}</td>
                    <td>{mn(r.market_name)}</td>
                    <td>{t(`conn.kinds.${r.kind}`)}</td>
                    <td><Badge color={TONE[r.status] ?? "var(--ink-3)"}>{t(`conn.st.${r.status}`)}</Badge></td>
                    <td className="text-right font-mono">{num(r.result?.listings)}</td>
                    <td className="text-right font-mono">{num(r.result?.badged)}</td>
                    <td className="text-right font-mono">{num(r.result?.reviews)}</td>
                    <td className="text-right font-mono">{num(r.result?.history_months)}</td>
                    <td className="text-right font-mono">{num(r.ledger?.requests)}<span className="text-ink-3"> / {num(r.ledger?.cached)}</span></td>
                    <td className="text-right font-mono">{r.ledger?.cost_usd != null ? `$${r.ledger.cost_usd.toFixed(2)}` : "—"}</td>
                    <td className="max-w-[28rem] text-ink-3">{r.error ?? (r.result?.notes ?? []).join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {runs.data?.length === 0 && <Empty title={t("conn.noRuns")}>{t("conn.noRunsSub")}</Empty>}
          </div>
        </Card>

        <Card>
          <CardHeader title={t("conn.marketplaces")} subtitle={t("conn.marketplacesSub")}
            right={src.data && <span className="text-[11px] text-ink-3">{t("conn.fx", { r: (1 / (src.data.fx.rates_to_usd.CNY || 1)).toFixed(4), d: src.data.fx.rates_as_of })}</span>} />
          <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 xl:grid-cols-5">
            {!src.data && [0, 1, 2].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
            {src.data?.platforms.map((p) => (
              <div key={p.name} className="rounded-xl border border-line bg-panel-2/40 p-3">
                <div className="flex items-center gap-2"><StatusDot ok={p.configured} /><span className="text-sm font-semibold">{p.label}</span></div>
                <p className="mt-1 text-[11px] text-ink-3">{p.currency} · {t(`conn.pkind.${p.kind}`)}</p>
                {!p.configured && <p className="mt-2 text-xs text-ink-3">{t("conn.missing")}: {p.missing.map((m) => <code key={m} className="mr-1 break-all font-mono text-[11px] text-ink-2">{m}</code>)}</p>}
              </div>
            ))}
          </div>
          <p className="flex items-center gap-2 border-t border-line px-4 py-3 text-xs text-ink-3"><PlugZap className="h-3.5 w-3.5 text-accent" aria-hidden />{t("conn.docs")}</p>
        </Card>
      </div>
    </div>
  );
}
