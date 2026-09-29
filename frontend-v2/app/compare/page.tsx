"use client";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Badge, Card, CardHeader, Empty, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { ExportButtons } from "@/components/export-button";
import { GlossaryBar, Term } from "@/components/glossary";
import { useMarket } from "@/components/market-switcher";
import type { MarketRow } from "@/lib/api";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { CATEGORICAL } from "@/lib/viz";
import { useMarketName } from "@/lib/market-name";
import { headlineOf } from "@/components/v2/market-size";

const COLOR = { a: CATEGORICAL[0], b: CATEGORICAL[1] } as const;
const GRADE: Record<string, string> = { A: "var(--good)", B: "#3987e5", C: "var(--warn)", D: "var(--ink-3)" };
const SEV: Record<string, string> = { high: "var(--bad)", medium: "var(--warn)", low: "var(--ink-3)" };

type Iv = { est: number | null | undefined; lo?: number | null; hi?: number | null };
type Summary = { revenue_month?: { estimate: number; low: number; high: number }; units_month?: { estimate: number; low: number; high: number; floor?: number | null };
  hhi?: number; entrant_success_rate?: number | null; evidence_grade?: string };
type Seg = { segment_id: string; segment_label: string; revenue_est: number | null; revenue_lo: number | null; revenue_hi: number | null;
  opportunity_index: number | null; opportunity_score?: number | null };
type Q = { median: number; p10: number; p90: number };
type Concept = {
  market: string; segment_id: string; segment_label: string; features: string[]; price: number | null; opportunity_index: number | null;
  engine?: { score: number | null } | null;
  evidence_grade: string | null; segment_revenue: number | null; hhi: number | null; entrant_success_rate: number | null; listings: number | null;
  leader?: { brand: string; share: number; share_lo: number; share_hi: number; p_top: number };
  growth: { per_month: number; lo: number; hi: number; direction: string } | { status: string; periods: number; min_periods: number };
  launch?: { units: Q; revenue: Q & { target: number; p_target: number }; risks: { code: string; severity: string }[] } | { error: string };
};
const conceptKey = (c: Concept) => `${c.market}|${c.segment_id}`;
const simOf = (c?: Concept) => (c?.launch && "units" in c.launch ? c.launch : null);
const growOf = (c?: Concept) => (c && "per_month" in c.growth ? c.growth : null);
const signed = (v: number) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;

