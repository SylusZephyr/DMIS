"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type Sigma from "sigma";
import { Badge, Button, Card, CardHeader, Input } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { get, type GraphEdge, type GraphNode } from "@/lib/api";
import { truncate } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { CATEGORICAL, OTHER } from "@/lib/viz";

// 8 decision-relevant kinds carry the validated categorical hues; the rest fold into OTHER and are named in panels
const KEY_KINDS = ["Category", "ProductFamily", "Segment", "Product", "Brand", "Feature", "Recommendation", "Supplier"];
const KIND_COLOR: Record<string, string> = Object.fromEntries(KEY_KINDS.map((k, i) => [k, CATEGORICAL[i]]));
const ALL_KINDS = [...KEY_KINDS, "Industry", "Momentum", "Trend", "CustomerProblem", "Country", "Listing", "Seller", "Project"];
const SIZE: Record<string, number> = { Industry: 12, Category: 13, ProductFamily: 9, Segment: 7, Product: 4, Brand: 5, Feature: 6,
  Recommendation: 8, Supplier: 6, Momentum: 6, Trend: 5, CustomerProblem: 5, Country: 5, Listing: 2, Seller: 3, Project: 6 };
const BASIS_COLOR: Record<string, string> = { fact: "rgba(142,160,187,0.35)", estimate: "rgba(57,135,229,0.55)", test: "rgba(25,158,112,0.7)",
  similarity: "rgba(144,133,233,0.55)" };
const PATH_COLOR = "#f5b942";
// product-to-product edges are many and local: off by default so the structure reads, one click to show
const DENSE_RELS = new Set(["PRODUCT_SIMILAR_TO", "COMPETES_WITH", "HAS_LISTING", "SOLD_BY"]);

type Ev = { evidence?: string; basis?: string; strength?: number | null; metric?: string | null; value?: number | null;
  low?: number | null; high?: number | null; n?: number | null; q_value?: number | null };
type Hop = { from: GraphNode; to: GraphNode; rel: string; props: Ev; forward: boolean };
const kindColor = (k: string) => KIND_COLOR[k] ?? OTHER;

type Conn = { key: string; rel: string; props: Ev; other: GraphNode; out: boolean };
function incidentOf(g: Graph, id: string): Conn[] {
  if (!g.hasNode(id)) return [];
  return g.edges(id).map((k) => {
    const a = g.getEdgeAttributes(k);
    const other = g.source(k) === id ? g.target(k) : g.source(k);
    return { key: k, rel: a.rel as string, props: a.props as Ev, other: g.getNodeAttribute(other, "raw") as GraphNode, out: g.source(k) === id };
  }).sort((x, y) => (y.props.strength ?? 0) - (x.props.strength ?? 0));
}

function EvLine({ p, t }: { p: Ev; t: (k: string, v?: Record<string, string | number>) => string }) {
  return (
    <div className="text-[11px] text-ink-2">{p.evidence ?? "—"}
      <div className="text-ink-3">{t(`graph.basis.${p.basis ?? "fact"}`)}{p.strength != null && <> · {t("graph.strength")} {p.strength.toFixed(2)}</>}{p.n != null && <> · n = {p.n}</>}</div></div>
  );
}

