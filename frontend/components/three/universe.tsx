"use client";
import { useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Html, OrbitControls, Stars } from "@react-three/drei";
import { useReducedMotion } from "@/lib/theme";
import * as THREE from "three";
import type { UniverseNode } from "@/lib/api";
import { opportunityColor } from "@/lib/colors";
import { moneyShort, truncate } from "@/lib/format";

type Placed = { node: UniverseNode; pos: THREE.Vector3; r: number; depth: number; parent?: THREE.Vector3 };

function ring(n: number, radius: number, tilt = 0, phase = 0) {
  return Array.from({ length: n }, (_, i) => {
    const a = phase + (i / Math.max(n, 1)) * Math.PI * 2;
    return new THREE.Vector3(Math.cos(a) * radius, Math.sin(a * 2) * tilt, Math.sin(a) * radius);
  });
}

function layout(root: UniverseNode, focus: string | null): Placed[] {
  const out: Placed[] = [];
  const maxVal = Math.max(1, ...(root.children ?? []).flatMap((b) => (b.children ?? []).map((c) => c.value ?? 0)));
  const size = (v?: number | null, base = 0.2, span = 0.42) => base + span * Math.sqrt((v ?? 0) / maxVal);
  const origin = new THREE.Vector3();
  out.push({ node: root, pos: origin, r: 0.7, depth: 0 });
  const branches = root.children ?? [];
  ring(branches.length, 7, 0.8).forEach((p, i) => {
    const b = branches[i];
    out.push({ node: b, pos: p, r: 0.42, depth: 1, parent: origin });
    const cats = b.children ?? [];
    ring(cats.length, 1.7 + cats.length * 0.12, 0.4, i).forEach((q, j) => {
      const c = cats[j];
      const cp = p.clone().add(q);
      out.push({ node: c, pos: cp, r: size(c.value), depth: 2, parent: p });
      if (focus === c.id) {
        const segs = (c.children ?? []).slice(0, 60);
        ring(segs.length, 1.1 + segs.length * 0.03, 0.3).forEach((s, k) => {
          const sv = segs[k];
          out.push({ node: sv, pos: cp.clone().add(s), r: 0.05 + 0.2 * Math.sqrt((sv.value ?? 0) / Math.max(1, c.value ?? 1)), depth: 3, parent: cp });
        });
      }
    });
  });
  return out;
}

function Node({ p, selected, onSelect, still }: { p: Placed; selected: boolean; onSelect: (n: UniverseNode) => void; still: boolean }) {
  const ref = useRef<THREE.Mesh>(null);
  const [hover, setHover] = useState(false);
  const color = p.depth <= 1 ? (p.depth === 0 ? "#e6eefb" : "#8b7bff") : opportunityColor(p.node.opportunity);
  const growth = p.node.growth ?? p.node.momentum ?? null;
  const glow = growth == null ? 0.25 : Math.min(1.6, 0.35 + Math.max(0, growth) * 2.5);  // glow = growth
  useFrame(({ clock }) => {
    if (!still && ref.current && growth != null && growth > 0.05)
      (ref.current.material as THREE.MeshStandardMaterial).emissiveIntensity = glow * (0.8 + 0.2 * Math.sin(clock.elapsedTime * 2));
  });
  const showLabel = hover || selected || p.depth <= 2;
  return (
    <group position={p.pos}>
      <mesh ref={ref} onClick={(e) => { e.stopPropagation(); onSelect(p.node); }}
        onPointerOver={(e) => { e.stopPropagation(); setHover(true); document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { setHover(false); document.body.style.cursor = "auto"; }}>
        <sphereGeometry args={[p.r, 32, 32]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={glow} roughness={0.35} />
      </mesh>
      {(selected || hover) && (
        <mesh>
          <sphereGeometry args={[p.r * 1.45, 32, 32]} />
          <meshBasicMaterial color={color} transparent opacity={0.12} />
        </mesh>
      )}
      {showLabel && (
        <Html position={[0, p.r + 0.18, 0]} center style={{ pointerEvents: "none" }} zIndexRange={[10, 0]}>
          <div className={`whitespace-nowrap rounded px-1.5 py-0.5 text-center text-[10px] ${hover || selected ? "bg-panel/95 text-ink" : "text-ink-2"}`}>
            {truncate(p.node.label, p.depth >= 3 ? 30 : 28)}
            {(hover || selected) && p.node.value != null && <div className="text-ink-3">{moneyShort(p.node.value)}/mo</div>}
          </div>
        </Html>
      )}
    </group>
  );
}

function Links({ placed }: { placed: Placed[] }) {
  const geom = useMemo(() => {
    const pts: number[] = [];
    placed.forEach((p) => { if (p.parent) pts.push(p.parent.x, p.parent.y, p.parent.z, p.pos.x, p.pos.y, p.pos.z); });
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return g;
  }, [placed]);
  return (
    <lineSegments geometry={geom}>
      <lineBasicMaterial color="#38d6ff" transparent opacity={0.16} />
    </lineSegments>
  );
}

export default function Universe({ root, focus, selected, onSelect, ariaLabel }: {
  root: UniverseNode; focus: string | null; selected: string | null; onSelect: (n: UniverseNode) => void; ariaLabel?: string;
}) {
  const placed = useMemo(() => layout(root, focus), [root, focus]);
  const still = useReducedMotion(); // no auto-rotation or pulsing when the viewer asked for reduced motion
  return (
    // Scenes keep the dark viewport in both themes (surface-dark re-applies the dark tokens to labels).
    <div className="surface-dark h-full w-full" role="img" aria-label={ariaLabel}>
    <Canvas camera={{ position: [0, 12, 19], fov: 50 }} dpr={[1, 2]} onPointerMissed={() => onSelect(root)}>
      <color attach="background" args={["#04070d"]} />
      <ambientLight intensity={0.35} />
      <pointLight position={[0, 10, 0]} intensity={60} />
      <Stars radius={80} depth={40} count={3000} factor={3} fade speed={0.3} />
      <Links placed={placed} />
      {placed.map((p) => <Node key={p.node.id} p={p} selected={selected === p.node.id} onSelect={onSelect} still={still} />)}
      <OrbitControls makeDefault autoRotate={!still} autoRotateSpeed={0.25} minDistance={4} maxDistance={40} />
    </Canvas>
    </div>
  );
}
