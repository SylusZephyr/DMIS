"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import { AlertTriangle, Check, Copy, ExternalLink, PackageSearch, Search, Send, Sparkles } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input, LiveStatus, Select, Skeleton } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { EChart, axis } from "@/components/charts/echart";
import { useMarket } from "@/components/market-switcher";
import { ScoreRing } from "@/components/v2/motion";
import { post } from "@/lib/api";
import { money, num, pct, timeAgo, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";
import { CATEGORICAL, seq } from "@/lib/viz";

type Concept = { market: string; label: string; scope: string | null; scope_id: string | null; target_price: number | null;
  first_order_qty: number; expected_units_month: number | null; unit_weight_g: number | null; fba_fee: number | null;
  max_fob: number | null; required_certs: string[]; must_have: { attribute: string; value: unknown }[]; features: string[];
  queries_en: string[]; terms_zh: string[]; basis: string[]; image_urls: string[] };
type OfferRow = { platform: string; offer_id: string; title: string; title_en: string | null; url: string | null; image: string | null;
  currency: string; price: number | null; price_usd: number | null; moq: number | null; sold: number | null; supplier: string | null;
  location: string | null; years: number | null; verified: boolean | null; trade_assurance: boolean | null; rating: number | null;
  certifications: string[]; fit: number; unit_usd: number | null; landed_usd: number | null; margin: number | null;
  components: Record<string, number | null>; score: number | null; coverage: number; pareto: boolean; match: boolean;
  reasons: string[]; flags: string[] };
type Supplier = { key: string; name: string; platforms: string[]; offers: number; matching_offers: number; location: string | null;
  years: number | null; verified: boolean | null; certifications: string[]; best_offer: OfferRow; score: number | null };
type RunResult = { offers: number; matching: number; pareto: number; suppliers: number; platforms: Record<string, number>;
  best: OfferRow | null; shortlist: Supplier[]; notes: string[]; fx_rates_as_of: string };
type RunRow = { id: string; market_name: string | null; status: string; started_at: string; concept: Concept | null;
  queries: Record<string, string[]> | null; result: RunResult | null; error: string | null };
type Seg = { segment_id: string; segment_label: string };

const PLATFORMS = ["1688", "alibaba", "taobao", "aliexpress", "made_in_china"];
const platformColor = (p: string) => CATEGORICAL[Math.max(0, PLATFORMS.indexOf(p)) % CATEGORICAL.length];

function Fact({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-panel-2/40 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-3">{label}</div>
      <div className="mt-0.5 font-mono text-base text-ink">{value}</div>
      {sub && <div className="text-[11px] text-ink-3">{sub}</div>}
    </div>
  );
}

function RfqDialog({ runId, offer, onClose }: { runId: string; offer: OfferRow; onClose: () => void }) {
  const { t } = useI18n();
  const [res, setRes] = useState<{ rfq: { en: string; zh: string }; supplier: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lang, setLang] = useState<"en" | "zh">("zh");
  const [copied, setCopied] = useState(false);
  const send = async () => {
    setErr(null);
    try { setRes(await post(`/sourcing/runs/${runId}/reach-out`, { platform: offer.platform, offer_id: offer.offer_id })); }
    catch (e) { setErr((e as Error).message); }
  };
  const copy = async () => {
    if (!res) return;
    try { await navigator.clipboard.writeText(res.rfq[lang]); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label={t("src.reachOut")} onMouseDown={(e) => e.stopPropagation()}
        className="fade-up w-full max-w-2xl rounded-2xl border border-line bg-panel shadow-2xl">
        <CardHeader title={t("src.reachOut")} subtitle={`${offer.supplier ?? "—"} · ${offer.platform}`}
          right={<button type="button" onClick={onClose} className="text-ink-3 hover:text-ink" aria-label={t("nav.close")}>✕</button>} />
        <div className="space-y-3 p-4 text-sm">
          {!res ? (
            <>
              <p className="text-ink-2">{t("src.reachOutSub")}</p>
              {err && <p role="alert" className="text-bad">{err}</p>}
              <Button onClick={send}><Send className="h-4 w-4" aria-hidden />{t("src.createInquiry")}</Button>
            </>
          ) : (
            <>
              <p className="flex items-center gap-2 text-good"><Check className="h-4 w-4" aria-hidden />{t("src.recorded", { s: res.supplier })}</p>
              <div className="flex gap-1" role="tablist">
                {(["zh", "en"] as const).map((l) => (
                  <Button key={l} role="tab" aria-selected={lang === l} size="sm" variant={lang === l ? "primary" : "outline"} onClick={() => setLang(l)}>
                    {l === "zh" ? "中文" : "English"}</Button>))}
                <Button size="sm" variant="ghost" onClick={copy} className="ml-auto"><Copy className="h-4 w-4" aria-hidden />{copied ? t("src.copied") : t("src.copy")}</Button>
              </div>
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-panel-2/60 p-3 font-sans text-sm text-ink-2">{res.rfq[lang]}</pre>
              {offer.url && <a href={offer.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">{t("src.openOffer")}<ExternalLink className="h-3.5 w-3.5" aria-hidden /></a>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Workspace() {
  const { t, lang } = useI18n();
  const mn = useMarketName();
  const router = useRouter();
  const sp = useSearchParams();
  const [ctxMarket] = useMarket();
  const market = sp.get("market") || ctxMarket;
  const [scopeId, setScopeId] = useState(sp.get("id") ?? "");
  const [text, setText] = useState("");
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("");
  const [concept, setConcept] = useState<Concept | null>(null);
  const [busy, setBusy] = useState<"" | "preview" | "run">("");
  const [err, setErr] = useState<string | null>(null);
  const [matchOnly, setMatchOnly] = useState(true);
  const [rfqFor, setRfqFor] = useState<OfferRow | null>(null);
  const runId = sp.get("run");
  const status = useApi<{ ready: boolean; platforms: { name: string; configured: boolean }[] }>("/sourcing/status");
  const segs = useApi<Seg[]>(market ? `/markets/${encodeURIComponent(market)}/segments` : null, [market]);
  const run = useApi<RunRow>(runId ? `/sourcing/runs/${runId}` : null, [runId]);
  const offers = useApi<{ offers: OfferRow[] }>(runId && run.data?.status !== "running" ? `/sourcing/runs/${runId}/offers` : null, [runId, run.data?.status]);
  const history = useApi<RunRow[]>(market ? `/sourcing/runs?market=${encodeURIComponent(market)}&limit=10` : "/sourcing/runs?limit=10", [market, runId]);

  const running = run.data?.status === "running";
  const { reload: reloadRun } = run;
  useEffect(() => {
    if (!running) return;
    const h = setInterval(reloadRun, 3000);
    return () => clearInterval(h);
  }, [running, reloadRun]);

  const body = () => ({ market, ...(scopeId ? { scope: "segment", scope_id: scopeId } : { text }),
    target_price: price ? Number(price) : null, qty: qty ? Number(qty) : null });
  const preview = async () => {
    setBusy("preview"); setErr(null);
    try { setConcept(await post<Concept>("/sourcing/concept", body())); } catch (e) { setErr((e as Error).message); } finally { setBusy(""); }
  };
  const search = async () => {
    setBusy("run"); setErr(null);
    try {
      const r = await post<{ id: string }>("/sourcing/runs", body());
      if (r.id) router.replace(`/sourcing?${new URLSearchParams({ market, run: r.id })}`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(""); }
  };

  const res = run.data?.result ?? null;
  const shown = useMemo(() => (offers.data?.offers ?? []).filter((o) => !matchOnly || o.match), [offers.data, matchOnly]);
  const chart = useMemo<EChartsOption>(() => {
    const pts = (offers.data?.offers ?? []).filter((o) => o.match && o.landed_usd != null);
    const plats = PLATFORMS.filter((p) => pts.some((o) => o.platform === p));
    return {
      grid: { left: 56, right: 20, top: 36, bottom: 48 },
      legend: { top: 0, textStyle: { color: "var(--ink-2)" }, itemWidth: 10, itemHeight: 10 },
      tooltip: { trigger: "item", formatter: (p: unknown) => {
        const o = (p as { data: { raw: OfferRow } }).data.raw;
        return `<b>${truncate(o.title_en || o.title, 60)}</b><br/>${o.supplier ?? ""} · ${o.platform}<br/>${t("src.landed")} ${money(o.landed_usd, 2)} · ` +
          `${t("src.reliability")} ${o.components.reliability?.toFixed(0) ?? "—"} · ${t("src.fit")} ${pct(o.fit, 0)}` +
          `${o.margin != null ? `<br/>${t("src.margin")} ${pct(o.margin, 0)}` : ""}${o.pareto ? `<br/>★ ${t("src.paretoOne")}` : ""}`;
      } },
      xAxis: { ...axis, type: "value", name: t("src.landedAxis"), nameLocation: "middle", nameGap: 30, axisLabel: { color: "var(--ink-3)", formatter: (v: number) => `$${v}` } },
      yAxis: { ...axis, type: "value", name: t("src.reliability"), min: 0, max: 100 },
      series: plats.map((p) => ({
        name: p, type: "scatter" as const, itemStyle: { color: platformColor(p), borderColor: "var(--panel)", borderWidth: 2 },
        emphasis: { scale: 1.4 },
        data: pts.filter((o) => o.platform === p).map((o) => ({
          value: [o.landed_usd as number, o.components.reliability ?? 0], symbolSize: 8 + 16 * o.fit, raw: o,
          itemStyle: o.pareto ? { borderColor: "var(--ink)", borderWidth: 2 } : undefined })),
      })),
    };
  }, [offers.data, t]);

  const best = res?.best ?? null;
  const c = concept ?? run.data?.concept ?? null;
  return (
    <div className="pb-8">
      <PageHeader title={t("src.title")} subtitle={t("src.subtitle")} />
      <ErrorNote error={status.error} />
      <div className="grid grid-cols-1 gap-5 px-4 md:px-6 xl:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <div className="space-y-5">
          <Card>
            <CardHeader title={t("src.concept")} subtitle={market ? mn(market) : t("conn.pickMarket")} />
            <div className="space-y-3 p-4 text-sm">
              <label className="block">{t("src.fromSegment")}
                <Select className="mt-1 w-full" value={scopeId} onChange={(e) => setScopeId(e.target.value)} disabled={!market}>
                  <option value="">{t("src.orText")}</option>
                  {(segs.data ?? []).map((s) => <option key={s.segment_id} value={s.segment_id}>{truncate(s.segment_label, 60)}</option>)}
                </Select></label>
              {!scopeId && <label className="block">{t("src.idea")}<Input className="mt-1" value={text} onChange={(e) => setText(e.target.value)} placeholder={t("src.ideaPh")} /></label>}
              <div className="grid grid-cols-2 gap-2">
                <label className="block">{t("src.targetPrice")}<Input className="mt-1" type="number" min={0} value={price} onChange={(e) => setPrice(e.target.value)} placeholder={t("src.auto")} /></label>
                <label className="block">{t("src.qty")}<Input className="mt-1" type="number" min={1} value={qty} onChange={(e) => setQty(e.target.value)} placeholder={t("src.auto")} /></label>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={preview} disabled={!market || (!scopeId && !text.trim()) || !!busy}><Sparkles className="h-4 w-4" aria-hidden />{t("src.preview")}</Button>
                <Button onClick={search} disabled={!market || (!scopeId && !text.trim()) || !!busy || !status.data?.ready} aria-busy={busy === "run"}>
                  <Search className="h-4 w-4" aria-hidden />{t("src.search")}</Button>
              </div>
              <LiveStatus busy={!!busy} done={!!concept || !!res} busyText={t("a11y.loading")} doneText={t("a11y.loaded")} />
              {status.data && !status.data.ready && <p className="text-xs text-ink-3">{t("src.notConfigured")}</p>}
              {err && <p role="alert" className="text-sm text-bad">{err}</p>}
            </div>
          </Card>
          <Card>
            <CardHeader title={t("src.history")} />
            <ul className="divide-y divide-line">
              {(history.data ?? []).length === 0 && <li><Empty title={t("src.noRuns")} /></li>}
              {(history.data ?? []).map((h) => (
                <li key={h.id}>
                  <button type="button" onClick={() => router.replace(`/sourcing?${new URLSearchParams({ market: h.market_name ?? "", run: h.id })}`)}
                    className={`w-full px-4 py-2 text-left text-sm hover:bg-panel-2/60 ${h.id === runId ? "bg-accent/10" : ""}`}>
                    <span className="block truncate">{h.concept?.label ?? "—"}</span>
                    <span className="text-[11px] text-ink-3">{timeAgo(h.started_at, lang)} · {t(`conn.st.${h.status}`)} · {num(h.result?.offers)} {t("src.offers")}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <div className="min-w-0 space-y-5">
          {c && (
            <Card>
              <CardHeader title={c.label} subtitle={t("src.conceptSub")} />
              <div className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-2 p-4">
                <Fact label={t("src.targetPrice")} value={money(c.target_price, 2)} />
                <Fact label={t("src.qty")} value={num(c.first_order_qty)} sub={c.expected_units_month ? t("src.perMonth", { n: num(c.expected_units_month, 0) }) : undefined} />
                <Fact label={t("src.maxFob")} value={money(c.max_fob, 2)} />
                <Fact label={t("src.fba")} value={money(c.fba_fee, 2)} />
                <Fact label={t("src.weight")} value={c.unit_weight_g ? `${num(c.unit_weight_g, 0)} g` : "—"} />
                <Fact label={t("src.certs")} value={<span className="font-sans text-sm">{c.required_certs.join(", ") || "—"}</span>} />
              </div>
              <div className="space-y-2 border-t border-line px-4 py-3 text-xs">
                {c.must_have.length > 0 && <div className="flex flex-wrap items-center gap-1"><span className="text-ink-3">{t("src.mustHave")}:</span>
                  {c.must_have.map((m) => <Badge key={m.attribute} color="var(--accent)">{m.attribute}: {String(m.value)}</Badge>)}</div>}
                {c.features.length > 0 && <div className="flex flex-wrap items-center gap-1"><span className="text-ink-3">{t("src.features")}:</span>
                  {c.features.map((f) => <Badge key={f}>{f}</Badge>)}</div>}
                <div><span className="text-ink-3">EN:</span> {c.queries_en.join(" · ")}</div>
                <div lang="zh-CN"><span className="text-ink-3">中文:</span> {c.terms_zh.join(" · ")}</div>
                <div className="text-ink-3">{c.basis.join(" · ")}</div>
              </div>
            </Card>
          )}

          {runId && run.data?.status === "running" && <Card className="p-6"><Skeleton className="h-6 w-1/2" /><p className="mt-3 text-sm text-ink-3">{t("src.running")}</p></Card>}
          {res && res.notes.length > 0 && (
            <p className="flex items-start gap-2 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{res.notes.join(" · ")}</p>)}

          {best && (
            <Card className="overflow-hidden">
              <CardHeader title={t("src.best")} subtitle={t("src.bestSub", { o: res?.offers ?? 0, m: res?.matching ?? 0, s: res?.suppliers ?? 0 })} />
              <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-[8rem_minmax(0,1fr)_auto]">
                <div className="flex h-32 w-32 items-center justify-center overflow-hidden rounded-xl border border-line bg-panel-2">
                  {best.image ? <img src={best.image} alt="" className="max-h-full object-contain" onError={(e) => { e.currentTarget.style.display = "none"; }} />
                    : <PackageSearch className="h-8 w-8 text-ink-3" aria-hidden />}
                </div>
                <div className="min-w-0">
                  <h2 className="text-base font-semibold leading-snug">{best.title_en || best.title}</h2>
                  {best.title_en && <p className="text-xs text-ink-3" lang="zh-CN">{best.title}</p>}
                  <p className="mt-1 text-sm text-ink-2">{best.supplier ?? "—"} <span className="text-ink-3">· <span style={{ color: platformColor(best.platform) }}>●</span> {best.platform}
                    {best.location ? ` · ${best.location}` : ""}{best.years ? ` · ${t("src.years", { n: best.years })}` : ""}</span></p>
                  <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <Fact label={t("src.unit")} value={money(best.unit_usd, 2)} sub={`${best.price ?? "—"} ${best.currency}`} />
                    <Fact label={t("src.landed")} value={money(best.landed_usd, 2)} />
                    <Fact label={t("src.margin")} value={best.margin != null ? pct(best.margin, 0) : "—"} />
                    <Fact label={t("src.moq")} value={num(best.moq)} />
                  </div>
                  <ul className="mt-3 space-y-1 text-sm">
                    {best.reasons.map((r) => <li key={r} className="flex gap-2 text-ink-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-good" aria-hidden />{r}</li>)}
                    {best.flags.map((r) => <li key={r} className="flex gap-2 text-warn"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{r}</li>)}
                  </ul>
                </div>
                <div className="flex flex-col items-center gap-3">
                  <ScoreRing score={best.score} size={64} color={seq((best.score ?? 0) / 100)} label={t("src.score")} />
                  <Button onClick={() => setRfqFor(best)}><Send className="h-4 w-4" aria-hidden />{t("src.reachOut")}</Button>
                  {best.url && <a href={best.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">{t("src.openOffer")}<ExternalLink className="h-3 w-3" aria-hidden /></a>}
                </div>
              </div>
            </Card>
          )}

          {res && (offers.data?.offers ?? []).some((o) => o.match && o.landed_usd != null) && (
            <Card>
              <CardHeader title={t("src.pareto")} subtitle={t("src.paretoSub")} />
              <div className="px-2 pb-2"><EChart option={chart} height={320} label={t("src.pareto")} /></div>
            </Card>
          )}

          {res && res.shortlist.length > 0 && (
            <Card>
              <CardHeader title={t("src.shortlist")} subtitle={t("src.shortlistSub")} />
              <div className="overflow-x-auto" tabIndex={0}>
                <table className="w-full text-xs">
                  <thead className="text-left text-[10px] uppercase tracking-wide text-ink-3 [&_th]:whitespace-nowrap [&_th]:px-3 [&_th]:py-2">
                    <tr><th scope="col">{t("src.supplier")}</th><th scope="col">{t("src.platforms")}</th><th scope="col" className="text-right">{t("src.landed")}</th>
                      <th scope="col" className="text-right">{t("src.margin")}</th><th scope="col" className="text-right">{t("src.moq")}</th>
                      <th scope="col">{t("src.certs")}</th><th scope="col" className="text-right">{t("src.score")}</th><th scope="col" /></tr>
                  </thead>
                  <tbody className="divide-y divide-line [&_td]:px-3 [&_td]:py-2">
                    {res.shortlist.map((s) => (
                      <tr key={s.key}>
                        <td className="max-w-[16rem]"><div className="truncate font-medium" title={s.name}>{s.name}</div>
                          <div className="text-[11px] text-ink-3">{[s.location, s.years ? t("src.years", { n: s.years }) : null, s.verified ? t("src.verified") : null].filter(Boolean).join(" · ")}</div></td>
                        <td><div className="flex flex-wrap gap-1">{s.platforms.map((p) => <Badge key={p} color={platformColor(p)}>{p}</Badge>)}</div></td>
                        <td className="text-right font-mono">{money(s.best_offer.landed_usd, 2)}</td>
                        <td className="text-right font-mono">{s.best_offer.margin != null ? pct(s.best_offer.margin, 0) : "—"}</td>
                        <td className="text-right font-mono">{num(s.best_offer.moq)}</td>
                        <td className="text-ink-3">{s.certifications.join(", ") || "—"}</td>
                        <td className="text-right font-mono">{s.score?.toFixed(0) ?? "—"}</td>
                        <td><Button size="sm" variant="outline" onClick={() => setRfqFor(s.best_offer)}>{t("src.reachOut")}</Button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {res && (
            <Card>
              <CardHeader title={t("src.allOffers", { n: shown.length })} subtitle={Object.entries(res.platforms).map(([p, n]) => `${p} ${n}`).join(" · ")}
                right={<label className="inline-flex items-center gap-2 text-xs"><input type="checkbox" checked={matchOnly} onChange={(e) => setMatchOnly(e.target.checked)} />{t("src.matchOnly")}</label>} />
              <ul className="divide-y divide-line">
                {!offers.data && <li className="p-4"><Skeleton className="h-5 w-full" /></li>}
                {shown.slice(0, 60).map((o) => (
                  <li key={`${o.platform}:${o.offer_id}`} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 px-4 py-2.5 text-sm">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2"><span className="h-2 w-2 shrink-0 rounded-full" style={{ background: platformColor(o.platform) }} aria-hidden />
                        <span className="truncate" title={o.title}>{o.title_en || o.title}</span>{o.pareto && <Badge color="var(--accent)">{t("src.paretoOne")}</Badge>}
                        {!o.match && <Badge color="var(--ink-3)">{t("src.noMatch")}</Badge>}</div>
                      <div className="text-[11px] text-ink-3">{o.supplier ?? "—"} · {o.platform} · {t("src.fit")} {pct(o.fit, 0)}{o.flags.length ? ` · ⚠ ${o.flags[0]}` : ""}</div>
                    </div>
                    <div className="text-right font-mono text-xs">{money(o.landed_usd, 2)}<div className="text-ink-3">{o.margin != null ? pct(o.margin, 0) : "—"} · {o.score?.toFixed(0) ?? "—"}</div></div>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          {!c && !runId && <Card><Empty title={t("src.startTitle")}>{t("src.startSub")}</Empty></Card>}
        </div>
      </div>
      {rfqFor && runId && <RfqDialog runId={runId} offer={rfqFor} onClose={() => setRfqFor(null)} />}
    </div>
  );
}

export default function SourcingPage() {
  return <Suspense><Workspace /></Suspense>;
}