function Explorer() {
  const { t } = useI18n();
  const start = useSearchParams().get("node") ?? "industry:dental";
  const box = useRef<HTMLDivElement>(null);
  const sigma = useRef<Sigma | null>(null);
  const graph = useRef(new Graph({ multi: true, type: "directed" }));
  const [kinds, setKinds] = useState<string[]>(ALL_KINDS.filter((k) => !["Listing", "Seller"].includes(k)));
  const [rels, setRels] = useState<Record<string, boolean>>({});
  const [minStrength, setMinStrength] = useState(0);
  const [sel, setSel] = useState<GraphNode | null>(null);
  const [selEdge, setSelEdge] = useState<{ source: GraphNode; target: GraphNode; rel: string; props: Ev } | null>(null);
  const [pathFrom, setPathFrom] = useState<GraphNode | null>(null);
  const [path, setPath] = useState<Hop[] | null>(null);
  const [pathNote, setPathNote] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<GraphNode[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [counts, setCounts] = useState({ n: 0, e: 0 });
  const [relCounts, setRelCounts] = useState<Record<string, number>>({});
  const [conn, setConn] = useState<Conn[]>([]);
  const filters = useRef({ rels, minStrength, path: new Set<string>() });

  const knownRels = useCallback(() => {
    const out: Record<string, number> = {};
    graph.current.forEachEdge((_, a) => { out[a.rel] = (out[a.rel] ?? 0) + 1; });
    return out;
  }, []);
  const refreshCounts = () => { setCounts({ n: graph.current.order, e: graph.current.size }); setRelCounts(knownRels()); };

  const expand = async (id: string, depth = 1) => {
    try {
      const r = await get<{ nodes: GraphNode[]; edges: GraphEdge[] }>(`/graph/explore?node=${encodeURIComponent(id)}&depth=${depth}&kinds=${kinds.join(",")}&limit=900`);
      const g = graph.current;
      const anchor = g.hasNode(id) ? g.getNodeAttributes(id) : { x: 0, y: 0 };
      r.nodes.forEach((n) => {
        if (!g.hasNode(n.id))
          g.addNode(n.id, { label: truncate(n.label, 40), size: SIZE[n.kind] ?? 4, color: kindColor(n.kind),
            x: anchor.x + (Math.random() - 0.5) * 10, y: anchor.y + (Math.random() - 0.5) * 10, raw: n });
      });
      r.edges.forEach((e) => {
        const key = `${e.source}|${e.rel}|${e.target}`;
        if (g.hasNode(e.source) && g.hasNode(e.target) && !g.hasEdge(key)) {
          const p = (e.props ?? {}) as Ev;
          g.addDirectedEdgeWithKey(key, e.source, e.target, { rel: e.rel, props: p, size: 0.5 + 2.5 * (p.strength ?? 0.3),
            color: BASIS_COLOR[p.basis ?? "fact"] ?? BASIS_COLOR.fact });
        }
      });
      forceAtlas2.assign(g, { iterations: 120, settings: { ...forceAtlas2.inferSettings(g), slowDown: 5, gravity: 1 } });
      setRels((old) => { const n = { ...old }; Object.keys(knownRels()).forEach((k) => { if (!(k in n)) n[k] = !DENSE_RELS.has(k); }); return n; });
      refreshCounts();
      setErr(null);
    } catch (e) { setErr((e as Error).message); }
  };

  useEffect(() => {
    filters.current = { rels, minStrength, path: filters.current.path };
    sigma.current?.refresh();
  }, [rels, minStrength]);

  useEffect(() => {
    let alive = true;
    (async () => {
      const { default: SigmaCls } = await import("sigma");
      if (!alive || !box.current) return;
      const s = new SigmaCls(graph.current, box.current, {
        renderEdgeLabels: false, enableEdgeEvents: true, labelColor: { color: "#c9d8ee" }, labelSize: 11, labelRenderedSizeThreshold: 6,
        defaultEdgeType: "arrow",
        edgeReducer: (edge, data) => {
          const f = filters.current;
          const onPath = f.path.has(edge);
          if (!onPath && (f.rels[data.rel] === false || (data.props?.strength ?? 1) < f.minStrength)) return { ...data, hidden: true };
          return onPath ? { ...data, color: PATH_COLOR, size: Math.max(3, data.size) } : data;
        },
      });
      s.on("clickNode", ({ node }) => { setSel(graph.current.getNodeAttribute(node, "raw")); setSelEdge(null); expand(node).then(() => setConn(incidentOf(graph.current, node))); });
      s.on("clickEdge", ({ edge }) => {
        const g = graph.current;
        const a = g.getEdgeAttributes(edge);
        setSelEdge({ source: g.getNodeAttribute(g.source(edge), "raw"), target: g.getNodeAttribute(g.target(edge), "raw"), rel: a.rel, props: a.props });
      });
      sigma.current = s;
      graph.current.clear();
      await expand(start, start.startsWith("industry:") ? 3 : 2);
    })();
    return () => { alive = false; sigma.current?.kill(); sigma.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start]);

  const search = async () => { if (q.trim()) setHits(await get<GraphNode[]>(`/graph/search?q=${encodeURIComponent(q)}&limit=15`)); };
  const explainPath = async (to: GraphNode) => {
    if (!pathFrom) return;
    const r = await get<{ nodes: GraphNode[]; edges: GraphEdge[]; note?: string | null }>(`/graph/path?source=${encodeURIComponent(pathFrom.id)}&target=${encodeURIComponent(to.id)}`);
    setPathNote(r.note ?? (r.nodes.length ? null : t("graph.noPath")));
    const g = graph.current;
    const keys = new Set<string>();
    const hops: Hop[] = [];
    r.nodes.forEach((n, i) => {
      if (!g.hasNode(n.id)) g.addNode(n.id, { label: truncate(n.label, 40), size: SIZE[n.kind] ?? 4, color: kindColor(n.kind),
        x: (Math.random() - 0.5) * 20, y: (Math.random() - 0.5) * 20, raw: n });
      if (i > 0) {
        const e = r.edges[i - 1];
        const key = `${e.source}|${e.rel}|${e.target}`;
        if (!g.hasEdge(key)) g.addDirectedEdgeWithKey(key, e.source, e.target, { rel: e.rel, props: e.props ?? {}, size: 2, color: PATH_COLOR });
        keys.add(key);
        hops.push({ from: r.nodes[i - 1], to: n, rel: e.rel, props: (e.props ?? {}) as Ev, forward: e.source === r.nodes[i - 1].id });
      }
    });
    filters.current.path = keys;
    setPath(hops);
    refreshCounts();
    sigma.current?.refresh();
  };

  const props = sel?.props ?? {};

  return (
    <div className="flex h-screen flex-col">
      <PageHeader title={t("graph.title")} subtitle={t("graph.subtitle")} />
      <ErrorNote error={err} />
      <div className="mx-6 mb-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-3">
        <span>{t("graph.edgeTypes")}</span>
        {Object.keys(rels).sort().map((r) => (
          <button key={r} onClick={() => setRels({ ...rels, [r]: !rels[r] })}
            className={`rounded-md border px-1.5 py-0.5 ${rels[r] ? "border-line text-ink-2" : "border-line opacity-35"}`}>{r} <span className="text-ink-3">{relCounts[r] ?? 0}</span></button>
        ))}
        <label className="ml-2 flex items-center gap-1">{t("graph.minStrength")}
          <input type="range" min={0} max={1} step={0.05} value={minStrength} onChange={(e) => setMinStrength(Number(e.target.value))} />
          <span className="w-8 font-mono text-ink">{minStrength.toFixed(2)}</span></label>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 px-6 pb-6 xl:grid-cols-[1fr_380px]">
        <Card className="surface-dark relative min-h-[520px] overflow-hidden">
          <div ref={box} className="absolute inset-0" />
          <div className="absolute bottom-3 left-3 max-w-[70%] rounded-lg border border-line bg-panel/90 p-2 text-[10px]">
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {ALL_KINDS.map((k) => (
                <button key={k} onClick={() => setKinds((ks) => (ks.includes(k) ? ks.filter((x) => x !== k) : [...ks, k]))}
                  className={`flex items-center gap-1 ${kinds.includes(k) ? "" : "opacity-35"}`}>
                  <span className="h-2 w-2 rounded-full" style={{ background: kindColor(k) }} />{t(`graph.kind.${k}`)}</button>
              ))}
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 text-ink-3">
              {Object.entries(BASIS_COLOR).map(([k, c]) => <span key={k} className="flex items-center gap-1"><span className="h-0.5 w-4" style={{ background: c }} />{t(`graph.basis.${k}`)}</span>)}
              <span className="flex items-center gap-1"><span className="h-0.5 w-4" style={{ background: PATH_COLOR }} />{t("graph.path")}</span>
              <span>{t("graph.widthNote")}</span>
            </div>
          </div>
          <div className="absolute right-3 top-3 text-[11px] text-ink-3">{t("graph.counts", { n: counts.n, e: counts.e })}</div>
        </Card>
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto scrollbar-thin">
          <Card>
            <CardHeader title={t("graph.find")} />
            <div className="flex gap-2 p-3">
              <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} placeholder={t("graph.findPh")} />
              <Button size="md" onClick={search}>{t("graph.go")}</Button>
            </div>
            <div className="max-h-40 divide-y divide-line overflow-y-auto scrollbar-thin">
              {hits.map((h) => (
                <button key={h.id} onClick={() => { setSel(h); setSelEdge(null); expand(h.id, 1).then(() => setConn(incidentOf(graph.current, h.id))); }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-panel-2">
                  <span className="h-2 w-2 rounded-full" style={{ background: kindColor(h.kind) }} />
                  <span className="min-w-0 flex-1 truncate">{h.label}</span><span className="text-ink-3">{t(`graph.kind.${h.kind}`)}</span>
                </button>
              ))}
            </div>
            <div className="flex gap-2 border-t border-line p-3">
              <Button variant="outline" size="sm" onClick={() => { graph.current.clear(); setSel(null); setSelEdge(null); setPath(null); filters.current.path = new Set(); expand(start, start.startsWith("industry:") ? 3 : 2); }}>{t("graph.reset")}</Button>
              {path && <Button variant="ghost" size="sm" onClick={() => { setPath(null); setPathFrom(null); filters.current.path = new Set(); sigma.current?.refresh(); }}>{t("graph.clearPath")}</Button>}
            </div>
          </Card>

          {selEdge && (
            <Card>
              <CardHeader title={t("graph.why")} subtitle={`${truncate(selEdge.source.label, 30)} —${selEdge.rel}→ ${truncate(selEdge.target.label, 30)}`} />
              <div className="p-4"><EvLine p={selEdge.props} t={t} />
                {selEdge.props.value != null && <div className="mt-1 font-mono text-[11px]">{selEdge.props.metric}: {selEdge.props.value.toFixed(3)}
                  {selEdge.props.low != null && selEdge.props.high != null && ` (${selEdge.props.low.toFixed(3)}–${selEdge.props.high.toFixed(3)})`}</div>}</div>
            </Card>
          )}

          {path && (
            <Card>
              <CardHeader title={t("graph.pathTitle")} subtitle={t("graph.pathSub", { n: path.length })} />
              {pathNote ? <div className="p-4 text-xs text-ink-3">{pathNote}</div> : (
                <ol className="space-y-2 p-4">
                  {path.map((h, i) => (
                    <li key={i} className="text-xs">
                      <div><span style={{ color: kindColor(h.from.kind) }}>{truncate(h.from.label, 34)}</span> <span className="font-mono text-ink-3">{h.forward ? `—${h.rel}→` : `←${h.rel}—`}</span> <span style={{ color: kindColor(h.to.kind) }}>{truncate(h.to.label, 34)}</span></div>
                      <EvLine p={h.props} t={t} />
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          )}

          <Card className="min-h-0">
            <CardHeader title={sel ? t(`graph.kind.${sel.kind}`) : t("graph.selection")} subtitle={sel ? truncate(sel.label, 80) : t("graph.clickNode")} />
            {sel && (
              <div className="space-y-1 p-4 text-xs">
                {Object.entries(props).filter(([k, v]) => v != null && v !== "" && !["image", "description_zh"].includes(k)).slice(0, 12).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3"><span className="text-ink-3">{k}</span>
                    <span className="truncate text-right font-mono">{typeof v === "number" ? (Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(3)) : String(v)}</span></div>
                ))}
                <div className="flex flex-wrap gap-2 pt-2">
                  {sel.kind === "Product" && <a className="text-accent" href={`/products/${String(props.product_id ?? sel.id.split(":")[1])}`}>{t("graph.openProduct")} →</a>}
                  {sel.kind === "Category" && <a className="text-accent" href={`/markets/${encodeURIComponent(sel.label)}`}>{t("graph.openMarket")} →</a>}
                  {!pathFrom || pathFrom.id === sel.id
                    ? <Button size="sm" variant="outline" onClick={() => setPathFrom(sel)}>{pathFrom?.id === sel.id ? t("graph.pathFromSet") : t("graph.pathFrom")}</Button>
                    : <Button size="sm" onClick={() => explainPath(sel)}>{t("graph.pathTo", { a: truncate(pathFrom.label, 24) })}</Button>}
                </div>
                <div className="pt-3 text-[11px] font-semibold uppercase tracking-wider text-ink-3">{t("graph.connections")}</div>
                <div className="divide-y divide-line">
                  {conn.slice(0, 25).map((c) => (
                    <button key={c.key} className="block w-full py-1.5 text-left hover:bg-panel-2"
                      onClick={() => setSelEdge({ source: c.out ? sel : c.other, target: c.out ? c.other : sel, rel: c.rel, props: c.props })}>
                      <div className="flex items-center gap-2"><span className="font-mono text-[10px] text-ink-3">{c.out ? "→" : "←"} {c.rel}</span>
                        <span className="h-2 w-2 rounded-full" style={{ background: kindColor(c.other.kind) }} /><span className="min-w-0 flex-1 truncate">{c.other.label}</span>
                        {c.props.strength != null && <span className="font-mono text-ink-3">{c.props.strength.toFixed(2)}</span>}</div>
                    </button>
                  ))}
                </div>
                <div className="pt-2"><Badge>{sel.id}</Badge></div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

export default function GraphPage() {
  return <Suspense><Explorer /></Suspense>;
}
