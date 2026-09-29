"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";
import { Button, Card, CardHeader, Input, Select } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { EDGE_COLOR, type EdgeType, type GalaxyEdge, type GalaxyProduct, type GalaxySegment } from "@/components/three/galaxy-types";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { CATEGORICAL, OTHER, SEQ, seq } from "@/lib/viz";
import { useMarketName } from "@/lib/market-name";

const Galaxy = dynamic(() => import("@/components/three/galaxy"), { ssr: false });

type Trait = "segment" | "family" | "brand" | "price_quartile" | "opportunity" | "margin" | "entrant";
type SizeBy = "revenue" | "units" | "uniform";
const TRAITS: Trait[] = ["segment", "family", "brand", "price_quartile", "opportunity", "margin", "entrant"];
const CATEGORICAL_TRAITS = new Set<Trait>(["segment", "family", "brand", "entrant"]);
type Payload = { products: GalaxyProduct[]; segments: GalaxySegment[]; edges: GalaxyEdge[]; edge_counts: Record<string, number>;
  lod?: { shown: number; total: number; limit: number; revenue_share_shown: number | null } };

function catKey(p: GalaxyProduct, t: Trait): string {
  if (t === "segment") return p.segment_label ?? p.segment_id;
  if (t === "family") return p.family_label ?? "—";
  if (t === "brand") return p.brand ?? "(no brand)";
  if (t === "entrant") return p.is_entrant == null ? "unknown" : p.is_entrant ? "entrant" : "established";
  return "";
}

