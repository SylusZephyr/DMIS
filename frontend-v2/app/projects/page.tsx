"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, Input, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { post, type MarketRow, type Outcome, type ProjectDetail, type ProjectRow } from "@/lib/api";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

const STATUS_COLOR: Record<string, string> = { active: "var(--ink-2)", pending_approval: "var(--warn)", approved: "var(--good)",
  rejected: "var(--bad)", closed: "var(--ink-3)" };
const errText = (e: unknown) => (e as Error).message.replace(/^\d+ [^:]*: /, "");

export default function ProjectsPage() {
  return <Suspense><Projects /></Suspense>;
}

function Projects() {
  const mn = useMarketName();
  const { t } = useI18n();
  const [nonce, setNonce] = useState(0);
  const list = useApi<{ stages: string[]; projects: ProjectRow[] }>("/projects", [nonce]);
  const markets = useApi<MarketRow[]>("/markets");
  const [sel, setSel] = useState<string | null>(useSearchParams().get("selected"));
  const [f, setF] = useState({ title: "", market: "", price: "", cost: "" });
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => setNonce((n) => n + 1);
  const priceErr = f.price !== "" && !(Number(f.price) > 0) ? t("projects.errPrice") : null;
  const costErr = f.cost !== "" && !(Number(f.cost) >= 0) ? t("projects.errCost") : null;
  const create = async () => {
    setErr(null);
    try {
      const idea: Record<string, number> = {};
      if (f.price) idea.price = Number(f.price);
      if (f.cost) idea.unit_cost = Number(f.cost);
      const p = await post<ProjectDetail>("/projects", { title: f.title, market: f.market || null, idea });
      setF({ title: "", market: f.market, price: "", cost: "" }); setSel(p.id); refresh();
    } catch (e) { setErr(errText(e)); }
  };
  const stages = list.data?.stages ?? [];
  return (
    <div className="pb-8">
      <PageHeader title={t("projects.title")} subtitle={t("projects.subtitle")} />
      <ErrorNote error={list.error} />
      <Card className="mx-6 mb-4">
        <div className="flex flex-wrap items-start gap-2 p-3">
          <Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder={t("projects.titlePh")} className="min-w-[300px] flex-1" />
          <Select value={f.market} onChange={(e) => setF({ ...f, market: e.target.value })} className="w-56" aria-label={t("projects.marketAuto")}>
            <option value="">{t("projects.marketAuto")}</option>
            {(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}
          </Select>
          <div><Input type="number" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} placeholder={t("projects.price")} aria-label={t("projects.price")} className="w-36" />
            {priceErr && <div className="text-[10px] text-bad">{priceErr}</div>}</div>
          <div><Input type="number" value={f.cost} onChange={(e) => setF({ ...f, cost: e.target.value })} placeholder={t("projects.cost")} aria-label={t("projects.cost")} title={t("projects.cost")} className="w-44" />
            {costErr && <div className="text-[10px] text-bad">{costErr}</div>}</div>
          <Button disabled={f.title.trim().length < 3 || !!priceErr || !!costErr} onClick={create}>{t("projects.create")}</Button>
        </div>
        <div className="px-3 pb-2 text-[11px] text-ink-3">{t("projects.createNote")}</div>
        {err && <div className="px-3 pb-2 text-xs text-bad">{err}</div>}
      </Card>
      <div className="grid grid-cols-1 gap-4 px-6 2xl:grid-cols-[1fr_540px]">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {stages.map((st) => {
            const here = (list.data?.projects ?? []).filter((p) => p.stage === st);
            return (
              <Card key={st} className="min-h-[200px]">
                <CardHeader title={t(`projects.stage.${st}`)} subtitle={t("projects.count", { n: here.length })} />
                <div className="space-y-2 p-2">
                  {here.map((p) => (
                    <button key={p.id} onClick={() => setSel(p.id)}
                      className={`w-full rounded-lg border p-2 text-left text-xs hover:border-accent ${sel === p.id ? "border-accent" : "border-line"}`}>
                      <div className="font-medium text-ink">{truncate(p.title, 70)}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1"><Badge color={STATUS_COLOR[p.status]}>{t(`projects.status.${p.status}`)}</Badge>
                        {p.prediction?.units_p10_p50_p90 && <span className="font-mono text-ink-3">~{num(p.prediction.units_p10_p50_p90[1], 1)}/mo</span>}</div>
                      <div className="mt-0.5 text-ink-3">{p.market_name ?? "—"}</div>
                    </button>
                  ))}
                </div>
              </Card>
            );
          })}
        </div>
        {sel ? <Detail id={sel} stages={stages} onChange={refresh} /> : <Card><Empty title={t("projects.select")}>{t("projects.selectSub")}</Empty></Card>}
      </div>
    </div>
  );
}

