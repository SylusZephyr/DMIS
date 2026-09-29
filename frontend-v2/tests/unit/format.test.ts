import { describe, expect, it } from "vitest";
import { money, moneyShort, num, pct, truncate } from "@/lib/format";

describe("lib/format", () => {
  it("money rounds to the requested digits and uses thousands separators", () => {
    expect(money(1234.5)).toBe("$1,235");
    expect(money(1234.567, 2)).toBe("$1,234.57");
    expect(money(0)).toBe("$0");
  });
  it("renders missing values as an em dash", () => {
    for (const f of [money, moneyShort, num, pct]) {
      expect(f(null)).toBe("—");
      expect(f(undefined)).toBe("—");
      expect(f(Number.NaN)).toBe("—");
    }
  });
  it("moneyShort picks K / M / B", () => {
    expect(moneyShort(999)).toBe("$999");
    expect(moneyShort(2_500)).toBe("$2.5K");
    expect(moneyShort(1_500_000)).toBe("$1.50M");
    expect(moneyShort(1_200_000_000)).toBe("$1.20B");
    expect(moneyShort(-2_500)).toBe("$-2.5K");
  });
  it("num and pct", () => {
    expect(num(1234.5)).toBe("1,235");
    expect(num(1234.56, 1)).toBe("1,234.6");
    expect(pct(0.123)).toBe("12%");
    expect(pct(0.1234, 1)).toBe("12.3%");
    expect(pct(1)).toBe("100%");
  });
  it("truncate keeps short strings and ellipsises long ones to n characters", () => {
    expect(truncate("abc", 5)).toBe("abc");
    expect(truncate("abcdef", 4)).toBe("abc…");
    expect(truncate("abcdef", 4)).toHaveLength(4);
    expect(truncate(null, 3)).toBe("");
  });
});
