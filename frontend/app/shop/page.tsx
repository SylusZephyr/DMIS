"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import type { EChartsOption } from "echarts";
import { axis, EChart } from "@/components/charts/echart";
import { Badge, Button, Card, CardHeader, Empty, Input, LiveStatus } from "@/components/ui/primitives";
import { PageHeader } from "@/components/page";
import { post } from "@/lib/api";
import { money, num, pct } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Crit = "match" | "quality" | "proof" | "price";
const CRITERIA: Crit[] = ["match", "quality", "proof", "price"];
type Rec = {
  product_id: string; title: string; brand: string | null; image: string | null; market: string; price: number; rating: number | null;
  rating_bayes: number | null; units_est: number | null; units_lo: number | null; units_hi: number | null; need_coverage: number;
  proof_units: number; utility: number; front: number; dominated_by: string | null; dominated_by_title: string | null;
  pct_match: number; pct_quality: number; pct_proof: number; pct_price: number;
  explain: { criterion: Crit; kind: "strength" | "tradeoff"; value: number | null; percentile: number }[];
};
type Parsed = { requirements: { attribute: string; op: string; value: unknown; unit?: string | null }[];
  budget_min: number | null; budget_max: number | null; min_rating: number | null } | null;
type Out = { parsed?: Parsed; status: string; candidates: number; filtered_out: Record<string, number>; need_words: string[]; weights: Record<Crit, number>;
  profile: string; rating_prior: { mean: number | null; strength: number; basis: string }; pareto_size: number; results: Rec[];
  map: { product_id: string; title: string; price: number; rating_bayes: number | null; proof_units: number; front: number; utility: number }[] };

const PROFILES = ["beginner", "professional", "budget", "default"] as const;