/** One metric for A and B on a shared scale: the dot is the estimate, the band its interval. */
function IntervalRow({ label, a, b, fmt, names }: { label: React.ReactNode; a: Iv; b: Iv; fmt: (v: number) => string; names: [string, string] }) {
  const { t } = useI18n();
  const vals = [a, b].flatMap((x) => [x.lo, x.hi, x.est]).filter((v): v is number => v != null && Number.isFinite(v));
  if (!vals.length) return null;
  const lo = Math.min(0, ...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (v: number) => `${((v - lo) / span) * 100}%`;
  const verdict = a.lo != null && a.hi != null && b.lo != null && b.hi != null
    ? (a.lo > b.hi ? t("cmp.higher", { a: names[0] }) : b.lo > a.hi ? t("cmp.higher", { a: names[1] }) : t("cmp.overlap")) : null;
  return (
    <div className="border-b border-line px-4 py-3 last:border-b-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{label}</div>
        {verdict && <div className="text-[11px] text-ink-3">{verdict}</div>}
      </div>
      {([["a", a], ["b", b]] as const).map(([k, v]) => (
        <div key={k} className="mt-1.5 grid grid-cols-[1.25rem_1fr_minmax(7.5rem,auto)] items-center gap-2">
          <span className="font-mono text-xs font-semibold" style={{ color: COLOR[k] }}>{k.toUpperCase()}</span>
          <div className="relative h-4">
            <div className="absolute top-1/2 h-px w-full -translate-y-1/2 bg-line" />
            {v.lo != null && v.hi != null && (
              <div className="absolute top-1/2 h-2 -translate-y-1/2 rounded-full opacity-40"
                style={{ left: x(v.lo), width: `calc(${x(v.hi)} - ${x(v.lo)})`, background: COLOR[k] }} />
            )}
            {v.est != null && <div className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-panel"
              style={{ left: x(v.est), background: COLOR[k] }} />}
          </div>
          <span className="text-right font-mono text-xs">{v.est == null ? "—" : fmt(v.est)}
            {v.lo != null && v.hi != null && <span className="block text-[10px] text-ink-3">{fmt(v.lo)}–{fmt(v.hi)}</span>}</span>
        </div>
      ))}
    </div>
  );
}

function Picker({ side, value, options, onChange }: { side: "a" | "b"; value: string; options: { value: string; label: string }[];
  onChange: (v: string) => void }) {
  const { t } = useI18n();
  return (
    <label className="flex min-w-0 flex-1 items-center gap-2">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-mono text-xs font-semibold text-[#04121c]" style={{ background: COLOR[side] }}>{side.toUpperCase()}</span>
      <Select value={value} onChange={(e) => onChange(e.target.value)} className="w-full min-w-0">
        {!value && <option value="">{t("cmp.choose")}</option>}
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
    </label>
  );
}

function MarketSide({ name, side }: { name: string; side: "a" | "b" }) {
  const { t } = useI18n();
  const enc = encodeURIComponent(name);
  const segs = useApi<Seg[]>(name ? `/markets/${enc}/segments-v3` : null, [name]);
  // one opportunity score (the explainable engine's); segments it cannot score follow, by the older index
  const top = [...(segs.data ?? [])].sort((x, y) => ((y.opportunity_score ?? -1) - (x.opportunity_score ?? -1))
    || ((y.opportunity_index ?? -1) - (x.opportunity_index ?? -1))).slice(0, 5);
  return (
    <Card className="min-w-0">
      <CardHeader title={<span style={{ color: COLOR[side] }}>{side.toUpperCase()} · {name}</span>} subtitle={t("cmp.topSegments")}
        right={<Link href={`/markets/${enc}`} className="text-xs text-accent">{t("cmp.open")} →</Link>} />
      {!segs.data ? <div className="h-24 animate-pulse" /> : !top.length ? <Empty title={t("common.notAvailable")} /> : (
        <ol className="divide-y divide-line">
          {top.map((s, i) => (
            <li key={s.segment_id} className="flex items-center gap-2 px-4 py-2 text-sm">
              <span className="w-4 font-mono text-xs text-ink-3">{i + 1}</span>
              <span className="min-w-0 flex-1 truncate" title={s.segment_label}>{s.segment_label}</span>
              <span className="text-right font-mono text-xs">{s.opportunity_score?.toFixed(0) ?? "—"}
                <span className="block text-[10px] text-ink-3">{moneyShort(s.revenue_est)} ({moneyShort(s.revenue_lo)}–{moneyShort(s.revenue_hi)})</span></span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Markets({ a, b, setAB }: { a: string; b: string; setAB: (a: string, b: string) => void }) {
  const mn = useMarketName();
  const { t } = useI18n();
  const [globalMarket] = useMarket();
  const mk = useApi<MarketRow[]>("/markets");
  const names = (mk.data ?? []).map((m) => m.name);
  const A = names.includes(a) ? a : names.includes(globalMarket) ? globalMarket : names[0] ?? "";
  const B = names.includes(b) && b !== A ? b : names.find((n) => n !== A) ?? "";
  const ma = useApi<{ summary: Summary | null }>(A ? `/markets/${encodeURIComponent(A)}/metrics?metric=none` : null, [A]);
  const mb = useApi<{ summary: Summary | null }>(B ? `/markets/${encodeURIComponent(B)}/metrics?metric=none` : null, [B]);
  const ra = mk.data?.find((m) => m.name === A), rb = mk.data?.find((m) => m.name === B);
  const sa = ma.data?.summary ?? {}, sb = mb.data?.summary ?? {};
  const opts = names.map((n) => ({ value: n, label: mn(n) }));
  if (mk.data && names.length < 2) return <Card className="mx-6"><Empty title={t("cmp.empty")}>{t("cmp.emptySub")}</Empty></Card>;
  const nm: [string, string] = [`A (${A})`, `B (${B})`];
  return (
    <div className="space-y-4 px-6">
      <ErrorNote error={mk.error ?? ma.error ?? mb.error} />
      <div className="flex flex-col gap-3 sm:flex-row">
        <Picker side="a" value={A} options={opts} onChange={(v) => setAB(v, B === v ? "" : B)} />
        <Picker side="b" value={B} options={opts.filter((o) => o.value !== A)} onChange={(v) => setAB(A, v)} />
      </div>
      {ra && rb && (
        <Card>
          {/* what the data certainly shows first; the demand model's numbers follow, flagged when it failed validation */}
          <IntervalRow names={nm} label={t("cmp.sizeObserved")} fmt={(v) => moneyShort(v)}
            a={{ est: headlineOf(ra) }} b={{ est: headlineOf(rb) }} />
          <IntervalRow names={nm} label={t("cmp.unitsObserved")} fmt={(v) => num(v, 0)}
            a={{ est: sa.units_month?.floor }} b={{ est: sb.units_month?.floor }} />
          {(ra.model_validated === false || rb.model_validated === false) && (
            <div className="border-b border-line px-4 py-2 text-[11px] text-warn">{t("cmp.modelNote", {
              which: [ra, rb].filter((r) => r.model_validated === false).map((r) => r === ra ? "A" : "B").join(" & ") })}</div>
          )}
          <IntervalRow names={nm} label={<Term id="interval95">{t("cmp.size")}</Term>} fmt={(v) => moneyShort(v)}
            a={{ est: ra.revenue_est, lo: ra.revenue_lo, hi: ra.revenue_hi }} b={{ est: rb.revenue_est, lo: rb.revenue_lo, hi: rb.revenue_hi }} />
          <IntervalRow names={nm} label={t("cmp.units")} fmt={(v) => num(v, 0)}
            a={{ est: sa.units_month?.estimate, lo: sa.units_month?.low, hi: sa.units_month?.high }}
            b={{ est: sb.units_month?.estimate, lo: sb.units_month?.low, hi: sb.units_month?.high }} />
          <IntervalRow names={nm} label={<Term id="hhi">{t("cmp.hhi")}</Term>} fmt={(v) => num(v, 0)} a={{ est: ra.hhi }} b={{ est: rb.hhi }} />
          <IntervalRow names={nm} label={<Term id="entrant">{t("cmp.entry")}</Term>} fmt={(v) => pct(v, 0)}
            a={{ est: sa.entrant_success_rate }} b={{ est: sb.entrant_success_rate }} />
          <IntervalRow names={nm} label={<><Term id="opportunityIndex">{t("cmp.opportunity")}</Term> · {t("home.topSegment")}</>} fmt={(v) => v.toFixed(0)}
            a={{ est: ra.top_segment?.opportunity_score }} b={{ est: rb.top_segment?.opportunity_score }} />
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-6 gap-y-1.5 px-4 py-3 text-sm">
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3"><Term id="evidenceGrade">{t("cmp.grade")}</Term></span>
            {[ra, rb].map((r, i) => <span key={i} className="text-right">{r.evidence_grade ? <Badge color={GRADE[r.evidence_grade]}>{r.evidence_grade}</Badge> : "—"}</span>)}
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("cmp.products")} / {t("cmp.listings")}</span>
            {[ra, rb].map((r, i) => <span key={i} className="text-right font-mono text-xs">{num(r.products)} / {num(r.listings)}</span>)}
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t("cmp.recommended")}</span>
            {[ra, rb].map((r, i) => <span key={i} className="max-w-[12rem] truncate text-right text-xs text-ink-2" title={r.recommendation?.segment_label ?? undefined}>
              {r.recommendation ? `${r.recommendation.segment_label}${r.recommendation.features?.length ? ` · ${r.recommendation.features.join(", ")}` : ""}` : "—"}</span>)}
          </div>
        </Card>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {A && <MarketSide name={A} side="a" />}
        {B && <MarketSide name={B} side="b" />}
      </div>
    </div>
  );
}

function ConceptSide({ c, side }: { c: Concept; side: "a" | "b" }) {
  const mn = useMarketName();
  const { t, lang } = useI18n();
  const s = simOf(c);
  return (
    <Card className="min-w-0">
      <CardHeader title={<span style={{ color: COLOR[side] }}>{side.toUpperCase()} · {mn(c.market)}</span>}
        subtitle={<span className="text-ink-2">{c.segment_label}</span>}
        right={c.evidence_grade && <Badge color={GRADE[c.evidence_grade]}>{c.evidence_grade}</Badge>} />
      <div className="space-y-2 p-4 text-sm">
        <div className="flex flex-wrap items-center gap-1">
          {c.features.map((f) => <Badge key={f} color="var(--accent)">{f}</Badge>)}
          <span className="font-mono text-xs text-ink-2">{t("cmp.price")} {money(c.price, 2)}</span>
        </div>
        <div className="text-xs text-ink-3">{t("cmp.segRevenue")}: <span className="font-mono text-ink-2">{moneyShort(c.segment_revenue)}</span> · {num(c.listings)} {t("common.listings")}</div>
        {c.leader && <div className="text-xs">{t("cmp.leader")}: <b>{c.leader.brand}</b> <span className="text-ink-3">{pct(c.leader.share, 0)} ({pct(c.leader.share_lo, 0)}–{pct(c.leader.share_hi, 0)}) · <Term id="pTop" /> {pct(c.leader.p_top, 0)}</span></div>}
        <div>
          <div className="text-[11px] uppercase text-ink-3">{t("cmp.risks")}</div>
          {!s ? <div className="text-xs text-ink-3">{t("cmp.noSim")}</div> : !s.risks.length ? <div className="text-xs text-good">{t("cmp.noRisk")}</div>
            : s.risks.map((x) => <div key={x.code} className="flex items-center gap-2 text-xs"><span className="h-2 w-2 rounded-full" style={{ background: SEV[x.severity] }} />{t(`launch.risk.${x.code}.0`)}</div>)}
        </div>
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Link href={`/markets/${encodeURIComponent(c.market)}`} className="text-xs text-accent">{t("cmp.open")} →</Link>
          <ExportButtons label={t("exp.memo")} formats={["md", "html"]} path={`/export/memo?${new URLSearchParams({ market: c.market, segment_id: c.segment_id, lang })}`} />
        </div>
      </div>
    </Card>
  );
}

function Concepts({ a, b, setAB }: { a: string; b: string; setAB: (a: string, b: string) => void }) {
  const mn = useMarketName();
  const { t } = useI18n();
  const d = useApi<{ items: Concept[] }>("/opportunities-v3");
  const items = d.data?.items ?? [];
  const byKey = new Map(items.map((c) => [conceptKey(c), c]));
  const A = byKey.has(a) ? a : items[0] ? conceptKey(items[0]) : "";
  const B = byKey.has(b) && b !== A ? b : items.map(conceptKey).find((k) => k !== A) ?? "";
  const ca = byKey.get(A), cb = byKey.get(B);
  const opts = items.map((c) => ({ value: conceptKey(c), label: `${mn(c.market)} · ${truncate(c.segment_label, 48)}${c.engine?.score != null ? ` (${c.engine.score.toFixed(0)})` : ""}` }));
  if (d.data && items.length < 2) return <Card className="mx-6"><Empty title={t("cmp.empty")}>{t("cmp.emptySub")}</Empty></Card>;
  const sa = simOf(ca), sb = simOf(cb), ga = growOf(ca), gb = growOf(cb);
  const nm: [string, string] = ["A", "B"];
  const target = sa?.revenue.target ?? sb?.revenue.target ?? null;
  return (
    <div className="space-y-4 px-6">
      <ErrorNote error={d.error} />
      <div className="flex flex-col gap-3 sm:flex-row">
        <Picker side="a" value={A} options={opts} onChange={(v) => setAB(v, B === v ? "" : B)} />
        <Picker side="b" value={B} options={opts.filter((o) => o.value !== A)} onChange={(v) => setAB(A, v)} />
      </div>
      {ca && cb && (
        <>
          <Card>
            <IntervalRow names={nm} label={t("cmp.simUnits")} fmt={(v) => num(v, 0)}
              a={{ est: sa?.units.median, lo: sa?.units.p10, hi: sa?.units.p90 }} b={{ est: sb?.units.median, lo: sb?.units.p10, hi: sb?.units.p90 }} />
            <IntervalRow names={nm} label={t("cmp.simRevenue")} fmt={(v) => moneyShort(v)}
              a={{ est: sa?.revenue.median, lo: sa?.revenue.p10, hi: sa?.revenue.p90 }} b={{ est: sb?.revenue.median, lo: sb?.revenue.p10, hi: sb?.revenue.p90 }} />
            {target != null && <IntervalRow names={nm} label={t("cmp.pTarget", { v: moneyShort(target) })} fmt={(v) => pct(v, 0)}
              a={{ est: sa?.revenue.p_target }} b={{ est: sb?.revenue.p_target }} />}
            <IntervalRow names={nm} label={<Term id="interval95">{t("cmp.growth")}</Term>} fmt={signed}
              a={{ est: ga?.per_month, lo: ga?.lo, hi: ga?.hi }} b={{ est: gb?.per_month, lo: gb?.lo, hi: gb?.hi }} />
            <IntervalRow names={nm} label={<Term id="opportunityIndex">{t("cmp.opportunity")}</Term>} fmt={(v) => v.toFixed(0)}
              a={{ est: ca.engine?.score }} b={{ est: cb.engine?.score }} />
            <IntervalRow names={nm} label={t("cmp.leader")} fmt={(v) => pct(v, 0)}
              a={{ est: ca.leader?.share, lo: ca.leader?.share_lo, hi: ca.leader?.share_hi }} b={{ est: cb.leader?.share, lo: cb.leader?.share_lo, hi: cb.leader?.share_hi }} />
            <IntervalRow names={nm} label={<Term id="hhi">{t("cmp.hhi")}</Term>} fmt={(v) => num(v, 0)} a={{ est: ca.hhi }} b={{ est: cb.hhi }} />
            <IntervalRow names={nm} label={<Term id="entrant">{t("cmp.entry")}</Term>} fmt={(v) => pct(v, 0)}
              a={{ est: ca.entrant_success_rate }} b={{ est: cb.entrant_success_rate }} />
            {(!ga || !gb) && <div className="px-4 py-2 text-[11px] text-ink-3">{t("cmp.growth")}: {[ca, cb].map((c, i) => {
              const g = c.growth;
              return `${"AB"[i]} ${"per_month" in g ? signed(g.per_month) : t("cmp.growthNone", { n: g.periods ?? 0, m: g.min_periods })}`;
            }).join(" · ")}</div>}
          </Card>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <ConceptSide c={ca} side="a" />
            <ConceptSide c={cb} side="b" />
          </div>
        </>
      )}
      {!d.data && <div className="h-40 animate-pulse rounded-xl border border-line bg-panel" />}
    </div>
  );
}

function View() {
  const { t } = useI18n();
  const sp = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const mode = sp.get("mode") === "concepts" ? "concepts" : "markets";
  const a = sp.get("a") ?? "", b = sp.get("b") ?? "";
  const nav = (m: string, na: string, nb: string) => {
    const q = new URLSearchParams({ mode: m });
    if (na) q.set("a", na);
    if (nb) q.set("b", nb);
    router.replace(`${path}?${q}`, { scroll: false });
  };
  return (
    <div className="pb-10">
      <PageHeader title={t("cmp.title")}
        subtitle={<>{t("cmp.subtitle")}<GlossaryBar terms={["interval95", "evidenceGrade", "hhi", "pTop", "opportunityIndex"]} /></>}
        right={<div className="inline-flex overflow-hidden rounded-lg border border-line text-sm">
          {(["markets", "concepts"] as const).map((m) => (
            <button key={m} type="button" onClick={() => nav(m, "", "")} aria-pressed={mode === m}
              className={mode === m ? "bg-accent/15 px-3 py-1.5 text-accent" : "px-3 py-1.5 text-ink-2 hover:text-ink"}>
              {t(m === "markets" ? "cmp.modeMarkets" : "cmp.modeConcepts")}</button>
          ))}
        </div>} />
      {mode === "markets" ? <Markets a={a} b={b} setAB={(x, y) => nav("markets", x, y)} />
        : <Concepts a={a} b={b} setAB={(x, y) => nav("concepts", x, y)} />}
    </div>
  );
}

export default function ComparePage() {
  return <Suspense><View /></Suspense>;
}
