"use client";
import { useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Html, OrbitControls, Stars } from "@react-three/drei";
import { useReducedMotion } from "@/lib/theme";
import * as THREE from "three";
import { opportunityColor } from "@/lib/colors";
import { moneyShort } from "@/lib/format";

export type GeoCountry = {
  country: string; iso2: string; lat: number; lon: number; suppliers: number; markets: string[];
  market_revenue: number; opportunity: number | null; avg_supplier_score: number | null;
};

const R = 2;

export function latLon(lat: number, lon: number, r = R) {
  const phi = ((90 - lat) * Math.PI) / 180;
  const theta = ((lon + 180) * Math.PI) / 180;
  return new THREE.Vector3(-r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta));
}

function Graticule() {
  const geom = useMemo(() => {
    const pts: number[] = [];
    for (let lat = -75; lat <= 75; lat += 15)
      for (let lon = -180; lon < 180; lon += 3) {
        const a = latLon(lat, lon, R + 0.002), b = latLon(lat, lon + 3, R + 0.002);
        pts.push(a.x, a.y, a.z, b.x, b.y, b.z);
      }
    for (let lon = -180; lon < 180; lon += 15)
      for (let lat = -90; lat < 90; lat += 3) {
        const a = latLon(lat, lon, R + 0.002), b = latLon(lat + 3, lon, R + 0.002);
        pts.push(a.x, a.y, a.z, b.x, b.y, b.z);
      }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return g;
  }, []);
  return (
    <lineSegments geometry={geom}>
      <lineBasicMaterial color="#1c5a7a" transparent opacity={0.35} />
    </lineSegments>
  );
}

function Beacon({ c, maxRev, maxSup, onSelect, selected, tip }: {
  tip?: (c: GeoCountry) => string;
  c: GeoCountry; maxRev: number; maxSup: number; onSelect: (c: GeoCountry) => void; selected: boolean;
}) {
  const [hover, setHover] = useState(false);
  const activity = Math.max(c.market_revenue / (maxRev || 1), c.suppliers / (maxSup || 1));
  const h = 0.12 + 0.9 * Math.sqrt(activity);
  const base = latLon(c.lat, c.lon);
  const dir = base.clone().normalize();
  const mid = base.clone().add(dir.clone().multiplyScalar(h / 2));
  const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
  const color = c.opportunity != null ? opportunityColor(c.opportunity) : "#ff9f5a";
  return (
    <group>
      <mesh position={mid} quaternion={quat} onClick={(e) => { e.stopPropagation(); onSelect(c); }}
        onPointerOver={(e) => { e.stopPropagation(); setHover(true); document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { setHover(false); document.body.style.cursor = "auto"; }}>
        <cylinderGeometry args={[0.022, 0.022, h, 12]} />
        <meshBasicMaterial color={color} transparent opacity={hover || selected ? 1 : 0.85} />
      </mesh>
      <mesh position={base.clone().add(dir.clone().multiplyScalar(h))}>
        <sphereGeometry args={[hover || selected ? 0.06 : 0.045, 16, 16]} />
        <meshBasicMaterial color={color} />
      </mesh>
      {(hover || selected) && (
        <Html position={base.clone().add(dir.clone().multiplyScalar(h + 0.12))} center style={{ pointerEvents: "none" }}>
          <div className="whitespace-nowrap rounded-md border border-line bg-panel/95 px-2 py-1 text-[11px] text-ink shadow-lg">
            <b>{c.country}</b> · {tip ? tip(c) : `${c.suppliers} suppliers${c.markets.length ? ` · ${c.markets.length} markets · ${moneyShort(c.market_revenue)}/mo` : ""}`}
          </div>
        </Html>
      )}
    </group>
  );
}

function Earth({ countries, onSelect, selected, tip, still }: { countries: GeoCountry[]; onSelect: (c: GeoCountry) => void; selected?: string; tip?: (c: GeoCountry) => string; still: boolean }) {
  const grp = useRef<THREE.Group>(null);
  const [drag, setDrag] = useState(false);
  useFrame((_, dt) => { if (grp.current && !drag && !still) grp.current.rotation.y += dt * 0.05; });
  const maxRev = Math.max(0, ...countries.map((c) => c.market_revenue));
  const maxSup = Math.max(0, ...countries.map((c) => c.suppliers));
  return (
    <group ref={grp} onPointerDown={() => setDrag(true)} onPointerUp={() => setDrag(false)}>
      <mesh>
        <sphereGeometry args={[R, 96, 96]} />
        <meshStandardMaterial color="#071b2c" emissive="#04121f" roughness={0.9} metalness={0.1} />
      </mesh>
      <mesh scale={1.035}>
        <sphereGeometry args={[R, 64, 64]} />
        <meshBasicMaterial color="#38d6ff" transparent opacity={0.06} side={THREE.BackSide} />
      </mesh>
      <Graticule />
      {countries.map((c) => (
        <Beacon key={c.iso2} c={c} maxRev={maxRev} maxSup={maxSup} onSelect={onSelect} selected={selected === c.iso2} tip={tip} />
      ))}
    </group>
  );
}

export default function Globe({ countries, onSelect, selected, tip, ariaLabel }: { countries: GeoCountry[]; onSelect: (c: GeoCountry) => void; selected?: string; tip?: (c: GeoCountry) => string; ariaLabel?: string }) {
  const still = useReducedMotion(); // no spinning when the viewer asked for reduced motion
  return (
    // Scenes keep the dark viewport in both themes (surface-dark re-applies the dark tokens to labels).
    <div className="surface-dark h-full w-full" role="img" aria-label={ariaLabel}>
    <Canvas camera={{ position: [0, 1.6, 7.2], fov: 45 }} dpr={[1, 2]}>
      <color attach="background" args={["#04070d"]} />
      <ambientLight intensity={0.6} />
      <pointLight position={[6, 4, 6]} intensity={40} color="#7fdcff" />
      <Stars radius={60} depth={30} count={2500} factor={3} fade speed={0.4} />
      <Earth countries={countries} onSelect={onSelect} selected={selected} tip={tip} still={still} />
      <OrbitControls enablePan={false} minDistance={3} maxDistance={10} />
    </Canvas>
    </div>
  );
}
