"use client";
import Link from "next/link";
import { useState } from "react";
import { Eye, RefreshCw, Star } from "lucide-react";
import { Badge, Button, Card, CardHeader } from "@/components/ui/primitives";
import { post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

/** "Refresh from Amazon" for a market: starts a live acquisition when a provider is configured, else says how. */
export function LiveRefresh({ market }: { market: string }) {
  const { t } = useI18n();
  const st = useApi<{ ready: boolean }>("/acquire/status");
  const [state, setState] = useState<"" | "busy" | "started" | "error">("");
  const [msg, setMsg] = useState<string | null>(null);
  if (!st.data) return null;
  if (!st.data.ready) {
    return <Link href="/connectors" className="inline-flex items-center gap-1 text-xs text-ink-3 hover:text-accent" title={t("live.setupTip")}>
      <RefreshCw className="h-3.5 w-3.5" aria-hidden />{t("live.setup")}</Link>;
  }
  const go = async () => {
    setState("busy"); setMsg(null);
    try { await post(`/acquire/markets/${encodeURIComponent(market)}/run`, { history: false, reviews: true }); setState("started"); }
    catch (e) { setState("error"); setMsg((e as Error).message); }
  };
  return (
    <span className="inline-flex items-center gap-2">
      <button type="button" onClick={go} disabled={state === "busy" || state === "started"}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-accent/50 px-2.5 text-xs text-accent hover:bg-accent/10 disabled:opacity-60">
        <RefreshCw className={`h-3.5 w-3.5 ${state === "busy" ? "animate-spin" : ""}`} aria-hidden />{t("live.refresh")}</button>
      {state === "started" && <Link href="/connectors" className="text-xs text-good hover:underline">{t("live.started")}</Link>}
      {state === "error" && <span role="alert" className="text-xs text-bad">{msg}</span>}
    </span>
  );
}

type Rev = { review_id: string; rating: number | null; title: string | null; text: string; date: string | null; verified: boolean | null; helpful: number | null };

/** Review text acquired live for one listing (shown only when some exists). */
export function LiveReviews({ market, asin }: { market: string; asin: string | null | undefined }) {
  const { t } = useI18n();
  const r = useApi<{ reviews: Rev[]; total: number }>(asin ? `/markets/${encodeURIComponent(market)}/acquired-reviews?asin=${encodeURIComponent(asin)}&limit=20` : null, [asin]);
  if (!r.data || r.data.total === 0) return null;
  return (
    <Card>
      <CardHeader title={t("live.reviews")} subtitle={t("live.reviewsSub", { n: r.data.total })} />
      <ul className="max-h-[28rem] divide-y divide-line overflow-y-auto">
        {r.data.reviews.map((x) => (
          <li key={x.review_id} className="px-4 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-0.5 font-mono text-xs" aria-label={`${x.rating ?? "—"} / 5`}>
                <Star className="h-3.5 w-3.5 text-warn" aria-hidden />{x.rating ?? "—"}</span>
              {x.title && <span className="font-medium">{x.title}</span>}
              {x.verified && <Badge color="var(--good)">{t("live.verified")}</Badge>}
              {x.date && <span className="text-[11px] text-ink-3">{x.date}</span>}
            </div>
            <p className="mt-1 text-ink-2">{x.text}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Follow a listing on the competitor watchlist (shows "Watching" once it is on the list). */
export function WatchButton({ asin, market, label }: { asin: string | null | undefined; market: string; label?: string | null }) {
  const { t } = useI18n();
  const list = useApi<{ items: { asin: string; market_name: string | null }[] }>(asin ? "/watchlist" : null);
  const [busy, setBusy] = useState(false);
  if (!asin || asin.length !== 10) return null;
  const on = (list.data?.items ?? []).some((i) => i.asin === asin && i.market_name === market);
  if (on) return <Link href="/watchlist" className="inline-flex items-center gap-1 text-xs text-good hover:underline"><Eye className="h-3.5 w-3.5" aria-hidden />{t("watch.watching")}</Link>;
  const add = async () => {
    setBusy(true);
    try { await post("/watchlist", { asin, market, label: label ? label.slice(0, 120) : null }); list.reload(); } finally { setBusy(false); }
  };
  return (
    <Button size="sm" variant="outline" onClick={add} disabled={busy || !list.data} aria-busy={busy}>
      <Eye className="h-3.5 w-3.5" aria-hidden />{t("watch.watchBtn")}
    </Button>
  );
}
