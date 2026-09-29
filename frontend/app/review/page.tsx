"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, Input, Meter, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { useMarket } from "@/components/market-switcher";
import type { MarketRow } from "@/lib/api";
import { apiPost, type Schemas } from "@/lib/api-typed";
import { money, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Dups = Schemas["DuplicatePage"];
type Dup = Schemas["DuplicateCandidate"];
type Evaluation = Schemas["IdentityEvaluation"];
type Taxonomy = Schemas["TaxonomyResponse"];
type Records = Schemas["ListingKnowledgePage"];
type Anomalies = Schemas["AnomalyResponse"];
type Tab = "duplicates" | "taxonomy" | "relevance" | "anomalies";
const SEV_COLOR: Record<string, string> = { high: "var(--bad)", medium: "var(--warn)", low: "var(--ink-3)" };

const BAND_COLOR: Record<string, string> = {
  strong: "var(--good)", probable: "var(--good)", review: "var(--warn)", probably_non_dental: "var(--bad)",
  non_dental: "var(--bad)", verified_dental: "var(--good)", verified_non_dental: "var(--bad)",
};

function DuplicatePair({ market, d, onDone }: { market: string; d: Dup; onDone: (msg: string) => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "merge" | "keep_separate" | "needs_evidence") => {
    setBusy(true);
    try {
      await apiPost("/markets/{market}/duplicates/decision", { listing_a: d.listing_a, listing_b: d.listing_b, decision }, { market });
      onDone(t("kn.saved"));
    } catch (e) {
      onDone((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const side = (title?: string | null, brand?: string | null, price?: number | null, conf?: string | null, id?: string) => (
    <div className="min-w-0 space-y-1 rounded-lg border border-line p-3 text-sm">
      <div className="line-clamp-3 text-ink">{title}</div>
      <div className="text-xs text-ink-3">{brand ?? "—"} · {money(price)} · <span className="font-mono">{id}</span></div>
      {conf && <div className="text-xs text-ink-3">{t("kn.configuration")}: <span className="font-mono">{conf}</span></div>}
    </div>
  );
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 px-4 pt-3 text-xs text-ink-3">
        <span>{t("kn.similarity")} <span className="font-mono tabular-nums text-ink">{pct(d.score)}</span></span>
        <Badge>{d.system_decision}</Badge>
        {d.same_image && <Badge color="var(--accent)">{t("kn.sameImage")}</Badge>}
        <Badge color={d.status === "pending" ? "var(--warn)" : "var(--good)"}>{d.status === "pending" ? t("kn.pending") : d.status}</Badge>
        <span className="truncate">{d.reason}</span>
      </div>
      <div className="grid gap-3 p-4 md:grid-cols-2">
        {side(d.title_a, d.brand_a, d.price_a, d.configuration_a, d.listing_a)}
        {side(d.title_b, d.brand_b, d.price_b, d.configuration_b, d.listing_b)}
      </div>
      <div className="flex flex-wrap gap-2 px-4 pb-4">
        <Button size="sm" disabled={busy} onClick={() => decide("merge")}>{t("kn.merge")}</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => decide("keep_separate")}>{t("kn.keepSeparate")}</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => decide("needs_evidence")}>{t("kn.needsEvidence")}</Button>
      </div>
    </Card>
  );
}

function TaxonomyNodeRow({ market, n, onDone }: { market: string; n: Taxonomy["nodes"][number]; onDone: (msg: string) => void }) {
  const { t } = useI18n();
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "approved" | "rejected" | "renamed") => {
    setBusy(true);
    try {
      await apiPost("/markets/{market}/taxonomy/decision", { node_key: n.node_key, decision, label: decision === "renamed" ? label : null }, { market });
      onDone(t("kn.saved"));
    } catch (e) {
      onDone((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="space-y-2 border-b border-line px-4 py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium" style={{ paddingLeft: `${(n.depth - 1) * 0.9}rem` }}>{n.approved_label ?? n.label}</span>
        <Badge color={n.status === "machine" ? "var(--ink-3)" : n.status === "rejected" ? "var(--bad)" : "var(--good)"}>{n.status}</Badge>
        <span className="text-xs text-ink-3">{num(n.products)} {t("kn.products").toLowerCase()} · {money(n.price_median)}</span>
      </div>
      {n.explanation && <p className="text-xs text-ink-3">{n.explanation}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => decide("approved")}>{t("kn.approve")}</Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => decide("rejected")}>{t("kn.reject")}</Button>
        <Input aria-label={t("kn.newLabel")} placeholder={t("kn.newLabel")} value={label} onChange={(e) => setLabel(e.target.value)} className="h-8 w-48" />
        <Button size="sm" variant="ghost" disabled={busy || !label.trim()} onClick={() => decide("renamed")}>{t("kn.rename")}</Button>
      </div>
    </li>
  );
}

function View() {
  const mn = useMarketName();
  const { t } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const markets = useApi<MarketRow[]>("/markets");
  const [globalMarket] = useMarket();
  const names = (markets.data ?? []).map((m) => m.name);
  const market = params.get("market") ?? (names.includes(globalMarket) ? globalMarket : names[0] ?? "");
  const enc = encodeURIComponent(market);
  const [tab, setTab] = useState<Tab>("duplicates");
  const [note, setNote] = useState<string | null>(null);
  const dups = useApi<Dups>(market && tab === "duplicates" ? `/markets/${enc}/duplicates?status=pending&limit=50` : null, [market, tab]);
  const ev = useApi<Evaluation>(market && tab === "duplicates" ? `/markets/${enc}/identity/evaluation` : null, [market, tab]);
  const tax = useApi<Taxonomy>(market && tab === "taxonomy" ? `/markets/${enc}/taxonomy` : null, [market, tab]);
  const rec = useApi<Records>(market && tab === "relevance" ? `/markets/${enc}/knowledge/records?needs_review=true&limit=60` : null, [market, tab]);
  const anom = useApi<Anomalies>(market && tab === "anomalies" ? `/markets/${enc}/anomalies?limit=200` : null, [market, tab]);
  const done = (msg: string) => { setNote(msg); dups.reload(); ev.reload(); tax.reload(); };

  return (
    <div className="space-y-5 p-4 md:p-6">
      <PageHeader title={t("kn.reviewTitle")} subtitle={t("kn.reviewSub")}
        right={<Select aria-label={t("mkt.label")} value={market} onChange={(e) => router.push(`/review?market=${encodeURIComponent(e.target.value)}`)}>
          {names.map((n) => <option key={n} value={n}>{mn(n)}</option>)}
        </Select>} />
      <ErrorNote error={markets.error ?? dups.error ?? tax.error ?? rec.error ?? anom.error} />
      <div className="flex flex-wrap gap-2" role="tablist">
        {(["duplicates", "taxonomy", "relevance", "anomalies"] as const).map((k) => (
          <Button key={k} role="tab" aria-selected={tab === k} size="sm" variant={tab === k ? "primary" : "outline"} onClick={() => { setTab(k); setNote(null); }}>
            {t(`kn.tabs.${k}`)}
          </Button>
        ))}
      </div>
      {note && <p role="status" className="text-sm text-ink-2">{note}</p>}

      {tab === "duplicates" && (
        <div className="space-y-4">
          {ev.data && (
            <Card>
              <CardHeader title={t("kn.evaluation")} subtitle={`${num(ev.data.labelled_pairs)} ${t("kn.labelled")}`}
                right={<span className="font-mono text-sm tabular-nums">{t("kn.precision")} {pct(ev.data.precision)} · {t("kn.recall")} {pct(ev.data.recall)}</span>} />
            </Card>
          )}
          {(dups.data?.rows ?? []).length === 0 && !dups.loading ? <Card><Empty title={t("kn.noDuplicates")} /></Card> :
            (dups.data?.rows ?? []).map((d) => <DuplicatePair key={`${d.listing_a}|${d.listing_b}`} market={market} d={d} onDone={done} />)}
        </div>
      )}

      {tab === "taxonomy" && (
        <Card>
          <CardHeader title={t("kn.taxonomy")} />
          {(tax.data?.nodes ?? []).length === 0 ? <Empty title={t("kn.noNodes")} /> : (
            <ul>{(tax.data?.nodes ?? []).map((n) => <TaxonomyNodeRow key={n.node_key} market={market} n={n} onDone={done} />)}</ul>
          )}
        </Card>
      )}

      {tab === "relevance" && (
        <Card>
          <CardHeader title={t("kn.tabs.relevance")} subtitle={rec.data ? `${num(rec.data.total)}` : undefined} />
          {(rec.data?.rows ?? []).length === 0 ? <Empty title={t("kn.noRelevance")} /> : (
            <ul className="divide-y divide-line">
              {(rec.data?.rows ?? []).map((r) => (
                <li key={`${r.id}|${r.record_id}`} className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_16rem]">
                  <div className="min-w-0 space-y-1">
                    <div className="text-sm text-ink">{r.title}</div>
                    <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
                      <Badge color={BAND_COLOR[r.dental_band ?? ""] ?? "var(--ink-3)"}>{t(`kn.band.${r.dental_band ?? "review"}`)}</Badge>
                      <span>{t("kn.application")}: {r.primary_application}</span>
                      {(r.attribute_conflicts ?? []).length > 0 && <Badge color="var(--warn)">{t("kn.conflicts")}: {(r.attribute_conflicts ?? []).join(", ")}</Badge>}
                    </div>
                  </div>
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-xs"><span className="text-ink-3">{t("kn.dental")}</span>
                      <span className="font-mono tabular-nums">{num(r.dental_confidence)}</span></div>
                    {Object.entries((r.dental_components ?? {}) as Record<string, number | null>).map(([k, v]) => (
                      <div key={k} className="grid grid-cols-[6rem_1fr] items-center gap-2 text-[11px]">
                        <span className="text-ink-3">{t(`kn.component.${k}`)}</span>
                        {v == null ? <span className="text-ink-3">{t("kn.unknown")}</span> : <Meter value={v} />}
                      </div>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {tab === "anomalies" && (
        <Card>
          <CardHeader title={t("kn.tabs.anomalies")} subtitle={t("kn.anomalyNote")}
            right={<div className="flex flex-wrap gap-1">{Object.entries(anom.data?.counts ?? {}).map(([k, v]) =>
              <Badge key={k} color="var(--ink-3)">{t(`kn.anomaly.${k}`)} · {num(v)}</Badge>)}</div>} />
          {(anom.data?.rows ?? []).length === 0 ? <Empty title={t("kn.noAnomalies")} /> : (
            <ul className="divide-y divide-line">
              {(anom.data?.rows ?? []).map((a, i) => (
                <li key={`${a.entity_id}|${a.code}|${i}`} className="grid gap-2 px-4 py-2.5 md:grid-cols-[minmax(0,1fr)_14rem]">
                  <div className="min-w-0">
                    <div className="truncate text-sm text-ink" title={a.title ?? a.entity_id}>{a.title ?? a.entity_id}</div>
                    <div className="text-xs text-ink-3">{a.detail}</div>
                  </div>
                  <div className="flex items-center justify-end gap-2 text-xs">
                    <Badge color={SEV_COLOR[a.severity] ?? "var(--ink-3)"}>{t(`kn.anomaly.${a.code}`)}</Badge>
                    <span className="font-mono tabular-nums">{a.value != null ? num(a.value, 2) : "—"}
                      {a.expected != null && <span className="text-ink-3"> / {num(a.expected, 2)}</span>}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

export default function ReviewPage() {
  return <Suspense><View /></Suspense>;
}
