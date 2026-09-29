"use client";
import { useEffect, useRef, useState } from "react";

/** A number that counts up to its value when it first appears (and eases between later values). Respects
 *  reduced motion. `format` renders the intermediate values exactly like the final one. */
export function AnimatedNumber({ value, format, duration = 700 }: { value: number | null | undefined; format: (v: number) => string; duration?: number }) {
  const [shown, setShown] = useState<number | null>(null);
  const from = useRef(0);
  useEffect(() => {
    if (value == null || !Number.isFinite(value)) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const start = performance.now(), a = from.current, b = value;
    let raf = 0;
    const tick = (now: number) => {
      const k = reduce ? 1 : Math.min(1, (now - start) / duration);
      const e = 1 - Math.pow(1 - k, 3);
      setShown(a + (b - a) * e);
      if (k < 1) raf = requestAnimationFrame(tick); else from.current = b;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  if (value == null || !Number.isFinite(value)) return <>—</>;
  return <span className="tabular-nums">{format(shown ?? 0)}</span>;
}

/** A 0-100 score as a ring. The number is text (never colour alone); colour follows the magnitude ramp. */
export function ScoreRing({ score, size = 44, color, label }: { score: number | null | undefined; size?: number; color: string; label?: string }) {
  const r = (size - 6) / 2, c = 2 * Math.PI * r;
  const v = score == null ? 0 : Math.max(0, Math.min(100, score));
  return (
    <span className="relative inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size }}
      role="img" aria-label={label ? `${label}: ${score == null ? "—" : score.toFixed(0)}` : undefined}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth={3} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={3} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - v / 100)} style={{ transition: "stroke-dashoffset .8s ease" }} />
      </svg>
      <span className="absolute font-mono text-[11px] font-semibold text-ink">{score == null ? "—" : score.toFixed(0)}</span>
    </span>
  );
}
