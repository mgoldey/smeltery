import type { ReactNode } from "react";
import { type Block, type Inline, parseMarkdown } from "./markdown";

function inline(nodes: Inline[]): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text":
        return n.text;
      case "code":
        return <code key={i}>{n.text}</code>;
      case "strong":
        return <strong key={i}>{inline(n.children)}</strong>;
      case "em":
        return <em key={i}>{inline(n.children)}</em>;
      case "link":
        return (
          <a key={i} href={n.href} rel="noopener noreferrer" target={/^https?:/i.test(n.href) ? "_blank" : undefined}>
            {inline(n.children)}
          </a>
        );
    }
  });
}

function block(b: Block, i: number): ReactNode {
  switch (b.t) {
    case "heading": {
      const H = b.level === 3 ? "h5" : "h4"; // entry titles are h3
      return <H key={i}>{inline(b.children)}</H>;
    }
    case "code":
      return (
        <pre key={i}>
          <code>{b.text}</code>
        </pre>
      );
    case "list": {
      const L = b.ordered ? "ol" : "ul";
      return (
        <L key={i}>
          {b.items.map((it, j) => (
            <li key={j}>{inline(it)}</li>
          ))}
        </L>
      );
    }
    case "p":
      return <p key={i}>{inline(b.children)}</p>;
  }
}

/** Entry text as markdown. Built from React elements only, never from an HTML string. */
export function Markdown({ source }: { source: string }) {
  return <div className="entry-body md">{parseMarkdown(source).map(block)}</div>;
}
