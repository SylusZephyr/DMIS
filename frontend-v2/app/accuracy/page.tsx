"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, Empty } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post } from "@/lib/api";
import { num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { KnowledgeAccuracyCard } from "@/components/knowledge-accuracy";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Check = { metric: string; value: number | null; target: number; status: "pass" | "watch" | "fail" | "n/a"; scenario?: string | null };
type ScenSum = { markets: number; median_abs_error: number; mean_error: number; market_coverage_95: number; listing_coverage_95: number; top_category_correct: number };
type SynRow = { seed: number; scenario?: string; heavy_tails: boolean; market_rel_error: number; market_covered: boolean; listing_coverage_95: number;
  top_category_correct: boolean; observed_floor_rel_error: number; elasticity_true: number; elasticity_est: number | null };
type Acc = {
  synthetic: { ran_at: string; markets: number; rows: SynRow[]; checks: Check[];
    summary: { median_abs_error: number; mean_error: number; market_coverage_95: number; listing_coverage_95: number; top_category_correct: number; observed_floor_mean_error: number; by_scenario?: Record<string, ScenSum> } } | null;
  synthetic_running: boolean;
  markets: { market: string; evidence_grade: string | null; badged_share: number | null; model_family: string | null; listings: number | null; auc: number | null;
    auc_status: string; rung_within_one: number | null; coverage_95: number | null; coverage_80: number | null; held_out: number | null; error?: string }[];
  labels: { sample_id?: string; market?: string; name?: string; note?: string; [k: string]: unknown }[];
  launch_calibration: { projects: number; measured_checkpoints: number; within_p10_p90_share: number | null; median_ratio_to_p50: number | null };
};
const STATUS_COLOR: Record<string, string> = { pass: "var(--good)", watch: "var(--warn)", fail: "var(--bad)", "n/a": "var(--ink-3)" };

export default function Accuracy() {
  const mn = useMarketName();
  const { t } = useI18n();
  const [nonce, setNonce] = useState(0);
  const d = useApi<Acc>("/accuracy", [nonce]);
  const [err, setErr] = useState<string | null>(null);
  const running = d.data?.synthetic_running;
  useEffect(() => {                       // poll while a validation run is going
    if (!running) return;
    const id = setInterval(() => setNonce((n) => n + 1), 4000);
    return () => clearInterval(id);
  }, [running]);
  const run = async () => {
    setErr(null);
    try { await post("/accuracy/synthetic", {}); setNonce((n) => n + 1); } catch (e) { setErr((e as Error).message); }
  };
  const syn = d.data?.synthetic;
  const fmt = (m: string, v: number | null) => (v == null ? "—" : m === "median_abs_error" ? pct(v, 1) : pct(v, 0));
  return (
    <div className="pb-10">
      <PageHeader title={t("accuracy.title")} subtitle={t("accuracy.subtitle")} />
      <ErrorNote error={d.error} />
      <div className="space-y-4 px-6">
        <Card>
          <CardHeader title={t("accuracy.synTitle")} subtitle={syn ? t("accuracy.synSub", { n: syn.markets, at: new Date(syn.ran_at).toLocaleString() }) : t("accuracy.synNever")}
            right={<Button size="sm" onClick={run} disabled={!!running}>{running ? t("accuracy.running") : t("accuracy.run")}</Button>} />
          {err && <div className="px-4 py-2 text-xs text-bad">{err}</div>}
          <div className="border-b border-line px-4 py-2 text-xs text-ink-3">{t("accuracy.synHow")}</div>
          {!syn ? <Empty title={t("accuracy.synEmpty")} /> : (<>
            <div className="grid grid-cols-2 divide-x divide-line md:grid-cols-4">
              {syn.checks.map((c) => (
                <div key={c.metric} className="p-4">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-3">{t(`accuracy.m.${c.metric}`)}</div>
                  <div className="mt-1 flex items-center gap-2 font-mono text-xl">{fmt(c.metric, c.value)} <Badge color={STATUS_COLOR[c.status]}>{t(`accuracy.status.${c.status}`)}</Badge></div>
                  <div className="text-[11px] text-ink-3">{t("accuracy.target")} {c.metric === "median_abs_error" ? "≤ " : "≥ "}{fmt(c.metric, c.target)}</div>
                  {c.scenario && <div className="text-[11px] text-ink-3">{t("accuracy.worstIn", { s: t(`accuracy.scenario.${c.scenario}`) })}</div>}
                </div>
              ))}
            </div>
            <div className="border-t border-line px-4 py-2 text-xs text-ink-2">{t("accuracy.floorNote", { e: pct(syn.summary.observed_floor_mean_error, 0), m: pct(syn.summary.mean_error, 1) })}</div>
            {syn.summary.by_scenario && (
              <div className="overflow-auto border-t border-line" tabIndex={0}>
                <table className="w-full text-xs">
                  <thead className="text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-1.5">{t("accuracy.byScenario")}</th><th scope="col">{t("accuracy.markets")}</th>
                    <th scope="col">{t("accuracy.m.median_abs_error")}</th><th scope="col">{t("accuracy.bias")}</th><th scope="col">{t("accuracy.m.market_coverage_95")}</th><th scope="col">{t("accuracy.m.listing_coverage_95")}</th><th scope="col" className="pr-4">{t("accuracy.m.top_category_correct")}</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {Object.entries(syn.summary.by_scenario).map(([k, v]) => (
                      <tr key={k}>
                        <td className="px-4 py-1">{t(`accuracy.scenario.${k}`)}</td><td className="font-mono">{v.markets}</td>
                        <td className="font-mono">{pct(v.median_abs_error, 1)}</td><td className="font-mono">{(v.mean_error * 100).toFixed(1)}%</td>
                        <td className="font-mono">{pct(v.market_coverage_95, 0)}</td><td className="font-mono">{pct(v.listing_coverage_95, 0)}</td>
                        <td className="pr-4 font-mono">{pct(v.top_category_correct, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="max-h-72 overflow-auto border-t border-line scrollbar-thin" tabIndex={0}>
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-panel text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-1.5">{t("accuracy.market")}</th><th scope="col">{t("accuracy.scenarioCol")}</th>
                  <th scope="col">{t("accuracy.sizeError")}</th><th scope="col">{t("accuracy.covered")}</th><th scope="col">{t("accuracy.listingCov")}</th><th scope="col">{t("accuracy.topCat")}</th><th scope="col" className="pr-4">{t("accuracy.elasticity")}</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {syn.rows.map((r) => (
                    <tr key={`${r.scenario ?? ""}-${r.seed}`}>
                      <td className="px-4 py-1 font-mono">#{r.seed}</td><td>{r.scenario ? t(`accuracy.scenario.${r.scenario}`) : r.heavy_tails ? t("accuracy.heavy") : t("accuracy.normal")}</td>
                      <td className="font-mono">{(r.market_rel_error * 100).toFixed(1)}%</td>
                      <td>{r.market_covered ? "✓" : <span className="text-bad">✗</span>}</td><td className="font-mono">{pct(r.listing_coverage_95, 0)}</td>
                      <td>{r.top_category_correct ? "✓" : <span className="text-bad">✗</span>}</td>
                      <td className="pr-4 font-mono">{r.elasticity_true} → {r.elasticity_est?.toFixed(2) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>)}
        </Card>

        <Card>
          <CardHeader title={t("accuracy.cvTitle")} subtitle={t("accuracy.cvSub")} />
          {!d.data?.markets.length ? <Empty title={t("common.notAvailable")} /> : (
            <table className="w-full text-sm">
              <thead className="text-left text-[10px] uppercase text-ink-3"><tr><th scope="col" className="px-4 py-2">{t("accuracy.market")}</th><th scope="col">{t("common.evidenceGrade")}</th>
                <th scope="col">{t("accuracy.badged")}</th><th scope="col">{t("accuracy.model")}</th><th scope="col">AUC</th><th scope="col">{t("accuracy.rung")}</th><th scope="col">{t("accuracy.cov95")}</th><th scope="col" className="pr-4">{t("accuracy.heldOut")}</th></tr></thead>
              <tbody className="divide-y divide-line">
                {d.data.markets.map((m) => (
                  <tr key={m.market}>
                    <td className="px-4 py-2"><Link className="hover:text-accent" href={`/markets/${encodeURIComponent(m.market)}`}>{m.market}</Link></td>
                    <td>{m.evidence_grade ?? "—"}</td><td className="font-mono text-xs">{pct(m.badged_share, 1)}</td>
                    <td className="text-xs">{m.model_family ?? "—"} · n={num(m.listings)}</td>
                    <td className="font-mono text-xs">{m.auc?.toFixed(2) ?? "—"} <Badge color={STATUS_COLOR[m.auc_status]}>{t(`accuracy.status.${m.auc_status}`)}</Badge></td>
                    <td className="font-mono text-xs">{pct(m.rung_within_one, 0)}</td><td className="font-mono text-xs">{pct(m.coverage_95, 0)}</td>
                    <td className="pr-4 font-mono text-xs">{m.held_out ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">{t("accuracy.cvNote")}</div>
        </Card>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader title={t("accuracy.labelsTitle")} subtitle={t("accuracy.labelsSub")} right={<Link className="text-xs text-accent" href="/labelling">{t("nav.labelling")} →</Link>} />
            {!d.data?.labels.length ? <Empty title={t("accuracy.noLabels")}>{t("accuracy.noLabelsSub")}</Empty> : (
              <div className="divide-y divide-line">
                {d.data.labels.map((l, i) => (
                  <div key={l.sample_id ?? i} className="px-4 py-2 text-xs">
                    <b>{l.name ?? l.sample_id}</b> <span className="text-ink-3">· {mn(l.market)}</span>
                    {l.note ? <div className="text-ink-3">{l.note}</div> : <pre className="mt-1 whitespace-pre-wrap text-[10px] text-ink-2">{JSON.stringify(Object.fromEntries(Object.entries(l).filter(([k]) => !["sample_id", "market", "name", "created_at"].includes(k))), null, 0).slice(0, 600)}</pre>}
                  </div>
                ))}
              </div>
            )}
          </Card>
          <Card>
            <CardHeader title={t("accuracy.launchTitle")} subtitle={t("accuracy.launchSub")} />
            <div className="grid grid-cols-2 divide-x divide-line">
              <div className="p-4"><div className="text-[11px] uppercase text-ink-3">{t("accuracy.measured")}</div>
                <div className="font-mono text-xl">{d.data?.launch_calibration.measured_checkpoints ?? 0}</div>
                <div className="text-[11px] text-ink-3">{t("accuracy.fromProjects", { n: d.data?.launch_calibration.projects ?? 0 })}</div></div>
              <div className="p-4"><div className="text-[11px] uppercase text-ink-3">{t("accuracy.withinRange")}</div>
                <div className="font-mono text-xl">{pct(d.data?.launch_calibration.within_p10_p90_share)}</div>
                <div className="text-[11px] text-ink-3">{t("accuracy.expect80")}</div></div>
            </div>
            <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">{t("accuracy.launchNote")}</div>
          </Card>
        </div>
      </div>
      <div className="mt-4 px-6"><KnowledgeAccuracyCard /></div>
    </div>
  );
}