function Detail({ id, stages, onChange }: { id: string; stages: string[]; onChange: () => void }) {
  const { t } = useI18n();
  const [nonce, setNonce] = useState(0);
  const d = useApi<ProjectDetail>(`/projects/${id}`, [nonce]);
  const o = useApi<Outcome>(`/projects/${id}/outcome`, [nonce]);
  const [text, setText] = useState("");
  const [asins, setAsins] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const act = async (path: string, body: unknown) => {
    setErr(null);
    try { await post(`/projects/${id}/${path}`, body); setText(""); setNonce((n) => n + 1); onChange(); }
    catch (e) { setErr(errText(e)); }
  };
  const comment = async () => {
    try { await post("/comments", { target_kind: "project", target_id: id, text, market: d.data?.market_name }); setText(""); setNonce((n) => n + 1); }
    catch (e) { setErr(errText(e)); }
  };
  const p = d.data;
  if (!p) return <Card><Empty title="…" /></Card>;
  const pr = p.prediction;
  const u = pr?.units_p10_p50_p90;
  const rv = pr?.revenue_p10_p50_p90;
  const warnings = [...((p.idea as { warnings?: string[] } | null)?.warnings ?? []), ...(pr?.warnings ?? [])];
  return (
    <Card>
      <CardHeader title={truncate(p.title, 60)} subtitle={`${p.market_name ?? t("projects.noMarket")} · ${t(`projects.stage.${p.stage}`)} · ${t("projects.owner")} ${p.owner ?? "—"}`}
        right={<Badge color={STATUS_COLOR[p.status]}>{t(`projects.status.${p.status}`)}</Badge>} />
      <div className="space-y-4 p-4 text-sm">
        {warnings.map((w) => <div key={w} className="rounded border border-warn/40 bg-warn/10 px-2 py-1 text-xs text-warn">{w}</div>)}
        {pr ? (
          <div className="rounded-lg border border-line p-3 text-xs">
            <div className="text-[11px] uppercase text-ink-3">{t("projects.prediction")} · {pr.model ?? "v2"}</div>
            <div className="mt-1">{t("projects.segment")}: <b>{truncate(pr.segment ?? "", 50)}</b> · {t("projects.opportunity")} <b>{pr.market_attractiveness?.toFixed(0) ?? "—"}</b>
              {pr.verdict && <> ({pr.verdict})</>}</div>
            {u && <div className="mt-1">{t("projects.units")}: <b className="font-mono">{num(u[1], 1)}</b> <span className="text-ink-3">(p10–p90 {num(u[0], 1)}–{num(u[2], 1)})</span>
              {rv && <> · {t("projects.revenue")} <b className="font-mono">{moneyShort(rv[1])}</b> <span className="text-ink-3">({moneyShort(rv[0])}–{moneyShort(rv[2])})</span></>}</div>}
            {pr.profit ? <div className="mt-1">{t("projects.profit")}: <b className="font-mono">{moneyShort(pr.profit.median)}</b> <span className="text-ink-3">({moneyShort(pr.profit.p10)}–{moneyShort(pr.profit.p90)})</span>
              · P(&gt;0) {pct(pr.profit.p_positive)}{pr.break_even_units != null && pr.break_even_units > 0 && <> · {t("projects.breakEven")} {num(pr.break_even_units, 1)}/mo</>}</div>
              : pr.economics && <div className="mt-1 text-warn">{t("projects.noCost")}</div>}
            {pr.economics?.unit_margin != null && <div className="text-ink-3">{t("projects.margin")} {money(pr.economics.unit_margin, 2)} · {t("projects.fba")}: {pr.economics.fulfilment_basis}</div>}
            {(pr.risks ?? []).length > 0 && <div className="mt-1 text-ink-3">{t("projects.risks")}: {(pr.risks ?? []).map((r) => t(`launch.risk.${r.code}.0`)).join(" · ")}</div>}
            {(pr.gaps ?? []).length > 0 && <div className="text-ink-3">{t("projects.gaps")}: {(pr.gaps ?? []).map((g) => `${g.feature} ${g.covered ? "✓" : "✗"}`).join(" · ")}</div>}
            {p.market_name && <Link className="mt-1 inline-block text-accent" href={`/launch?market=${encodeURIComponent(p.market_name)}&title=${encodeURIComponent(p.title)}${(p.idea as { price?: number })?.price ? `&price=${(p.idea as { price?: number }).price}` : ""}`}>{t("projects.openSim")} →</Link>}
          </div>
        ) : <div className="text-xs text-ink-3">{t("projects.noPrediction")}</div>}
        <div className="flex flex-wrap gap-2">
          <select className="h-8 rounded-lg border border-line bg-panel-2 px-2 text-xs" value={p.stage} onChange={(e) => act("stage", { stage: e.target.value, note: text || null })}>
            {stages.map((s) => <option key={s} value={s}>{t(`projects.stage.${s}`)}</option>)}
          </select>
          <Button size="sm" variant="outline" onClick={() => act("recommend", { text: text || null })}>{t("projects.recommend")}</Button>
          <Button size="sm" variant="outline" onClick={() => act("approve", { text: text || null })}>{t("projects.approve")}</Button>
          <Button size="sm" variant="ghost" onClick={() => act("reject", { text: text || null })}>{t("projects.reject")}</Button>
        </div>
        <div className="text-[11px] text-ink-3">{t("projects.gateNote")}</div>
        <div className="flex gap-2">
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder={t("projects.notePh")} />
          <Button size="sm" variant="outline" disabled={!text.trim()} onClick={comment}>{t("projects.comment")}</Button>
        </div>
        {err && <div className="text-xs text-bad">{err}</div>}
        <div>
          <div className="text-[11px] uppercase text-ink-3">{t("projects.history")}</div>
          <ul className="mt-1 space-y-1 text-xs">
            {p.history.map((h) => (
              <li key={h.id}><span className="font-mono text-ink-3">{new Date(h.created_at).toLocaleDateString()}</span> <b>{t(`projects.event.${h.kind}`)}</b>
                {h.to_stage && h.kind === "stage" && <> {t(`projects.stage.${h.from_stage ?? ""}`)} → {t(`projects.stage.${h.to_stage}`)}</>} {h.by && <span className="text-ink-3">· {h.by}</span>}
                {h.text && <span className="text-ink-2"> — {h.text}</span>}</li>
            ))}
          </ul>
        </div>
        {p.comments.length > 0 && (
          <div>
            <div className="text-[11px] uppercase text-ink-3">{t("projects.comments")}</div>
            {p.comments.map((c) => <div key={c.id} className="text-xs"><b>{c.author}</b>: {c.text}</div>)}
          </div>
        )}
        <div>
          <div className="text-[11px] uppercase text-ink-3">{t("projects.outcome")}</div>
          <div className="mt-1 flex gap-2">
            <Input value={asins} onChange={(e) => setAsins(e.target.value)} placeholder={t("projects.asinPh")} />
            <Button size="sm" variant="outline" disabled={!asins.trim()} onClick={() => act("track", { listing_ids: asins.split(/[\s,]+/).filter(Boolean) })}>{t("projects.track")}</Button>
          </div>
          {o.data && o.data.status !== "ok" ? <div className="mt-1 text-xs text-ink-3">{o.data.note}</div> : o.data && (
            <div className="mt-2 space-y-1 text-xs">
              {(o.data.observations ?? []).map((x) => <div key={x.period}>{x.period}: {num(x.sales)} {t("common.units")} · {money(x.revenue)} · ★ {x.rating?.toFixed(1) ?? "—"}</div>)}
              {(o.data.checkpoints ?? []).map((c) => (
                <div key={c.months} className="text-ink-3">{t("projects.months", { m: c.months })}: {c.status === "measured"
                  ? <>{t("projects.actual")} {c.actual_range && c.actual_range[0] !== c.actual_range[1] ? `${num(c.actual_range[0])}–${c.actual_range[1] == null ? "+" : num(c.actual_range[1])}` : num(c.actual_units)} vs {t("projects.predicted")} {num(c.predicted_units_p50, 1)}
                    ({c.within_p10_p90 ? t("projects.within") : t("projects.outside")})</>
                  : <>{t("projects.pending", { d: c.due ?? "" })}</>}</div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
