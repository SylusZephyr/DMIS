"use client";
import { useState } from "react";
import { Check, Download } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input, Select, Stat } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { getToken, post, type MarketRow } from "@/lib/api";
import { money, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type SampleRow = { id: string; market_name: string; name: string; seed: number; items: number; labelled_items: number; created_at: string;
  design: { n_products: number; n_excluded: number; population_products: number; population_excluded: number } };
type Listing = { record_id: string; id: string; title: string; price: number | null; sales: number | null; is_best_listing: boolean; relevance_status: string };
type LabelRow = { check: string; value: Record<string, unknown> | null; correct: boolean | null; notes: string | null; labeller: string | null };
type Item = { id: string; kind: "product" | "excluded_listing"; ref_id: string; stratum: string; position: number;
  snapshot: { title: string; brand?: string | null; segment_label?: string | null; model_label?: string | null; price_tier?: string | null;
    attributes?: Record<string, unknown>; best_listing?: string;
    dental_band?: string | null; dental_confidence?: number | null; kn_attributes?: Record<string, unknown>; taxonomy_node?: string | null; monthly_sales?: number | null; listings?: Listing[];
    id?: string; relevance_status?: string; relevance_score?: number | null; relevance_explanation?: string | null; excluded_reason?: string };
  labels: Record<string, LabelRow> };
type Share = { value: number | null; low: number | null; high: number | null; n?: number };
type Metrics = { against: string; relevance: { precision: Share; recall: Share & { n_excluded_labelled: number }; human_override_excluded: number };
  entity: { pairwise_precision: Share; pairwise_recall: Share; products_exactly_right: Share };
  segment: Share; best_listing: Share; attributes: Share; unverified_after_change: Record<string, number> | null };

const pctCI = (m?: Share | null) => (!m || m.value == null ? "—" : `${(m.value * 100).toFixed(0)}%`);
const ciSub = (m?: Share | null) => (!m || m.value == null ? "—" : m.low != null ? `${(m.low * 100).toFixed(0)}–${((m.high ?? 0) * 100).toFixed(0)}%${m.n ? ` · n=${m.n}` : ""}` : m.n ? `n=${m.n}` : "");

function YesNo({ value, onChange }: { value: boolean | null; onChange: (v: boolean) => void }) {
  const { t } = useI18n();
  return (
    <div className="inline-flex gap-1">
      <Button size="sm" variant={value === true ? "primary" : "outline"} onClick={() => onChange(true)}>{t("lab.correct")}</Button>
      <Button size="sm" variant={value === false ? "primary" : "outline"} onClick={() => onChange(false)}>{t("lab.wrong")}</Button>
    </div>
  );
}

function ProductForm({ item, onSaved }: { item: Item; onSaved: () => void }) {
  const { t } = useI18n();
  const L = item.labels;
  const listings = item.snapshot.listings ?? [];
  const [irr, setIrr] = useState<string[]>((L.relevance?.value?.irrelevant_listings as string[]) ?? []);
  const [wrong, setWrong] = useState<string[]>((L.entity?.value?.wrong_listings as string[]) ?? []);
  const [missing, setMissing] = useState(((L.entity?.value?.missing_listings as string[]) ?? []).join(", "));
  const [seg, setSeg] = useState<boolean | null>(L.segment?.correct ?? null);
  const [best, setBest] = useState<boolean | null>(L.best_listing?.correct ?? null);
  const [attr, setAttr] = useState<boolean | null>(L.attributes?.correct ?? null);
  const [dental, setDental] = useState<boolean | null>((L.dental?.value?.is_dental as boolean | undefined) ?? null);
  const [tax, setTax] = useState<boolean | null>(L.taxonomy?.correct ?? null);
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const toggle = (arr: string[], set: (v: string[]) => void, id: string) => set(arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id]);
  const save = async () => {
    setErr(null);
    try {
      const n = notes || null;
      await post(`/labels/items/${item.id}`, { check: "relevance", value: { irrelevant_listings: irr }, notes: n });
      await post(`/labels/items/${item.id}`, { check: "entity", value: { wrong_listings: wrong,
        missing_listings: missing.split(/[\s,;]+/).filter(Boolean) }, notes: n });
      if (seg != null) await post(`/labels/items/${item.id}`, { check: "segment", correct: seg, notes: n });
      if (best != null) await post(`/labels/items/${item.id}`, { check: "best_listing", correct: best, notes: n });
      if (attr != null) await post(`/labels/items/${item.id}`, { check: "attributes", correct: attr, notes: n });
      if (dental != null) await post(`/labels/items/${item.id}`, { check: "dental", value: { is_dental: dental }, notes: n });
      if (tax != null) await post(`/labels/items/${item.id}`, { check: "taxonomy", correct: tax, notes: n });
      onSaved();
    } catch (e) { setErr((e as Error).message); }
  };
  const s = item.snapshot;
  return (
    <div className="space-y-4 p-4 text-sm">
      <div>
        <div className="font-medium">{s.title}</div>
        <div className="text-xs text-ink-3">{s.brand ?? t("lab.noBrand")} · {t("lab.model")} {s.model_label ?? "—"} · {s.price_tier ?? "—"} · {s.monthly_sales != null ? `${num(s.monthly_sales)} ${t("common.units")}/mo` : t("lab.salesUnknown")}</div>
      </div>
      <div>
        <div className="mb-1 text-xs uppercase text-ink-3">{t("lab.q1")}</div>
        <table className="w-full text-xs">
          <thead className="text-left text-ink-3"><tr><th scope="col" className="py-1">ASIN</th><th scope="col">{t("lab.title2")}</th><th scope="col">{t("launch.price")}</th><th scope="col">{t("lab.sales")}</th><th scope="col" className="text-center">{t("lab.notCategory")}</th><th scope="col" className="text-center">{t("lab.notProduct")}</th></tr></thead>
          <tbody className="divide-y divide-line">
            {listings.map((l) => (
              <tr key={l.id}>
                <td className="py-1 font-mono">{l.id}{l.is_best_listing && <Badge className="ml-1">{t("lab.best")}</Badge>}</td>
                <td className="max-w-[420px] truncate" title={l.title}>{l.title}</td>
                <td className="font-mono">{money(l.price, 2)}</td><td className="font-mono">{num(l.sales)}</td>
                <td className="text-center"><input type="checkbox" checked={irr.includes(l.id)} onChange={() => toggle(irr, setIrr, l.id)} /></td>
                <td className="text-center"><input type="checkbox" checked={wrong.includes(l.id)} onChange={() => toggle(wrong, setWrong, l.id)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <label className="mt-2 block text-xs text-ink-3">{t("lab.missing")}
          <Input value={missing} onChange={(e) => setMissing(e.target.value)} placeholder="B0..., B0..." /></label>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <div><div className="mb-1 text-xs uppercase text-ink-3">{t("lab.q2")}</div><div className="mb-1">{s.segment_label ?? "—"}</div><YesNo value={seg} onChange={setSeg} /></div>
        <div><div className="mb-1 text-xs uppercase text-ink-3">{t("lab.q3")}</div><div className="mb-1 font-mono">{s.best_listing ?? "—"}</div><YesNo value={best} onChange={setBest} /></div>
        <div><div className="mb-1 text-xs uppercase text-ink-3">{t("lab.q4")}</div>
          <div className="mb-1 font-mono text-xs">{Object.keys(s.kn_attributes ?? s.attributes ?? {}).length ? JSON.stringify(s.kn_attributes ?? s.attributes) : t("lab.noneExtracted")}</div><YesNo value={attr} onChange={setAttr} /></div>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <div><div className="mb-1 text-xs uppercase text-ink-3">{t("lab.q5")}</div>
          <div className="mb-1 text-xs text-ink-3">{t("lab.systemSays", { v: s.dental_band ?? "—", c: s.dental_confidence != null ? s.dental_confidence.toFixed(0) : "—" })}</div>
          <YesNo value={dental} onChange={setDental} /></div>
        <div><div className="mb-1 text-xs uppercase text-ink-3">{t("lab.q6")}</div><div className="mb-1">{s.taxonomy_node ?? "—"}</div>
          {s.taxonomy_node ? <YesNo value={tax} onChange={setTax} /> : <div className="text-xs text-ink-3">{t("lab.noNode")}</div>}</div>
      </div>
      <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={t("lab.notesPh")} />
      {err && <div className="text-xs text-bad">{err}</div>}
      <Button onClick={save}>{t("lab.saveNext")}</Button>
    </div>
  );
}

function ExcludedForm({ item, market, onSaved }: { item: Item; market: string; onSaved: () => void }) {
  const { t } = useI18n();
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const s = item.snapshot;
  const save = async (isRelevant: boolean) => {
    try { await post(`/labels/items/${item.id}`, { check: "relevance", value: { is_relevant: isRelevant }, notes: notes || null }); onSaved(); }
    catch (e) { setErr((e as Error).message); }
  };
  const cur = item.labels.relevance?.value?.is_relevant as boolean | undefined;
  return (
    <div className="space-y-3 p-4 text-sm">
      <div className="text-xs uppercase text-ink-3">{t("lab.excludedHead", { s: s.relevance_status ?? "", v: String(s.relevance_score ?? "—") })}</div>
      <div className="font-medium">{s.title}</div>
      <div className="font-mono text-xs text-ink-3">{s.id} · {s.excluded_reason}</div>
      {s.relevance_explanation && <div className="text-xs text-ink-3">{String(s.relevance_explanation).slice(0, 300)}</div>}
      <div>{t("lab.belongs", { m: market })}</div>
      <Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder={t("lab.notesOpt")} />
      <div className="flex gap-2">
        <Button variant={cur === true ? "primary" : "outline"} onClick={() => save(true)}>{t("lab.yes")}</Button>
        <Button variant={cur === false ? "primary" : "outline"} onClick={() => save(false)}>{t("lab.no")}</Button>
      </div>
      {err && <div className="text-xs text-bad">{err}</div>}
    </div>
  );
}

export default function Labelling() {
  const mn = useMarketName();
  const { t } = useI18n();
  const markets = useApi<MarketRow[]>("/markets");
  const [market, setMarket] = useState("");
  const samples = useApi<SampleRow[]>(`/labels/samples${market ? `?market=${encodeURIComponent(market)}` : ""}`, [market]);
  const [sid, setSid] = useState<string | null>(null);
  const items = useApi<Item[]>(sid ? `/labels/samples/${sid}/items` : null, [sid]);
  const [against, setAgainst] = useState("snapshot");
  const metrics = useApi<Metrics>(sid ? `/labels/samples/${sid}/metrics?against=${against}` : null, [sid, against]);
  const [pos, setPos] = useState(0);
  const [draw, setDraw] = useState({ n: "50", n_excluded: "20", seed: "7" });
  const [err, setErr] = useState<string | null>(null);
  const list = items.data ?? [];
  const item = list[Math.min(pos, Math.max(0, list.length - 1))];
  const sample = (samples.data ?? []).find((s) => s.id === sid);
  const done = (it: Item) => (it.kind === "excluded_listing" ? !!it.labels.relevance : !!(it.labels.relevance && it.labels.entity));
  const next = () => { items.reload(); metrics.reload(); samples.reload(); setPos((p) => Math.min(p + 1, list.length - 1)); };
  const newSample = async () => {
    setErr(null);
    try {
      const s = await post<SampleRow>("/labels/samples", { market, n: Number(draw.n), n_excluded: Number(draw.n_excluded), seed: Number(draw.seed) });
      samples.reload(); setSid(s.id); setPos(0);
    } catch (e) { setErr((e as Error).message); }
  };
  const download = async () => {
    const r = await fetch(`/api/v2/labels/samples/${sid}/sheet`, { headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {} });
    const url = URL.createObjectURL(await r.blob());
    Object.assign(document.createElement("a"), { href: url, download: `labels_${sample?.market_name}_${sid}.csv` }).click();
    URL.revokeObjectURL(url);
  };
  const upload = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    try { await post(`/labels/samples/${sid}/sheet`, fd); next(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="pb-16">
      <PageHeader title={t("lab.title")} subtitle={t("lab.subtitle")} />
      <ErrorNote error={err ?? samples.error ?? items.error} />
      <div className="space-y-4 px-6">
        <Card>
          <CardHeader title={t("lab.sample")} subtitle={t("lab.sampleSub")}
            right={<div className="flex flex-wrap items-center gap-2">
              <Select value={market} onChange={(e) => { setMarket(e.target.value); setSid(null); }}>
                <option value="">{t("alerts.allMarkets")}</option>{(markets.data ?? []).map((m) => <option key={m.name} value={m.name}>{mn(m.name)}</option>)}</Select>
              <Select value={sid ?? ""} onChange={(e) => { setSid(e.target.value || null); setPos(0); }} className="max-w-[260px]">
                <option value="">{t("lab.chooseSample")}</option>
                {(samples.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name} · {s.labelled_items}/{s.items}</option>)}</Select>
            </div>} />
          <div className="flex flex-wrap items-end gap-2 p-4 text-sm">
            <label>{t("home.products")}<Input className="w-20" type="number" value={draw.n} onChange={(e) => setDraw({ ...draw, n: e.target.value })} /></label>
            <label>{t("lab.excludedListings")}<Input className="w-24" type="number" value={draw.n_excluded} onChange={(e) => setDraw({ ...draw, n_excluded: e.target.value })} /></label>
            <label>{t("lab.seed")}<Input className="w-20" type="number" value={draw.seed} onChange={(e) => setDraw({ ...draw, seed: e.target.value })} /></label>
            <Button disabled={!market} onClick={newSample}>{market ? t("lab.drawFor", { m: market }) : t("lab.drawChoose")}</Button>
            {sid && <>
              <Button variant="outline" onClick={download}><Download className="h-4 w-4" /> {t("lab.sheet")}</Button>
              <label className="inline-flex h-9 cursor-pointer items-center rounded-lg border border-line px-3 hover:border-accent">{t("lab.importSheet")}
                <input type="file" accept=".csv" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
            </>}
          </div>
        </Card>
        {sid && metrics.data && (
          <Card>
            <CardHeader title={t("lab.measured")} subtitle={against === "snapshot" ? t("lab.againstSnap") : t("lab.againstNow")}
              right={<Select value={against} onChange={(e) => setAgainst(e.target.value)}><option value="snapshot">{t("lab.before")}</option><option value="current">{t("lab.after")}</option></Select>} />
            <div className="grid grid-cols-2 divide-x divide-line md:grid-cols-4 xl:grid-cols-7">
              <Stat label={t("lab.relP")} value={pctCI(metrics.data.relevance.precision)} sub={ciSub(metrics.data.relevance.precision)} />
              <Stat label={t("lab.relR")} value={pctCI(metrics.data.relevance.recall)} sub={ciSub(metrics.data.relevance.recall)} />
              <Stat label={t("lab.grpP")} value={pctCI(metrics.data.entity.pairwise_precision)} sub={ciSub(metrics.data.entity.pairwise_precision)} />
              <Stat label={t("lab.grpR")} value={pctCI(metrics.data.entity.pairwise_recall)} sub={ciSub(metrics.data.entity.pairwise_recall)} />
              <Stat label={t("lab.segment")} value={pctCI(metrics.data.segment)} sub={ciSub(metrics.data.segment)} />
              <Stat label={t("lab.bestListing")} value={pctCI(metrics.data.best_listing)} sub={ciSub(metrics.data.best_listing)} />
              <Stat label={t("lab.attributes")} value={pctCI(metrics.data.attributes)} sub={ciSub(metrics.data.attributes)} />
            </div>
            {against === "current" && <div className="px-4 py-2 text-[11px] text-ink-3">
              {t("lab.afterNote", { o: metrics.data.relevance.human_override_excluded, u: Object.entries(metrics.data.unverified_after_change ?? {}).map(([k, v]) => `${k} ${v}`).join(", ") })}</div>}
          </Card>
        )}
        {sid && (list.length === 0 ? <Card><Empty title={t("common.loading")} /></Card> : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[240px_1fr]">
            <Card className="max-h-[720px] overflow-auto scrollbar-thin">
              <CardHeader title={t("lab.labelled", { a: list.filter(done).length, b: list.length })} />
              {list.map((it, i) => (
                <button key={it.id} onClick={() => setPos(i)}
                  className={cn("flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-panel-2", i === pos && "bg-accent/10 text-accent")}>
                  {done(it) ? <Check className="h-3 w-3 text-good" /> : <span className="h-3 w-3 rounded-full border border-line" />}
                  <span className="w-6 font-mono text-ink-3">{i + 1}</span>
                  <span className="truncate">{it.kind === "excluded_listing" ? "⊘ " : ""}{it.snapshot.title}</span>
                </button>
              ))}
            </Card>
            <Card>
              <CardHeader title={t("lab.item", { a: pos + 1, b: list.length })} subtitle={`${t("lab.stratum")}: ${item.stratum}`}
                right={<div className="flex gap-2"><Button size="sm" variant="ghost" disabled={pos === 0} onClick={() => setPos(pos - 1)}>← {t("lab.prev")}</Button>
                  <Button size="sm" variant="ghost" disabled={pos >= list.length - 1} onClick={() => setPos(pos + 1)}>{t("lab.next")} →</Button></div>} />
              {item.kind === "product"
                ? <ProductForm key={item.id} item={item} onSaved={next} />
                : <ExcludedForm key={item.id} item={item} market={sample?.market_name ?? t("lab.thisMarket")} onSaved={next} />}
            </Card>
          </div>
        ))}
        {!sid && <Card><Empty title={t("lab.choose")}>{t("lab.chooseSub")}</Empty></Card>}
      </div>
    </div>
  );
}
