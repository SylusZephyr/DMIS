import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { post } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { makeQueryClient, QueryProvider } from "@/lib/query";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const gets = (m: ReturnType<typeof vi.fn>, path: string) => m.mock.calls.filter(([u, init]) => String(u) === `/api/v2${path}` && !(init as RequestInit | undefined)?.method).length;

function Show({ path, id }: { path: string; id: string }) {
  const { data, error, loading } = useApi<{ v: number }>(path);
  return <div data-testid={id}>{loading ? "loading" : error ? `error:${error.slice(0, 3)}` : `v=${data?.v}`}</div>;
}
function client() {
  const c = makeQueryClient();
  c.setDefaultOptions({ queries: { ...c.getDefaultOptions().queries, retryDelay: 0 } });
  return c;
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("useApi (TanStack Query)", () => {
  it("de-duplicates identical requests and serves remounts from cache", async () => {
    const fetchMock = vi.fn(async () => json({ v: 7 }));
    vi.stubGlobal("fetch", fetchMock);
    const c = client();
    const { unmount } = render(<QueryClientProvider client={c}><Show path="/markets" id="a" /><Show path="/markets" id="b" /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByTestId("a").textContent).toBe("v=7"));
    expect(screen.getByTestId("b").textContent).toBe("v=7");
    expect(gets(fetchMock, "/markets")).toBe(1);
    unmount();
    render(<QueryClientProvider client={c}><Show path="/markets" id="c" /></QueryClientProvider>);
    expect(screen.getByTestId("c").textContent).toBe("v=7"); // synchronously from cache, no loading state
    expect(gets(fetchMock, "/markets")).toBe(1);
  });

  it("does not retry 4xx but retries 5xx", async () => {
    const fetchMock = vi.fn(async (u: string) => (u.endsWith("/missing") ? json({ detail: "nope" }, 404) : json({ detail: "boom" }, 503)));
    vi.stubGlobal("fetch", fetchMock);
    render(<QueryClientProvider client={client()}><Show path="/missing" id="a" /><Show path="/flaky" id="b" /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByTestId("a").textContent).toBe("error:404"));
    await waitFor(() => expect(screen.getByTestId("b").textContent).toBe("error:503"));
    expect(gets(fetchMock, "/missing")).toBe(1);
    expect(gets(fetchMock, "/flaky")).toBe(3);
  });

  it("mutating POSTs invalidate cached GETs; read-only POSTs do not", async () => {
    let n = 0;
    const fetchMock = vi.fn(async (_u: string, init?: RequestInit) => (init?.method === "POST" ? json({ ok: true }) : json({ v: ++n })));
    vi.stubGlobal("fetch", fetchMock);
    render(<QueryProvider><Show path="/projects" id="a" /></QueryProvider>);
    await waitFor(() => expect(screen.getByTestId("a").textContent).toBe("v=1"));
    await post("/launch/simulate", { title: "x", price: 1 });
    await new Promise((r) => setTimeout(r, 20));
    expect(gets(fetchMock, "/projects")).toBe(1);
    await post("/projects", { title: "x" });
    await waitFor(() => expect(screen.getByTestId("a").textContent).toBe("v=2"));
    expect(gets(fetchMock, "/projects")).toBe(2);
  });
});
