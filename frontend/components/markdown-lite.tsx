// Minimal, safe renderer for the analyst's answers: **bold**, "- " bullets, "1. " items, line breaks.
// No HTML is interpreted -- text is rendered as React text nodes.
import * as React from "react";

function inline(s: string, key: string) {
  return s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <b key={`${key}-${i}`} className="text-ink">{part.slice(2, -2)}</b>
      : part.startsWith("`") && part.endsWith("`") ? <code key={`${key}-${i}`} className="font-mono text-accent">{part.slice(1, -1)}</code>
        : <React.Fragment key={`${key}-${i}`}>{part}</React.Fragment>);
}

export function MarkdownLite({ text }: { text: string }) {
  return (
    <div className="space-y-1 text-sm leading-6 text-ink-2">
      {text.split("\n").map((line, i) => {
        const k = String(i);
        if (!line.trim()) return <div key={k} className="h-2" />;
        const bullet = line.match(/^(\s*)- (.*)$/);
        if (bullet) return <div key={k} className="flex gap-2 pl-2"><span className="text-accent">•</span><span>{inline(bullet[2], k)}</span></div>;
        const numbered = line.match(/^(\d+)\. (.*)$/);
        if (numbered) return <div key={k} className="mt-2 flex gap-2"><span className="font-mono text-accent">{numbered[1]}.</span><span>{inline(numbered[2], k)}</span></div>;
        const indented = line.match(/^\s{2,}(.*)$/);
        if (indented) return <div key={k} className="pl-6 text-ink-3">{inline(indented[1], k)}</div>;
        return <div key={k}>{inline(line, k)}</div>;
      })}
    </div>
  );
}