export default function Shop() {
  const mn = useMarketName();
  const { t } = useI18n();
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [need, setNeed] = useState("");
  const [budget, setBudget] = useState("");
  const [profile, setProfile] = useState<string | null>(null);
  const [weights, setWeights] = useState<Record<Crit, number> | null>(null);
  const [out, setOut] = useState<Out | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async (prof: string, w?: Record<Crit, number>) => {
    setErr(null);
    try {
      const r = await post<Out>("/shopping/recommend-v3", { need, budget_max: budget ? Number(budget) : null, profile: prof, weights: w ?? null, limit: 12 });
      setOut(r); setWeights(r.weights); setStep(3);
    } catch (e) { setErr((e as Error).message); }
  };

  const fmtCrit = (c: Crit, v: number | null) => v == null ? "—" : c === "match" ? pct(v) : c === "quality" ? `★ ${v.toFixed(2)}` : c === "proof" ? `≥ ${num(v, 0)}/mo` : money(v, 2);

  const chart = useMemo<EChartsOption | null>(() => {
    if (!out?.map.length) return null;
    const maxProof = Math.max(1, ...out.map.map((p) => p.proof_units));
    const pts = (front: boolean) => out.map.filter((p) => (p.front === 1) === front && p.rating_bayes != null)
      .map((p) => ({ value: [p.price, p.rating_bayes, p.proof_units, p.title, p.product_id], symbolSize: 8 + 26 * Math.sqrt(p.proof_units / maxProof) }));
    return {
      grid: { left: 56, right: 20, top: 30, bottom: 44 },
      legend: { top: 0, textStyle: { color: "#8ea0bb" } },
      tooltip: { formatter: ((p: { value: (string | number)[] }) => `<b>${String(p.value[3]).slice(0, 70)}</b><br/>${money(Number(p.value[0]), 2)} · ★ ${Number(p.value[1]).toFixed(2)} · ≥ ${num(Number(p.value[2]), 0)}/mo`) as never },
      xAxis: { ...axis, type: "value" as const, scale: true, name: t("shop.price"), nameLocation: "middle", nameGap: 28, axisLabel: { color: "#62789a", formatter: (v: number) => money(v) } },
      yAxis: { ...axis, type: "value" as const, scale: true, name: t("shop.quality"), nameLocation: "middle", nameGap: 40 },
      series: [
        { name: t("shop.dominated"), type: "scatter" as const, data: pts(false), itemStyle: { color: "#5b6b82", opacity: 0.6, borderColor: "#0a1220", borderWidth: 2 } },
        { name: t("shop.paretoFront"), type: "scatter" as const, data: pts(true), itemStyle: { color: "#3987e5", borderColor: "#0a1220", borderWidth: 2 } },
      ],
    } as unknown as EChartsOption;
  }, [out, t]);

  const Chip = ({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) => (
    <button onClick={onClick} className={`rounded-xl border px-4 py-3 text-left text-sm ${active ? "border-accent bg-accent/10 text-accent" : "border-line hover:border-accent"}`}>{children}</button>
  );

  return (
    <div className="pb-8">
      <LiveStatus busy={false} done={!!out} busyText={t("a11y.loading")} doneText={t("a11y.loaded")} />
      <PageHeader title={t("shop.title")} subtitle={t("shop.subtitle")} />
      <div className="mx-6 grid grid-cols-1 gap-4 xl:grid-cols-[400px_1fr]">
        <Card className="h-fit">
          <CardHeader title={t("shop.step", { n: Math.min(step + 1, 3) })} />
          <div className="space-y-4 p-4">
            <div>
              <div className="mb-1 text-sm">{t("shop.need")}</div>
              <Input value={need} onChange={(e) => setNeed(e.target.value)} placeholder={t("shop.needPh")} onKeyDown={(e) => e.key === "Enter" && need && setStep(Math.max(step, 1))} />
            </div>
            {step >= 1 && (
              <div>
                <div className="mb-1 text-sm">{t("shop.budget")}</div>
                <div className="flex gap-2">
                  <Input type="number" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="—" />
                  <Button variant="outline" onClick={() => setStep(Math.max(step, 2))}>{t("shop.next")}</Button>
                </div>
              </div>
            )}
            {step >= 2 && (
              <div className="grid grid-cols-1 gap-2">
                {PROFILES.map((p) => <Chip key={p} active={profile === p} onClick={() => { setProfile(p); run(p); }}>
                  <b>{t(`shop.profile.${p}.0`)}</b><div className="text-xs text-ink-3">{t(`shop.profile.${p}.1`)}</div></Chip>)}
              </div>
            )}
            {step === 0 && <Button disabled={!need} onClick={() => setStep(1)}>{t("shop.next")}</Button>}
            {weights && profile && (
              <div className="space-y-2 border-t border-line pt-3">
                <div className="text-sm">{t("shop.weights")}</div>
                {CRITERIA.map((c) => (
                  <label key={c} className="flex items-center gap-2 text-xs">
                    <span className="w-20">{t(`shop.crit.${c}.0`)}</span>
                    <input type="range" min={0} max={1} step={0.05} value={weights[c]} className="flex-1 accent-[#3987e5]"
                      onChange={(e) => setWeights({ ...weights, [c]: Number(e.target.value) })} />
                    <span className="w-10 text-right font-mono">{weights[c].toFixed(2)}</span>
                  </label>
                ))}
                <Button size="sm" variant="outline" onClick={() => run(profile, weights)}>{t("shop.rerank")}</Button>
              </div>
            )}
            {err && <div className="text-xs text-bad">{err}</div>}
            <div className="text-[11px] text-ink-3">{t("shop.how")}</div>
          </div>
        </Card>

        <div className="space-y-4">
          {!out ? <Card><Empty title={t("shop.empty")} /></Card> : !out.results.length ? (
            <Card><Empty title={t("shop.noMatch")}>{t("shop.noMatchSub")}</Empty></Card>
          ) : (<>
            <Card>
              <CardHeader title={t("shop.mapTitle")} subtitle={t("shop.mapSub", { c: out.candidates, p: out.pareto_size, m: out.rating_prior.mean?.toFixed(2) ?? "—" })} />
              {chart && <EChart option={chart} height={340} onEvents={{ click: (p) => { const v = (p as { value?: (string | number)[] }).value; if (v) router.push(`/products/${v[4]}`); } }} />}
              {out.parsed && (out.parsed.requirements.length > 0 || out.parsed.budget_max != null || out.parsed.budget_min != null || out.parsed.min_rating != null) && (
                <div className="flex flex-wrap items-center gap-1.5 border-t border-line px-4 py-2 text-[11px] text-ink-3">
                  <span>{t("shop.parsed")}:</span>
                  {out.parsed.requirements.map((q) => (
                    <Badge key={q.attribute} color="var(--accent)">{q.attribute} {t(`shop.op.${q.op}`)} {Array.isArray(q.value) ? q.value.join("+") : String(q.value)}{q.unit ? ` ${q.unit}` : ""}</Badge>
                  ))}
                  {out.parsed.budget_min != null && <Badge color="var(--ink-3)">≥ ${out.parsed.budget_min}</Badge>}
                  {out.parsed.budget_max != null && <Badge color="var(--ink-3)">≤ ${out.parsed.budget_max}</Badge>}
                  {out.parsed.min_rating != null && <Badge color="var(--ink-3)">★ ≥ {out.parsed.min_rating}</Badge>}
                </div>
              )}
              <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">
                {out.need_words.length > 0 && <>{t("shop.requirements")}: <b className="text-ink-2">{out.need_words.join(", ")}</b> · </>}
                {Object.entries(out.filtered_out).map(([k, v]) => `${t(`shop.filter.${k}`)} ${v}`).join(" · ")}
              </div>
            </Card>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {out.results.map((r, i) => (
                <Card key={r.product_id} className="flex h-full flex-col overflow-hidden">
                  <Link href={`/products/${r.product_id}`} className="relative flex h-36 items-center justify-center bg-white">
                    {r.image ? <img src={r.image} alt="" className="max-h-32 object-contain" onError={(e) => { e.currentTarget.style.display = "none"; }} /> : <span className="text-xs text-ink-3">—</span>}
                    <span className="absolute left-2 top-2 rounded-md bg-bg/90 px-2 py-0.5 font-mono text-xs text-accent">#{i + 1} · {r.utility.toFixed(0)}</span>
                    {r.front === 1 && <span className="absolute right-2 top-2"><Badge color="#3987e5">{t("shop.paretoFront")}</Badge></span>}
                  </Link>
                  <div className="flex-1 space-y-2 p-3 text-sm">
                    <Link href={`/products/${r.product_id}`} className="line-clamp-2 font-medium hover:text-accent">{r.title}</Link>
                    <div className="text-[11px] text-ink-3">{r.brand ?? "—"} · {mn(r.market)}</div>
                    <div className="space-y-1">
                      {CRITERIA.map((c) => {
                        const p = r[`pct_${c}` as const];
                        const val = c === "match" ? r.need_coverage : c === "quality" ? r.rating_bayes : c === "proof" ? r.proof_units : r.price;
                        return (
                          <div key={c} className="grid grid-cols-[70px_1fr_88px] items-center gap-2 text-[11px]">
                            <span className="text-ink-3">{t(`shop.crit.${c}.0`)}</span>
                            <div className="h-1.5 rounded-full bg-panel-2"><div className="h-1.5 rounded-full bg-accent" style={{ width: `${Math.max(3, p * 100)}%` }} /></div>
                            <span className="text-right font-mono">{fmtCrit(c, val)}</span>
                          </div>
                        );
                      })}
                    </div>
                    <ul className="space-y-0.5 text-[11px]">
                      {r.explain.map((e) => <li key={e.criterion} className={e.kind === "strength" ? "text-good" : "text-warn"}>
                        {t(`shop.${e.kind}`, { c: t(`shop.crit.${e.criterion}.0`), p: pct(e.percentile) })}</li>)}
                      {r.rating != null && r.rating_bayes != null && Math.abs(r.rating - r.rating_bayes) >= 0.1 &&
                        <li className="text-ink-3">{t("shop.shrunk", { r: r.rating.toFixed(1), b: r.rating_bayes.toFixed(2) })}</li>}
                      {r.dominated_by && <li className="text-ink-3">{t("shop.dominatedBy")} <Link className="text-accent" href={`/products/${r.dominated_by}`}>{(r.dominated_by_title ?? "").slice(0, 50)}</Link></li>}
                    </ul>
                  </div>
                </Card>
              ))}
            </div>
          </>)}
        </div>
      </div>
    </div>
  );
}
