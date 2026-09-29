"use client";
import { useEffect } from "react";
import { MarkdownLite } from "@/components/markdown-lite";
import { Card, Empty } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Section = { title: string; level: number; ids: string[]; body: string };

/** Renders one section body: pipe tables and fenced code blocks, the rest as MarkdownLite text. */
function Body({ text }: { text: string }) {
  const blocks: { kind: "text" | "table" | "code"; lines: string[] }[] = [];
  let code = false;
  for (const line of text.split("\n")) {
    if (line.trim().startsWith("```")) { code = !code; if (code) blocks.push({ kind: "code", lines: [] }); continue; }
    const kind = code ? "code" : line.trim().startsWith("|") ? "table" : "text";
    const last = blocks[blocks.length - 1];
    if (last && last.kind === kind) last.lines.push(line); else blocks.push({ kind, lines: [line] });
  }
  return (
    <div className="space-y-2">
      {blocks.map((b, i) => {
        if (b.kind === "code") return <pre key={i} className="overflow-x-auto rounded-lg bg-panel-2 p-3 font-mono text-xs text-ink">{b.lines.join("\n")}</pre>;
        if (b.kind === "table") {
          const rows = b.lines.filter((l) => !/^\s*\|[\s:|-]+\|\s*$/.test(l)).map((l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
          return (
            <div key={i} className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-left text-[10px] uppercase text-ink-3"><tr>{rows[0].map((c, j) => <th scope="col" key={j} className="py-1 pr-3">{c}</th>)}</tr></thead>
                <tbody className="divide-y divide-line">{rows.slice(1).map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j} className="py-1 pr-3 align-top text-ink-2"><MarkdownLite text={c} /></td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }
        return <MarkdownLite key={i} text={b.lines.join("\n")} />;
      })}
    </div>
  );
}

export default function Methodology() {
  const { t } = useI18n();
  const { data, error } = useApi<{ markdown: string; sections: Section[] }>("/methodology");
  useEffect(() => {
    if (data && window.location.hash) document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView();
  }, [data]);
  return (
    <div className="pb-16">
      <PageHeader title={t("nav.methodology")} subtitle="docs/METHODOLOGY.md — every formula, assumption and validation result behind the numbers." />
      <ErrorNote error={error} />
      {!data ? <Card className="mx-6"><Empty title={t("common.loading")} /></Card> : (
        <div className="grid grid-cols-1 gap-4 px-6 xl:grid-cols-[240px_1fr]">
          <Card className="h-fit p-3 text-xs xl:sticky xl:top-4">
            {data.sections.filter((s) => s.level <= 2).map((s, i) => (
              <a key={i} href={`#sec-${i}`} className="block py-0.5 text-ink-2 hover:text-accent">{s.title}</a>
            ))}
          </Card>
          <Card className="p-5">
            {data.sections.map((s, i) => (
              <section key={i} id={`sec-${i}`} className="mb-6 scroll-mt-4">
                {s.ids.map((id) => <span key={id} id={id} className="block scroll-mt-4" />)}
                {s.level <= 2 ? <h2 className="mb-2 text-lg font-semibold">{s.title}</h2> : <h3 className="mb-2 text-sm font-semibold text-accent">{s.title}</h3>}
                <Body text={s.body} />
              </section>
            ))}
          </Card>
        </div>
      )}
    </div>
  );
}
