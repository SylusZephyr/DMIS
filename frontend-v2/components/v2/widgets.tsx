"use client";
// Dashboard widgets shared by Mission Control and the workspace hubs. Each reads through the shared request
// cache, so a widget shown on two pages costs one request.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { useQueries } from "@tanstack/react-query";
import type { EChartsOption } from "echarts";
import { AlertTriangle, ArrowRight, Bell, CalendarClock, Copy, FolderKanban, GitBranch, LayoutGrid, Sparkles, Swords, Trophy, type LucideIcon } from "lucide-react";
import { Badge, Card, CardHeader, Empty, Skeleton } from "@/components/ui/primitives";
import { EChart, axis } from "@/components/charts/echart";
import { headlineOf, ModelledNote } from "@/components/v2/market-size";
import { ScoreRing } from "@/components/v2/motion";
import { get, type AlertRow, type EventRow, type MarketRow } from "@/lib/api";
import { severityColor } from "@/lib/colors";
import { useDescribe } from "@/lib/events";
import { moneyShort, num, pct, timeAgo, truncate } from "@/lib/format";
import { apiKey, useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { nodeLabel, useMarketName } from "@/lib/market-name";
import { categoricalMap, seq } from "@/lib/viz";

export type EngineRow = { scope: string; scope_id: string; label: string; products: number | null; opportunity_score: number | null;
  evidence_coverage: number | null; status: string; confidence: number | null;
  evidence_matrix?: Record<string, { value?: number | null; score?: number | null } | undefined> };
export type Scoped = EngineRow & { market: string };
type Explained = { market: string; rows: EngineRow[] };
type Dups = { total: number; rows: { status: string }[] };
type Tax = { nodes: { status: string }[] };
type Anom = { rows: unknown[] };
type Fresh = { market: string; stale: boolean | null; growth: { needed: number; have: number; ready: boolean } };
export type Projects = { stages: string[]; projects: { id: string; title: string; stage: string; status: string; market_name?: string | null }[] };

export const GRADE: Record<string, string> = { A: "var(--good)", B: "#3987e5", C: "var(--warn)", D: "var(--ink-3)" };
const enc = encodeURIComponent;
const query = <T,>(path: string) => ({ queryKey: apiKey(path), queryFn: ({ signal }: { signal: AbortSignal }) => get<T>(path, { signal }) });

export function scopeHref(r: Scoped) {
  return r.scope === "category" ? `/markets/${enc(r.market)}` : `/intelligence?market=${enc(r.market)}&scope=${r.scope}&id=${enc(r.scope_id)}`;
}

export function useMarkets() {
  const mk = useApi<MarketRow[]>("/markets");
  const names = useMemo(() => (mk.data ?? []).map((m) => m.name), [mk.data]);
  // colour follows the market (ordered once by size), never its rank in a list
  const colors = useMemo(() => categoricalMap([...(mk.data ?? [])].sort((a, b) => (b.revenue_est ?? 0) - (a.revenue_est ?? 0)).map((m) => m.name)), [mk.data]);
  return { ...mk, names, colors };
}

/** Every scored scope of every market (the opportunity engine), best first. */
export function useOpportunities(names: string[]) {
  const res = useQueries({ queries: names.map((n) => query<Explained>(`/markets/${enc(n)}/opportunities/explained`)) });
  const key = `${names.join("|")}:${res.map((r) => r.dataUpdatedAt).join(",")}`;
  const rows = useMemo<Scoped[]>(() => res.flatMap((r, i) => (r.data?.rows ?? []).map((x) => ({ ...x, market: names[i],
      label: x.scope === "taxonomy" ? nodeLabel(x.label) : x.label })))
    .filter((r) => r.opportunity_score != null && r.scope !== "category")
    .sort((a, b) => (b.opportunity_score ?? 0) - (a.opportunity_score ?? 0)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key]);
  return { rows, loading: names.length > 0 && res.some((r) => r.isPending) };
}

export type AttentionItem = { key: string; n: number; text: string; href: string; icon: LucideIcon; tone: string };

/** What needs a person: review queues, data gaps, approvals, unread alerts. */
export function useAttention(names: string[]) {
  const { t } = useI18n();
  const mn = useMarketName();
  const dups = useQueries({ queries: names.map((n) => query<Dups>(`/markets/${enc(n)}/duplicates`)) });
  const tax = useQueries({ queries: names.map((n) => query<Tax>(`/markets/${enc(n)}/taxonomy`)) });
  const anom = useQueries({ queries: names.map((n) => query<Anom>(`/markets/${enc(n)}/anomalies`)) });
  const fresh = useApi<Fresh[]>("/freshness");
  const projects = useApi<Projects>("/projects");
  const alerts = useApi<AlertRow[]>("/alerts");
  const items: AttentionItem[] = [];
  names.forEach((m, i) => {
    const d = dups[i]?.data, tx = tax[i]?.data, an = anom[i]?.data;
    const rv = `/review?market=${enc(m)}`;
    const pend = d?.rows.filter((r) => r.status === "pending").length ?? 0;
    if (pend) items.push({ key: `d${m}`, n: pend, text: t("mc.att.dups", { n: `${pend}${d && d.rows.length < d.total ? "+" : ""}`, m: mn(m) }), href: rv, icon: Copy, tone: "var(--warn)" });
    const tn = tx?.nodes.filter((x) => x.status === "machine").length ?? 0;
    if (tn) items.push({ key: `t${m}`, n: tn, text: t("mc.att.tax", { n: tn, m: mn(m) }), href: rv, icon: GitBranch, tone: "var(--accent)" });
    if (an?.rows.length) items.push({ key: `a${m}`, n: an.rows.length, text: t("mc.att.anom", { n: an.rows.length, m: mn(m) }), href: rv, icon: AlertTriangle, tone: "var(--bad)" });
  });
  for (const f of fresh.data ?? []) {
    if (f.stale) items.push({ key: `s${f.market}`, n: 1, text: t("mc.att.stale", { m: mn(f.market) }), href: "/data", icon: CalendarClock, tone: "var(--warn)" });
    else if (!f.growth.ready) items.push({ key: `g${f.market}`, n: 0, text: t("mc.att.growth", { n: f.growth.needed - f.growth.have, m: mn(f.market) }), href: "/data", icon: CalendarClock, tone: "var(--ink-3)" });
  }
  const pa = (projects.data?.projects ?? []).filter((p) => p.status === "pending_approval").length;
  if (pa) items.push({ key: "proj", n: pa, text: t("mc.att.approve", { n: pa }), href: "/projects", icon: FolderKanban, tone: "var(--accent-2)" });
  const unread = (alerts.data ?? []).filter((a) => a.status === "new").length;
  if (unread) items.push({ key: "alerts", n: unread, text: t("mc.att.alerts", { n: unread }), href: "/alerts", icon: Bell, tone: "var(--accent)" });
  items.sort((a, b) => b.n - a.n);
  const loading = names.length > 0 && [...dups, ...tax, ...anom].some((r) => r.isPending);
  return { items, loading, reviewOpen: items.filter((a) => a.href.startsWith("/review")).reduce((s, a) => s + a.n, 0), projects: projects.data, unread };
}

export function OpportunityList({ rows, loading, colors, limit = 8, market }: { rows: Scoped[]; loading: boolean; colors: Map<string, string>; limit?: number; market?: string }) {
  const { t } = useI18n();
  const mn = useMarketName();
  const shown = (market ? rows.filter((r) => r.market === market) : rows).slice(0, limit);
  return (
    <Card>
      <CardHeader title={t("mc.topOpps")} subtitle={market ? t("mc.topOppsIn", { m: mn(market) }) : t("mc.topOppsSub")}
        right={<Link href={market ? `/opportunities?market=${enc(market)}` : "/opportunities"} className="inline-flex items-center gap-1 whitespace-nowrap text-xs text-accent hover:underline">{t("mc.openBoard")} →</Link>} />
      <ol className="divide-y divide-line">
        {loading && [0, 1, 2, 3, 4].map((i) => <li key={i} className="px-4 py-3"><Skeleton className="h-5 w-full" /></li>)}
        {!loading && shown.length === 0 && <li><Empty title={t("kn.insufficient")} /></li>}
        {!loading && shown.map((r, i) => (
          <li key={`${r.market}:${r.scope}:${r.scope_id}`}>
            <Link href={scopeHref(r)} className="group grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 hover:bg-panel-2/60">
              <span className="font-mono text-xs text-ink-3">{i + 1}</span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium group-hover:text-accent" title={r.label}>{r.label}</span>
                <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="h-1.5 min-w-16 flex-1 overflow-hidden rounded-full bg-line">
                    <span className="block h-full rounded-full transition-[width] duration-700" style={{ width: `${r.opportunity_score}%`, background: seq((r.opportunity_score ?? 0) / 100) }} />
                  </span>
                  <span className="shrink-0 text-[11px] text-ink-3">
                    <span className="mr-1 inline-block h-2 w-2 rounded-full align-middle" style={{ background: colors.get(r.market) }} aria-hidden />
                    {mn(r.market)} · {t(`mc.scope.${r.scope}`)} · {pct(r.evidence_coverage, 0)} {t("mc.measured")}</span>
                </span>
              </span>
              <ScoreRing score={r.opportunity_score} color={seq((r.opportunity_score ?? 0) / 100)} label={t("mc.score")} />
            </Link>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function AttentionList({ items, loading, limit = 7, filter }: { items: AttentionItem[]; loading: boolean; limit?: number; filter?: (a: AttentionItem) => boolean }) {
  const { t } = useI18n();
  const shown = (filter ? items.filter(filter) : items).slice(0, limit);
  return (
    <Card>
      <CardHeader title={t("mc.attention")} subtitle={t("mc.attentionSub")} />
      <ul className="divide-y divide-line">
        {loading && shown.length === 0 && [0, 1, 2].map((i) => <li key={i} className="px-4 py-3"><Skeleton className="h-5 w-full" /></li>)}
        {!loading && shown.length === 0 && <li><Empty title={t("mc.allClear")}>{t("mc.allClearSub")}</Empty></li>}
        {shown.map((a) => (
          <li key={a.key}>
            <Link href={a.href} className="group flex items-center gap-3 px-4 py-2.5 hover:bg-panel-2/60">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line" style={{ color: a.tone }}><a.icon className="h-4 w-4" aria-hidden /></span>
              <span className="min-w-0 flex-1 text-sm text-ink-2 group-hover:text-ink">{a.text}</span>
              <ArrowRight className="h-4 w-4 shrink-0 text-ink-3 transition-transform group-hover:translate-x-0.5 group-hover:text-accent" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function OpportunityLandscape({ rows, loading, names, colors }: { rows: Scoped[]; loading: boolean; names: string[]; colors: Map<string, string> }) {
  const { t } = useI18n();
  const mn = useMarketName();
  const router = useRouter();
  const option = useMemo<EChartsOption>(() => ({
    grid: { left: 52, right: 20, top: 40, bottom: 48 },
    legend: { top: 0, textStyle: { color: "var(--ink-2)" }, itemWidth: 10, itemHeight: 10 },
    tooltip: { trigger: "item", formatter: (p: unknown) => {
      const d = (p as { data: { raw: Scoped } }).data.raw;
      return `<b>${truncate(d.label, 60)}</b><br/>${mn(d.market)}<br/>${t("mc.score")} ${d.opportunity_score?.toFixed(0)} · ${t("mc.demand")} ${moneyShort(d.evidence_matrix?.demand?.value ?? null)}/mo` +
        `<br/>${t("mc.coverage")} ${pct(d.evidence_coverage, 0)}`;
    } },
    xAxis: { ...axis, type: "log", name: t("mc.demandAxis"), nameLocation: "middle", nameGap: 30, min: 10,
      axisLabel: { color: "var(--ink-3)", formatter: (v: number) => moneyShort(v) } },
    yAxis: { ...axis, type: "value", name: t("mc.score"), min: 0, max: 100 },
    series: names.map((m) => ({
      name: mn(m), type: "scatter" as const,
      itemStyle: { color: colors.get(m) ?? "#5b6b82", borderColor: "var(--panel)", borderWidth: 2, opacity: 0.9 },
      emphasis: { focus: "series" as const, scale: 1.4 },
      data: rows.filter((r) => r.market === m && (r.evidence_matrix?.demand?.value ?? 0) > 0).map((r) => ({
        value: [Math.max(10, r.evidence_matrix!.demand!.value as number), r.opportunity_score as number],
        symbolSize: Math.max(8, Math.min(28, 6 + Math.sqrt(r.products ?? 1) * 3)), raw: r })),
    })),
  }), [rows, names, colors, mn, t]);
  return (
    <Card>
      <CardHeader title={t("mc.landscape")} subtitle={t("mc.landscapeSub")} />
      <div className="px-2 pb-2">
        {loading ? <Skeleton className="m-4 h-72" /> : rows.length === 0 ? <Empty title={t("kn.insufficient")} /> :
          <EChart option={option} height={340} label={t("mc.landscape")}
            onEvents={{ click: (p: unknown) => { const r = (p as { data?: { raw?: Scoped } }).data?.raw; if (r) router.push(scopeHref(r)); } }} />}
      </div>
    </Card>
  );
}

export function MarketCards({ markets, loading }: { markets: MarketRow[]; loading: boolean }) {
  const { t } = useI18n();
  const mn = useMarketName();
  const sorted = [...markets].sort((a, b) => (b.top_opportunity_score ?? -1) - (a.top_opportunity_score ?? -1));
  const maxRev = Math.max(1, ...sorted.map((x) => x.revenue_hi ?? x.revenue_est ?? 0));
  return (
    <div className="stagger grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {loading && [0, 1, 2].map((i) => <Skeleton key={i} className="h-48 rounded-2xl" />)}
      {sorted.map((m) => {
        const e = enc(m.name);
        return (
          <Card key={m.name} className="lift p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <Link href={`/markets/${e}`} className="block truncate text-base font-semibold hover:text-accent">{mn(m.name)}</Link>
                <div className="mt-0.5 text-[11px] text-ink-3">{num(m.products)} {t("common.products")} · {num(m.segments)} {t("common.segments")} · HHI {num(m.hhi)}</div>
              </div>
              <ScoreRing score={m.top_opportunity_score} size={48} color={seq((m.top_opportunity_score ?? 0) / 100)} label={t("mc.bestScore")} />
            </div>
            <div className="mt-3 flex items-baseline gap-2">
              <span className="font-mono text-xl" title={t("size.observedTip")}>{moneyShort(headlineOf(m))}</span>
              <span className="text-xs text-ink-3">/mo · {t("size.observed")}</span>
              {m.evidence_grade && <Badge className="ml-auto" color={GRADE[m.evidence_grade]}>{t("common.evidenceGrade")} {m.evidence_grade}</Badge>}
            </div>
            <ModelledNote m={m} className="mt-0.5 block text-[11px] text-ink-3" />
            {m.revenue_lo != null && m.revenue_hi != null && m.revenue_est != null && (
              <div className="relative mt-2 h-1.5 rounded-full bg-line" title={`${t("common.interval")} ${moneyShort(m.revenue_lo)}–${moneyShort(m.revenue_hi)}`}>
                <div className="absolute inset-y-0 rounded-full bg-accent/35" style={{ left: `${(m.revenue_lo / maxRev) * 100}%`, width: `${((m.revenue_hi - m.revenue_lo) / maxRev) * 100}%` }} />
                <div className="absolute -top-0.5 h-2.5 w-0.5 rounded bg-accent" style={{ left: `${(m.revenue_est / maxRev) * 100}%` }} />
                {headlineOf(m) != null && <div className="absolute -top-1 h-3.5 w-0.5 rounded bg-ink" title={t("size.observedTip")}
                  style={{ left: `${((headlineOf(m) ?? 0) / maxRev) * 100}%` }} />}
              </div>
            )}
            {m.top_opportunity_label && <div className="mt-3 truncate text-xs text-ink-2" title={m.top_opportunity_label}><Trophy className="mr-1 inline h-3 w-3 text-accent" aria-hidden />{m.top_opportunity_label}</div>}
            <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
              {([[`/markets/${e}`, "mc.analyze", LayoutGrid], [`/opportunities?market=${e}`, "mc.board", Trophy], [`/competitors?market=${e}`, "mc.rivals", Swords], [`/galaxy/${e}`, "nav.galaxy", Sparkles]] as const).map(([href, k, Icon]) => (
                <Link key={k} href={href} className="inline-flex h-8 items-center gap-1 rounded-lg border border-line px-2 text-ink-2 hover:border-accent/60 hover:text-accent"><Icon className="h-3.5 w-3.5" aria-hidden />{t(k)}</Link>
              ))}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

export function ActivityFeed({ limit = 8 }: { limit?: number }) {
  const { t, lang } = useI18n();
  const mn = useMarketName();
  const describe = useDescribe();
  const events = useApi<EventRow[]>(`/events?limit=${limit}`);
  return (
    <Card>
      <CardHeader title={t("mc.activity")} subtitle={t("mc.activitySub")}
        right={<Link href="/alerts" className="whitespace-nowrap text-xs text-accent hover:underline">{t("ia.allAlerts")} →</Link>} />
      <ol className="px-4 py-3">
        {!events.data && [0, 1, 2].map((i) => <li key={i} className="py-2"><Skeleton className="h-4 w-3/4" /></li>)}
        {events.data?.length === 0 && <li><Empty title={t("alerts.noEvents")} /></li>}
        {(events.data ?? []).map((ev, i, arr) => (
          <li key={ev.id} className="relative flex gap-3 pb-3">
            {i < arr.length - 1 && <span aria-hidden className="absolute left-[5px] top-4 h-full w-px bg-line" />}
            <span className="relative mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: severityColor(ev.severity) }} aria-hidden />
            <span className="min-w-0 flex-1 text-sm">
              <span className="line-clamp-2 text-ink-2">{describe(ev)}</span>
              <span className="text-[11px] text-ink-3">{ev.market_name ? `${mn(ev.market_name)} · ` : ""}{timeAgo(ev.created_at, lang)}</span>
            </span>
          </li>
        ))}
      </ol>
    </Card>
  );
}

/** Projects per pipeline stage as a compact funnel. */
export function PipelineFunnel() {
  const { t } = useI18n();
  const p = useApi<Projects>("/projects");
  const stages = p.data?.stages ?? [];
  const counts = stages.map((s) => (p.data?.projects ?? []).filter((x) => x.stage === s).length);
  const max = Math.max(1, ...counts);
  return (
    <Card>
      <CardHeader title={t("hub.funnel")} subtitle={t("hub.funnelSub")}
        right={<Link href="/projects" className="whitespace-nowrap text-xs text-accent hover:underline">{t("nav.projects")} →</Link>} />
      <div className="space-y-2 p-4">
        {!p.data && <Skeleton className="h-24" />}
        {stages.map((s, i) => (
          <div key={s} className="grid grid-cols-[9rem_1fr_2rem] items-center gap-3 text-sm">
            <span className="truncate text-ink-2">{t(`projects.stage.${s}`)}</span>
            <span className="h-2 rounded-full bg-line"><span className="block h-full rounded-full accent-bar transition-[width] duration-700" style={{ width: `${(counts[i] / max) * 100}%` }} /></span>
            <span className="text-right font-mono text-xs">{counts[i]}</span>
          </div>
        ))}
        {p.data && !p.data.projects.length && <p className="pt-1 text-xs text-ink-3">{t("hub.noProjects")}</p>}
      </div>
    </Card>
  );
}
