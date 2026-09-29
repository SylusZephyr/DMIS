"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Empty, Input, LiveStatus } from "@/components/ui/primitives";
import { PageHeader } from "@/components/page";
import { MarkdownLite } from "@/components/markdown-lite";
import { apiPost } from "@/lib/api-typed";
import { useI18n } from "@/lib/i18n";

type Answer = {
  question: string; intent: string; lang: string; mode: "computed" | "ai";
  scope: { markets: string[]; segments: [string, string][]; brand: string | null; basis: string };
  facts: { id: string; text: string; source: string }[]; answer: string; sources: string[]; followups: string[];
  ai_answer?: string; model?: string; prompt_version?: string; ai_status?: string; ai_note?: string;
};

// API paths in fact sources map to the page that shows the same numbers
function pageFor(src: string): string | null {
  const m = src.match(/^\/api\/v2\/markets\/([^/]+)\/(.*)$/);
  if (m) {
    const [, mk, rest] = m;
    if (rest.startsWith("competitors")) return `/competitors?market=${mk}`;
    if (rest.startsWith("segments/")) return `/markets/${mk}`;
    return `/markets/${mk}`;
  }
  if (src.startsWith("/api/v2/launch")) return "/launch";
  if (src.startsWith("/api/v2/methodology")) return "/methodology";
  return null;
}

export default function Analyst() {
  const { t, lang } = useI18n();
  const [q, setQ] = useState("");
  const [useAi, setUseAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [log, setLog] = useState<Answer[]>([]);
  const examples = [0, 1, 2, 3, 4, 5, 6].map((i) => t(`analyst.ex${i}`));
  const ask = async (question: string) => {
    if (!question.trim()) return;
    setBusy(true); setErr(null);
    try {
      const a = (await apiPost("/analyst/ask-v3", { question, use_ai: useAi, lang })) as Answer;
      setLog((l) => [a, ...l]); setQ("");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="pb-8">
      <PageHeader title={t("analyst.title")} subtitle={t("analyst.subtitle")} />
      <Card className="mx-6">
        <div className="flex flex-wrap items-center gap-2 p-4">
          <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask(q)}
            placeholder={t("analyst.placeholder")} className="min-w-[280px] flex-1" />
          <label className="flex items-center gap-2 text-xs text-ink-3">
            <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> {t("analyst.useAi")}
          </label>
          <Button onClick={() => ask(q)} disabled={busy || !q.trim()} aria-busy={busy}>{busy ? "…" : t("analyst.ask")}</Button><LiveStatus busy={busy} done={log.length > 0} busyText={t("a11y.loading")} doneText={t("a11y.loaded")} />
        </div>
        <div className="flex flex-wrap gap-2 border-t border-line px-4 py-3">
          {examples.map((x) => <button key={x} onClick={() => ask(x)} className="rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:border-accent hover:text-accent">{x}</button>)}
        </div>
        <div className="border-t border-line px-4 py-2 text-[11px] text-ink-3">{t("analyst.how")}</div>
      </Card>
      {err && <div className="mx-6 mt-3 text-sm text-bad">{err}</div>}
      <div className="space-y-4 px-6 pt-4">
        {log.length === 0 && <Card><Empty title={t("analyst.emptyTitle")}>{t("analyst.emptySub")}</Empty></Card>}
        {log.map((a, i) => (
          <Card key={`${a.question}-${i}`}>
            <CardHeader title={a.question}
              subtitle={<span>{t("analyst.intent")} <b>{t(`analyst.intents.${a.intent}`)}</b> · {a.scope.markets.join(", ") || "—"}{a.mode === "ai" ? ` · ${t("analyst.phrasedBy", { m: a.model ?? "" })}` : ""}</span>}
              right={<Badge color={a.mode === "ai" ? "var(--accent)" : a.ai_status === "rejected" ? "var(--warn)" : undefined}>
                {a.mode === "ai" ? t("analyst.modeAi") : a.ai_status === "rejected" ? t("analyst.modeRejected") : t("analyst.modeComputed")}</Badge>} />
            <div className="space-y-3 p-4">
              {a.ai_answer && <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 text-sm"><MarkdownLite text={a.ai_answer} /></div>}
              {a.ai_note && <div className={`text-xs ${a.ai_status === "rejected" ? "text-warn" : "text-ink-3"}`}>{t("analyst.aiNote")}: {a.ai_note}</div>}
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-ink-3">{t("analyst.facts")}</div>
                {a.facts.length === 0 ? <div className="text-sm text-ink-3">{a.answer}</div> : (
                  <ol className="space-y-1.5">
                    {a.facts.map((f) => {
                      const page = pageFor(f.source);
                      return (
                        <li key={f.id} className="flex gap-2 text-sm">
                          <span className="mt-0.5 shrink-0 rounded bg-panel-2 px-1.5 font-mono text-[10px] text-accent">{f.id}</span>
                          <span className="flex-1">{f.text}
                            <span className="ml-2 whitespace-nowrap text-[10px] text-ink-3">
                              {page ? <Link className="hover:text-accent" href={page}>{t("analyst.verify")} →</Link> : null}
                              <code className="ml-1 rounded bg-panel-2 px-1">{f.source}</code></span></span>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>
              {a.followups.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {a.followups.map((f) => <button key={f} onClick={() => ask(f)}
                    className="rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:border-accent hover:text-accent">{f}</button>)}
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
