"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Loader2, UploadCloud, XCircle } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input, Select, Stat } from "@/components/ui/primitives";
import { PageHeader } from "@/components/page";
import { get, post, type Job, type MarketRow } from "@/lib/api";
import { num, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { FreshnessCard } from "@/components/freshness-card";
import { useMarketName } from "@/lib/market-name";

type Excluded = { id: string; title: string; category: string | null; relevance_score: number; excluded_reason: string;
  relevance_explanation?: { title?: { verdict: string; dental_terms: string[]; non_dental_terms: string[] }; category?: { verdict: string } } };

const STAGE_ICON = { done: CheckCircle2, running: Loader2, failed: XCircle, queued: CircleDashed } as const;

export default function DataOps() {
  const mn = useMarketName();
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [reviews, setReviews] = useState<File | null>(null);
  const [market, setMarket] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [marketplace, setMarketplace] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const qc = useQueryClient();
  const jobs = useApi<Job[]>("/jobs?limit=10", [job?.status]);
  const markets = useApi<MarketRow[]>("/markets", [job?.status]);
  const [inspect, setInspect] = useState<string>("");
  const excluded = useApi<Excluded[]>(inspect ? `/markets/${encodeURIComponent(inspect)}/records?status=excluded&limit=300` : null, [inspect]);

  useEffect(() => {
    if (!jobId) return;
    const iv = setInterval(async () => {
      const j = await get<Job>(`/jobs/${jobId}`);
      setJob(j);
      if (j.status === "done" || j.status === "failed") {
        clearInterval(iv);
        if (j.status === "done") void qc.invalidateQueries({ queryKey: ["api"] }); // the job changed markets server-side
        if (j.status === "done" && j.market_name) setInspect(j.market_name); // market exists only once the job finishes
      }
    }, 800);
    return () => clearInterval(iv);
  }, [jobId, qc]);

  const submit = async () => {
    if (!file || !market.trim()) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("market", market.trim());
    if (snapshot) fd.append("snapshot_date", snapshot);
    if (marketplace) fd.append("marketplace", marketplace);
    if (reviews) fd.append("reviews", reviews);
    setErr(null); setJob(null);
    try {
      const r = await post<{ job_id: string }>("/datasets", fd);
      setJobId(r.job_id);
    } catch (e) { setErr((e as Error).message); }
  };

  const correct = async (r: Excluded, isDental: boolean) => {
    await post("/relevance/feedback", { record_id: r.id, text: `${r.title} ${r.category ?? ""}`, is_dental: isDental, market: inspect });
    alert(t("data.saved"));
  };

  const rep = job?.report;
  return (
    <div className="pb-8">
      <PageHeader title={t("data.title")} subtitle={t("data.subtitle")} />
      <div className="px-6 pb-4"><FreshnessCard /></div>
      <div className="grid grid-cols-1 gap-4 px-6 xl:grid-cols-[420px_1fr]">
        <Card>
          <CardHeader title={t("data.new")} />
          <div className="space-y-3 p-4 text-sm">
            <label className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-line px-4 py-6 text-center hover:border-accent focus-within:border-accent focus-within:ring-2 focus-within:ring-accent">
              <UploadCloud className="h-6 w-6 text-accent" />
              <span>{file ? file.name : t("data.choose")}</span>
              <input type="file" className="sr-only" accept=".xlsx,.xls,.csv,.tsv,.json,.jsonl" onChange={(e) => {
                const f = e.target.files?.[0] ?? null; setFile(f);
                if (f && !market) setMarket(f.name.replace(/\.[^.]+$/, "").replace(/_sellersprite.*$/i, ""));
              }} />
            </label>
            <div><div className="mb-1 text-xs text-ink-3">{t("data.market")}</div><Input value={market} onChange={(e) => setMarket(e.target.value)} placeholder={t("data.marketPh")} /></div>
            <div className="grid grid-cols-2 gap-2">
              <div><div className="mb-1 text-xs text-ink-3">{t("data.snapshot")}</div><Input type="date" aria-label={t("data.snapshot")} value={snapshot} onChange={(e) => setSnapshot(e.target.value)} /></div>
              <div><div className="mb-1 text-xs text-ink-3">{t("data.marketplace")}</div>
                <Select aria-label={t("data.marketplace")} value={marketplace} onChange={(e) => setMarketplace(e.target.value)} className="w-full">
                  <option value="">{t("data.autoMarketplace")}</option>
                  {["US", "GB", "DE", "FR", "IT", "ES", "JP", "CA", "AU", "CN", "IN"].map((c) => <option key={c}>{c}</option>)}
                </Select></div>
            </div>
            <div><div className="mb-1 text-xs text-ink-3">{t("data.reviews")}</div>
              <input type="file" aria-label={t("data.reviews")} className="text-xs text-ink-2" accept=".csv,.xlsx,.json" onChange={(e) => setReviews(e.target.files?.[0] ?? null)} /></div>
            <Button className="w-full" disabled={!file || !market.trim() || job?.status === "running"} onClick={submit}>{t("data.process")}</Button>
            {err && <div className="text-xs text-bad">{err}</div>}
          </div>
        </Card>

        <Card>
          <CardHeader title={t("data.processing")} subtitle={job ? `${t("data.job")} ${job.id} · ${t(`data.status.${job.status}`)}` : t("data.start")}
            right={job?.status === "done" && <Link href={`/markets/${encodeURIComponent(job.market_name ?? "")}`} className="text-sm text-accent">{t("data.openMarket")} →</Link>} />
          {!job ? <Empty title={t("data.noJob")} /> : (
            <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-2">
              <ol className="space-y-1.5 text-sm">
                {job.stages.map((s) => {
                  const Icon = STAGE_ICON[(s.status as keyof typeof STAGE_ICON) ?? "queued"] ?? CircleDashed;
                  return (
                    <li key={s.name} className="flex items-center gap-2">
                      <Icon className={`h-4 w-4 ${s.status === "done" ? "text-good" : s.status === "failed" ? "text-bad" : "animate-spin text-accent"}`} />
                      <span className="flex-1">{t(`data.stage.${s.name}`).startsWith("data.stage.") ? s.name.replace(/_/g, " ") : t(`data.stage.${s.name}`)}</span>
                      <span className="font-mono text-xs text-ink-3">{s.seconds != null ? `${s.seconds}s` : ""}</span>
                    </li>
                  );
                })}
                {job.error && <li className="whitespace-pre-wrap text-xs text-bad">{truncate(job.error, 600)}</li>}
              </ol>
              {rep && (
                <div className="space-y-3">
                  <div className="grid grid-cols-3 divide-x divide-line rounded-lg border border-line">
                    <Stat label={t("data.raw")} value={num(rep.raw_rows)} />
                    <Stat label={t("data.accepted")} value={<span className="text-good">{num(rep.accepted)}</span>} />
                    <Stat label={t("data.rejected")} value={<span className="text-bad">{num(rep.rejected)}</span>} />
                  </div>
                  <div className="text-xs">
                    <div className="mb-1 font-semibold uppercase tracking-wider text-ink-3">{t("data.reasons")}</div>
                    {Object.keys(rep.rejection_reasons).length === 0 ? <span className="text-ink-3">{t("common.none")}</span> :
                      Object.entries(rep.rejection_reasons).map(([k, v]) => <div key={k} className="flex justify-between"><span>{k}</span><span className="font-mono">{v}</span></div>)}
                  </div>
                  <div className="text-xs">
                    <div className="mb-1 font-semibold uppercase tracking-wider text-ink-3">{t("data.columns", { a: rep.adapter })}</div>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(rep.column_mapping).map(([k, v]) => <Badge key={k}>{k} ← {truncate(v, 18)} · {pct(rep.mapping_confidence[k])}</Badge>)}
                    </div>
                    {rep.warnings.map((w) => <div key={w} className="mt-1 text-warn">⚠ {w}</div>)}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 px-6 pt-4 xl:grid-cols-[420px_1fr]">
        <Card>
          <CardHeader title={t("data.recent")} />
          <div className="divide-y divide-line text-sm">
            {(jobs.data ?? []).map((j) => (
              <button key={j.id} className="flex w-full items-center justify-between px-4 py-2 text-left hover:bg-panel-2" onClick={() => { setJobId(j.id); if (j.market_name) setInspect(j.market_name); }}>
                <span>{j.market_name}</span><Badge color={j.status === "done" ? "var(--good)" : j.status === "failed" ? "var(--bad)" : "var(--accent)"}>{t(`data.status.${j.status}`)}</Badge>
              </button>
            ))}
          </div>
        </Card>
        <Card>
          <CardHeader title={t("data.excluded")} subtitle={t("data.excludedSub")}
            right={<Select aria-label={t("data.chooseMarket")} value={inspect} onChange={(e) => setInspect(e.target.value)}>
              <option value="">{t("data.chooseMarket")}</option>{(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}</Select>} />
          <div className="max-h-[420px] overflow-auto scrollbar-thin" tabIndex={0}>
            {(excluded.data ?? []).length === 0 ? <Empty title={inspect ? t("data.nothingExcluded") : t("data.chooseMarket")} /> : (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-panel text-left uppercase text-ink-3"><tr><th scope="col" className="px-3 py-2">{t("data.record")}</th><th scope="col">{t("data.reason")}</th><th scope="col">{t("data.dental")}</th><th scope="col" className="pr-3">{t("data.correct")}</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {(excluded.data ?? []).map((r) => (
                    <tr key={r.id + r.excluded_reason}>
                      <td className="max-w-[380px] px-3 py-1.5"><div className="truncate">{r.title}</div>
                        <div className="truncate text-ink-3">{r.category} {r.relevance_explanation?.title?.non_dental_terms?.length ? `· ${t("data.offDomain")}: ${r.relevance_explanation.title.non_dental_terms.join(", ")}` : ""}</div></td>
                      <td className="text-ink-2">{truncate(r.excluded_reason, 40)}</td>
                      <td className="font-mono">{r.relevance_score?.toFixed(0)}</td>
                      <td className="space-x-1 pr-3 whitespace-nowrap">
                        <Button size="sm" variant="outline" onClick={() => correct(r, true)}>{t("data.isDental")}</Button>
                        <Button size="sm" variant="ghost" onClick={() => correct(r, false)}>{t("data.notDental")}</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
