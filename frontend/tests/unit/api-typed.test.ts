import { describe, expect, it } from "vitest";
import { apiPath } from "@/lib/api-typed";

describe("apiPath", () => {
  it("fills and encodes path parameters", () => {
    expect(apiPath("/markets/{market}/scope", { market: "denture base/EU" })).toBe("/markets/denture%20base%2FEU/scope");
    expect(apiPath("/markets")).toBe("/markets");
  });
});
