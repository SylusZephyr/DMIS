"use client";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import { useReducedMotion } from "@/lib/theme";
import * as THREE from "three";
import { moneyShort, num, truncate } from "@/lib/format";

import { EDGE_COLOR, type EdgeType, type GalaxyEdge, type GalaxyProduct, type GalaxySegment } from "./galaxy-types";
export { EDGE_COLOR, type EdgeType, type GalaxyEdge, type GalaxyProduct, type GalaxySegment };

type Pos = [number, number, number];

/** Robust framing: centre on the median, scale so the 90th-percentile radius is 5 scene units. */
function layout(products: GalaxyProduct[]): Map<string, Pos> {
  const med = (a: number[]) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : 0; };
  const c = [med(products.map((p) => p.gx)), med(products.map((p) => p.gy)), med(products.map((p) => p.gz))];
  const r = products.map((p) => Math.hypot(p.gx - c[0], p.gy - c[1], p.gz - c[2])).sort((a, b) => a - b);
  const k = 5 / (r[Math.floor(r.length * 0.9)] || 1);
  return new Map(products.map((p) => [p.product_id, [(p.gx - c[0]) * k, (p.gy - c[1]) * k, (p.gz - c[2]) * k] as Pos]));
}

function StarField({ products, pos, color, size, visible, selected, onHover, onClick }: {
  products: GalaxyProduct[]; pos: Map<string, Pos>; color: (p: GalaxyProduct) => string; size: (p: GalaxyProduct) => number;
  visible: Set<string> | null; selected: string | null; onHover: (i: number | null) => void; onClick: (i: number) => void;
}) {
  const ref = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    const o = new THREE.Object3D();
    const c = new THREE.Color();
    products.forEach((p, i) => {
      const hidden = visible != null && !visible.has(p.product_id);
      o.position.set(...pos.get(p.product_id)!);
      o.scale.setScalar(size(p) * (hidden ? 0.5 : 1) * (p.product_id === selected ? 1.6 : 1));
      o.updateMatrix();
      m.setMatrixAt(i, o.matrix);
      c.set(hidden ? "#1b2433" : color(p));
      m.setColorAt(i, c);
    });
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [products, pos, color, size, visible, selected]);
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, products.length]}
      onPointerMove={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); onHover(e.instanceId ?? null); document.body.style.cursor = "pointer"; }}
      onPointerOut={() => { onHover(null); document.body.style.cursor = "auto"; }}
      onClick={(e: ThreeEvent<MouseEvent>) => { e.stopPropagation(); if (e.instanceId != null) onClick(e.instanceId); }}>
      <sphereGeometry args={[1, 16, 16]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  );
}

function Links({ segs, color, opacity }: { segs: number[]; color: string; opacity: number }) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(segs, 3));
    return g;
  }, [segs]);
  return <lineSegments geometry={geom}><lineBasicMaterial color={color} transparent opacity={opacity} /></lineSegments>;
}

export default function Galaxy({ products, segments, edges, edgeTypes, color, size, visible, selected, labelSegments, onSelect, tooltip, ariaLabel }: {
  products: GalaxyProduct[]; segments: GalaxySegment[]; edges: GalaxyEdge[]; edgeTypes: Set<EdgeType>;
  color: (p: GalaxyProduct) => string; size: (p: GalaxyProduct) => number; visible: Set<string> | null; selected: string | null;
  labelSegments: Set<string>; onSelect: (p: GalaxyProduct) => void; tooltip: (p: GalaxyProduct) => React.ReactNode; ariaLabel?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const pos = useMemo(() => layout(products), [products]);
  const hubs = useMemo(() => {
    const acc = new Map<string, { s: Pos; n: number }>();
    products.forEach((p) => {
      const q = pos.get(p.product_id)!;
      const a = acc.get(p.segment_id) ?? { s: [0, 0, 0] as Pos, n: 0 };
      acc.set(p.segment_id, { s: [a.s[0] + q[0], a.s[1] + q[1], a.s[2] + q[2]], n: a.n + 1 });
    });
    return new Map([...acc].map(([k, v]) => [k, [v.s[0] / v.n, v.s[1] / v.n, v.s[2] / v.n] as Pos]));
  }, [products, pos]);
  const lines = useMemo(() => {
    const keep = (id: string) => visible == null || visible.has(id);
    const out: Record<EdgeType, number[]> = { similar: [], brand: [], segment: [] };
    edges.forEach((e) => {
      if (!edgeTypes.has(e.type) || !keep(e.source) || !keep(e.target)) return;
      const a = pos.get(e.source), b = pos.get(e.target);
      if (a && b) out[e.type].push(...a, ...b);
    });
    if (edgeTypes.has("segment")) products.forEach((p) => {
      if (!keep(p.product_id)) return;
      const h = hubs.get(p.segment_id);
      if (h) out.segment.push(...pos.get(p.product_id)!, ...h);
    });
    return out;
  }, [edges, edgeTypes, products, pos, hubs, visible]);
  const hp = hover != null ? products[hover] : null;
  const segName = new Map(segments.map((s) => [s.segment_id, s.segment_label]));
  const still = useReducedMotion(); // no auto-rotation when the viewer asked for reduced motion
  return (
    // Scenes keep the dark viewport in both themes (surface-dark re-applies the dark tokens to labels).
    <div className="surface-dark h-full w-full" role="img" aria-label={ariaLabel}>
    <Canvas camera={{ position: [0, 1.5, 8.5], fov: 50 }} dpr={[1, 2]}>
      <color attach="background" args={["#03060b"]} />
      {(Object.keys(lines) as EdgeType[]).map((k) => lines[k].length > 0 &&
        <Links key={k} segs={lines[k]} color={EDGE_COLOR[k]} opacity={k === "segment" ? 0.18 : 0.45} />)}
      <StarField products={products} pos={pos} color={color} size={size} visible={visible} selected={selected}
        onHover={setHover} onClick={(i) => onSelect(products[i])} />
      {[...labelSegments].map((sid) => hubs.get(sid) && (
        <Html key={sid} position={hubs.get(sid)!} center style={{ pointerEvents: "none" }} zIndexRange={[5, 0]}>
          <div className="whitespace-nowrap rounded bg-bg/70 px-1 text-[10px] text-ink-2">{truncate((segName.get(sid) ?? sid).split(" · ").pop() ?? sid, 36)}</div>
        </Html>
      ))}
      {hp && (
        <Html position={pos.get(hp.product_id)!} center style={{ pointerEvents: "none", transform: "translateY(-70px)" }} zIndexRange={[20, 10]}>
          <div className="w-72 rounded-lg border border-line bg-panel/95 p-2 text-[11px] text-ink shadow-xl">
            <div className="font-medium">{truncate(hp.title, 90)}</div>
            <div className="mt-1 text-ink-3">{hp.brand ?? "—"} · {moneyShort(hp.price)} · {num(hp.units_est, 1)}/mo</div>
            {tooltip(hp)}
          </div>
        </Html>
      )}
      <OrbitControls makeDefault autoRotate={!still && hover == null && selected == null} autoRotateSpeed={0.25} minDistance={3} maxDistance={40} />
    </Canvas>
    </div>
  );
}
