export const money = (v?: number | null, digits = 0) =>
  v == null || Number.isNaN(v) ? "—" : `$${v.toLocaleString("en-US", { maximumFractionDigits: digits })}`;

export const moneyShort = (v?: number | null) => {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
};

export const num = (v?: number | null, digits = 0) =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: digits });

export const pct = (v?: number | null, digits = 0) => (v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(digits)}%`);

export const truncate = (s: string | null | undefined, n: number) => (!s ? "" : s.length > n ? `${s.slice(0, n - 1)}…` : s);