function GalaxyView() {
  const mn = useMarketName();
  const { t } = useI18n();
  const market = decodeURIComponent(useParams<{ market: string }>().market);
  const initialSeg = useSearchParams().get("segment");
  const { data, error } = useApi<Payload>(`/markets/${encodeURIComponent(market)}/galaxy-v3`);
  const [trait, setTrait] = useState<Trait>("segment");
  const [sizeBy, setSizeBy] = useState<SizeBy>("revenue");
  const [edgeTypes, setEdgeTypes] = useState<Set<EdgeType>>(new Set(["similar"]));
  const [seg, setSeg] = useState<string | null>(initialSeg);
  const [search, setSearch] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [minOpp, setMinOpp] = useState(0);
  const [isolate, setIsolate] = useState<string | null>(null);
  const [sel, setSel] = useState<GalaxyProduct | null>(null);
  const [compare, setCompare] = useState<GalaxyProduct[]>([]);
  const products = useMemo(() => data?.products ?? [], [data]);

  // categorical colours: stable by entity, ordered by estimated revenue once; beyond 7 -> Other
  const cats = useMemo(() => {
    if (!CATEGORICAL_TRAITS.has(trait)) return null;
    const rev = new Map<string, number>();
    products.forEach((p) => { const k = catKey(p, trait); rev.set(k, (rev.get(k) ?? 0) + (p.revenue_est ?? 0)); });
    const order = [...rev.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    const fixed = trait === "entrant" ? ["established", "entrant", "unknown"].filter((k) => rev.has(k)) : order;
    const colors = new Map(fixed.slice(0, 7).map((k, i) => [k, k === "unknown" ? OTHER : CATEGORICAL[i]]));
    const counts = new Map<string, number>();
    products.forEach((p) => { const k = catKey(p, trait); counts.set(k, (counts.get(k) ?? 0) + 1); });
    return { colors, order: fixed, counts, other: fixed.slice(7).reduce((s, k) => s + (counts.get(k) ?? 0), 0) };
  }, [products, trait]);

  const color = useCallback((p: GalaxyProduct): string => {
    if (cats) return cats.colors.get(catKey(p, trait)) ?? OTHER;
    if (trait === "price_quartile") return p.price_quartile == null ? OTHER : SEQ[[1, 3, 5, 8][p.price_quartile - 1]];
    if (trait === "opportunity") return p.opportunity_score == null ? OTHER : seq(p.opportunity_score / 100);
    if (trait === "margin") return p.margin_rate == null ? OTHER : seq(Math.max(0, Math.min(1, p.margin_rate / 0.6)));
    return OTHER;
  }, [cats, trait]);

  const maxSize = useMemo(() => Math.max(1e-9, ...products.map((p) => (sizeBy === "revenue" ? p.revenue_est : p.units_est) ?? 0)), [products, sizeBy]);
  const size = useCallback((p: GalaxyProduct) => sizeBy === "uniform" ? 0.07
    : 0.035 + 0.2 * Math.sqrt(Math.max(0, (sizeBy === "revenue" ? p.revenue_est : p.units_est) ?? 0) / maxSize), [sizeBy, maxSize]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q && !seg && !maxPrice && !minOpp && !isolate) return null;
    return new Set(products.filter((p) => (!q || p.title.toLowerCase().includes(q) || (p.brand ?? "").toLowerCase().includes(q))
      && (!seg || p.segment_id === seg) && (!maxPrice || (p.price != null && p.price <= Number(maxPrice)))
      && (p.opportunity_score ?? 0) >= minOpp
      && (!isolate || (cats ? (cats.colors.has(catKey(p, trait)) ? catKey(p, trait) === isolate : isolate === "__other") : true)))
      .map((p) => p.product_id));
  }, [products, search, seg, maxPrice, minOpp, isolate, cats, trait]);

  const labelSegments = useMemo(() => {
    if (seg) return new Set([seg]);
    return new Set([...(data?.segments ?? [])].sort((a, b) => (b.revenue_est ?? 0) - (a.revenue_est ?? 0)).slice(0, 6).map((s) => s.segment_id));
  }, [data, seg]);

  const links = useMemo(() => {
    if (!sel || !data) return null;
    const byId = new Map(products.map((p) => [p.product_id, p]));
    const similar = data.edges.filter((e) => e.type === "similar" && (e.source === sel.product_id || e.target === sel.product_id))
      .map((e) => ({ p: byId.get(e.source === sel.product_id ? e.target : e.source)!, score: e.score })).filter((x) => x.p)
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    const brand = sel.brand ? products.filter((p) => p.brand === sel.brand && p.product_id !== sel.product_id) : [];
    const segPeers = products.filter((p) => p.segment_id === sel.segment_id && p.product_id !== sel.product_id)
      .sort((a, b) => (b.revenue_est ?? 0) - (a.revenue_est ?? 0));
    return { similar, brand, segPeers };
  }, [sel, data, products]);

  const toggleEdge = (e: EdgeType) => setEdgeTypes((s) => { const n = new Set(s); if (n.has(e)) n.delete(e); else n.add(e); return n; });
  const toggleCompare = (p: GalaxyProduct) => setCompare((c) => c.some((x) => x.product_id === p.product_id)
    ? c.filter((x) => x.product_id !== p.product_id) : [...c, p].slice(-4));
  const traitValue = (p: GalaxyProduct) => cats ? catKey(p, trait) : trait === "price_quartile" ? (p.price_quartile ? `Q${p.price_quartile}` : "—")
    : trait === "opportunity" ? (p.opportunity_score?.toFixed(0) ?? "—") : (p.margin_rate == null ? "—" : pct(p.margin_rate));
  const PLink = ({ p, extra }: { p: GalaxyProduct; extra?: string }) => (
    <button onClick={() => setSel(p)} className="flex w-full items-center gap-2 px-4 py-1 text-left text-xs hover:bg-panel-2">
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color(p) }} />
      <span className="min-w-0 flex-1 truncate">{p.title}</span><span className="font-mono text-ink-3">{extra ?? moneyShort(p.revenue_est)}</span>
    </button>
  );

  return (
    <div className="flex min-h-screen flex-col">
      <PageHeader title={`${t("galaxy.title")} · ${mn(market)}`}
        subtitle={t("galaxy.subtitle", { n: num(products.length), s: num(data?.edge_counts.similar ?? 0), b: num(data?.edge_counts.brand ?? 0) })}
        right={<Select value={seg ?? ""} onChange={(e) => setSeg(e.target.value || null)} className="w-72">
          <option value="">{t("galaxy.allSegments")}</option>
          {[...(data?.segments ?? [])].sort((a, b) => (b.revenue_est ?? 0) - (a.revenue_est ?? 0)).map((s) =>
            <option key={s.segment_id} value={s.segment_id}>{truncate(s.segment_label, 50)}</option>)}
        </Select>} />
      <ErrorNote error={error} />
      {data?.lod && data.lod.shown < data.lod.total && (
        <div className="mx-6 mb-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-1.5 text-xs text-warn">
          {t("galaxy.lod", { n: num(data.lod.shown), t: num(data.lod.total), s: data.lod.revenue_share_shown == null ? "—" : pct(data.lod.revenue_share_shown, 1) })}
        </div>)}
      <div className="mx-6 mb-3 flex flex-wrap items-center gap-3 text-xs text-ink-3">
        <label className="flex items-center gap-1">{t("galaxy.colour")}
          <Select value={trait} onChange={(e) => { setTrait(e.target.value as Trait); setIsolate(null); }} className="w-40">
            {TRAITS.map((k) => <option key={k} value={k}>{t(`galaxy.trait.${k}`)}</option>)}</Select></label>
        <label className="flex items-center gap-1">{t("galaxy.size")}
          <Select value={sizeBy} onChange={(e) => setSizeBy(e.target.value as SizeBy)} className="w-36">
            {(["revenue", "units", "uniform"] as SizeBy[]).map((k) => <option key={k} value={k}>{t(`galaxy.sizeBy.${k}`)}</option>)}</Select></label>
        <span className="flex items-center gap-2">{t("galaxy.links")}
          {(["similar", "brand", "segment"] as EdgeType[]).map((e) => (
            <button key={e} onClick={() => toggleEdge(e)} className={`flex items-center gap-1 rounded-md border px-2 py-1 ${edgeTypes.has(e) ? "border-accent text-ink" : "border-line"}`}>
              <span className="h-0.5 w-4" style={{ background: EDGE_COLOR[e] }} />{t(`galaxy.edge.${e}`)}</button>))}
        </span>
        <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("galaxy.search")} className="w-48" />
        <Input type="number" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} placeholder={t("galaxy.maxPrice")} className="w-28" />
        <label className="flex items-center gap-1">{t("galaxy.minOpp")}
          <input type="range" min={0} max={80} step={5} value={minOpp} onChange={(e) => setMinOpp(Number(e.target.value))} />
          <span className="w-6 font-mono text-ink">{minOpp}</span></label>
        {visible && <span>{t("galaxy.matching", { a: num(visible.size), b: num(products.length) })}</span>}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 px-6 pb-4 xl:grid-cols-[1fr_360px]">
        <Card className="relative h-[640px] overflow-hidden">
          {data && <Galaxy ariaLabel={t("a11y.view3dNote")} products={products} segments={data.segments} edges={data.edges} edgeTypes={edgeTypes} color={color} size={size}
            visible={visible} selected={sel?.product_id ?? null} labelSegments={labelSegments} onSelect={setSel}
            tooltip={(p) => <>
              <div className="text-ink-3">{t("galaxy.revenue")} {moneyShort(p.revenue_est)} ({moneyShort(p.revenue_lo)}–{moneyShort(p.revenue_hi)})</div>
              <div className="text-ink-3">{t("galaxy.opportunity")} {p.opportunity_score?.toFixed(0) ?? "—"} · {t(`galaxy.trait.${trait}`)}: {traitValue(p)}</div>
            </>} />}
          {/* legend: identity never by colour alone -- names, counts, click to isolate */}
          <div className="absolute bottom-3 left-3 max-w-[340px] rounded-lg border border-line bg-panel/90 p-2 text-[11px]">
            <div className="mb-1 font-semibold uppercase tracking-wider text-ink-3">{t(`galaxy.trait.${trait}`)}</div>
            {cats ? <>
              {cats.order.slice(0, 7).map((k) => (
                <button key={k} onClick={() => setIsolate(isolate === k ? null : k)} className={`flex w-full items-center gap-2 text-left ${isolate && isolate !== k ? "opacity-40" : ""}`}>
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: cats.colors.get(k) }} />
                  <span className="min-w-0 flex-1 truncate">{trait === "entrant" ? t(`galaxy.entrant.${k}`) : k}</span><span className="font-mono text-ink-3">{cats.counts.get(k)}</span>
                </button>))}
              {cats.other > 0 && <button onClick={() => setIsolate(isolate === "__other" ? null : "__other")} className={`flex w-full items-center gap-2 text-left ${isolate && isolate !== "__other" ? "opacity-40" : ""}`}>
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: OTHER }} /><span className="flex-1">{t("galaxy.other")}</span><span className="font-mono text-ink-3">{cats.other}</span></button>}
            </> : trait === "price_quartile" ? (
              <div className="flex gap-1">{[1, 2, 3, 4].map((q) => <span key={q} className="flex items-center gap-1"><span className="h-2.5 w-4 rounded" style={{ background: SEQ[[1, 3, 5, 8][q - 1]] }} />Q{q}</span>)}
                <span className="text-ink-3">({t("galaxy.cheapest")} → {t("galaxy.priciest")})</span></div>
            ) : (
              <div><div className="h-2.5 w-56 rounded" style={{ background: `linear-gradient(to right, ${SEQ.join(",")})` }} />
                <div className="flex w-56 justify-between font-mono text-ink-3"><span>0</span><span>{trait === "opportunity" ? "100" : "≥ 60%"}</span></div></div>
            )}
            <div className="mt-1 text-ink-3">{t(`galaxy.sizeNote.${sizeBy}`)}</div>
          </div>
        </Card>
        <Card className="flex h-[640px] min-h-0 flex-col">
          <CardHeader title={sel ? t("galaxy.product") : t("galaxy.segments")} subtitle={sel ? sel.brand ?? undefined : search.trim() && visible ? t("galaxy.matching", { a: num(visible.size), b: num(products.length) }) : t("galaxy.byRevenue")} />
          {sel && links ? (
            <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
              <div className="space-y-2 p-4 text-sm">
                <div className="font-medium">{sel.title}</div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div>{t("galaxy.price")}<div className="font-mono text-sm">{money(sel.price, 2)}</div></div>
                  <div>{t("galaxy.units")}<div className="font-mono text-sm">{num(sel.units_est, 1)}</div><div className="text-[10px] text-ink-3">{num(sel.units_lo, 0)}–{num(sel.units_hi, 0)}</div></div>
                  <div>{t("galaxy.revenue")}<div className="font-mono text-sm">{moneyShort(sel.revenue_est)}</div><div className="text-[10px] text-ink-3">{moneyShort(sel.revenue_lo)}–{moneyShort(sel.revenue_hi)}</div></div>
                  <div>{t("galaxy.opportunity")}<div className="font-mono text-sm">{sel.opportunity_score?.toFixed(0) ?? "—"}</div></div>
                  <div>{t("galaxy.trait.margin")}<div className="font-mono text-sm">{sel.margin_rate == null ? "—" : pct(sel.margin_rate)}</div></div>
                  <div>{t("galaxy.trait.price_quartile")}<div className="font-mono text-sm">{sel.price_quartile ? `Q${sel.price_quartile}` : "—"}</div></div>
                </div>
                <div className="text-xs text-ink-3">{sel.segment_label}</div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => toggleCompare(sel)}>{compare.some((x) => x.product_id === sel.product_id) ? t("galaxy.uncompare") : t("galaxy.compare")}</Button>
                  <Link href={`/products/${sel.product_id}`} className="self-center text-xs text-accent hover:underline">{t("galaxy.open")} →</Link>
                </div>
              </div>
              {([["similar", links.similar.map((x) => ({ p: x.p, extra: x.score == null ? undefined : `sim ${x.score.toFixed(2)}` }))],
                 ["brand", links.brand.map((p) => ({ p, extra: undefined }))], ["segment", links.segPeers.slice(0, 8).map((p) => ({ p, extra: undefined }))]] as
                [EdgeType, { p: GalaxyProduct; extra?: string }[]][]).map(([k, rows]) => (
                <div key={k} className="border-t border-line py-1">
                  <div className="flex items-center gap-2 px-4 py-1 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
                    <span className="h-0.5 w-4" style={{ background: EDGE_COLOR[k] }} />{t(`galaxy.edge.${k}`)} ({k === "segment" ? links.segPeers.length : rows.length})</div>
                  {rows.length === 0 ? <div className="px-4 pb-1 text-xs text-ink-3">—</div> : rows.slice(0, 8).map((r) => <PLink key={r.p.product_id} p={r.p} extra={r.extra} />)}
                </div>
              ))}
              <button className="px-4 py-2 text-xs text-ink-3 hover:text-ink" onClick={() => setSel(null)}>← {t("galaxy.segments")}</button>
            </div>
          ) : search.trim() && visible ? (
            <div className="min-h-0 flex-1 overflow-y-auto py-1 scrollbar-thin">
              {products.filter((p) => visible.has(p.product_id)).slice(0, 60).map((p) => <PLink key={p.product_id} p={p} />)}
            </div>
          ) : (
            <div className="min-h-0 flex-1 divide-y divide-line overflow-y-auto scrollbar-thin">
              {[...(data?.segments ?? [])].sort((a, b) => (b.revenue_est ?? 0) - (a.revenue_est ?? 0)).map((s) => (
                <button key={s.segment_id} onClick={() => setSeg(seg === s.segment_id ? null : s.segment_id)}
                  className={`flex w-full items-center gap-2 px-4 py-2 text-left text-sm hover:bg-panel-2 ${seg === s.segment_id ? "bg-accent/10" : ""}`}>
                  <span className="min-w-0 flex-1 truncate">{s.segment_label}</span>
                  <span className="font-mono text-xs text-ink-3">{moneyShort(s.revenue_est)}</span>
                  <span className="w-8 text-right font-mono text-xs">{s.opportunity_index?.toFixed(0) ?? "—"}</span>
                </button>
              ))}
            </div>
          )}
        </Card>
      </div>
      {compare.length > 0 && (
        <Card className="mx-6 mb-6">
          <CardHeader title={t("galaxy.compareTitle")} right={<button className="text-xs text-accent" onClick={() => setCompare([])}>{t("galaxy.clear")}</button>} />
          <div className="overflow-x-auto" tabIndex={0}>
            <table className="w-full text-sm">
              <tbody className="divide-y divide-line">
                {([
                  [t("galaxy.product"), (p: GalaxyProduct) => <Link href={`/products/${p.product_id}`} className="hover:text-accent">{truncate(p.title, 70)}</Link>],
                  [t("comp.brand"), (p: GalaxyProduct) => p.brand ?? "—"],
                  [t("galaxy.price"), (p: GalaxyProduct) => money(p.price, 2)],
                  [t("galaxy.units"), (p: GalaxyProduct) => `${num(p.units_est, 1)} (${num(p.units_lo, 0)}–${num(p.units_hi, 0)})`],
                  [t("galaxy.revenue"), (p: GalaxyProduct) => `${moneyShort(p.revenue_est)} (${moneyShort(p.revenue_lo)}–${moneyShort(p.revenue_hi)})`],
                  [t("galaxy.opportunity"), (p: GalaxyProduct) => p.opportunity_score?.toFixed(0) ?? "—"],
                  [t("galaxy.trait.margin"), (p: GalaxyProduct) => (p.margin_rate == null ? "—" : pct(p.margin_rate))],
                  [t("galaxy.trait.segment"), (p: GalaxyProduct) => truncate(p.segment_label ?? "", 50)],
                  [t("common.listings"), (p: GalaxyProduct) => p.listing_count],
                ] as [string, (p: GalaxyProduct) => React.ReactNode][]).map(([label, f]) => (
                  <tr key={label}><td className="w-36 px-4 py-2 text-[11px] uppercase text-ink-3">{label}</td>
                    {compare.map((p) => <td key={p.product_id} className="px-3 py-2 align-top">{f(p)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

export default function GalaxyPage() {
  return <Suspense><GalaxyView /></Suspense>;
}
