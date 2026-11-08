/**
 * A small, safe markdown subset for notebook entries, as a token tree. Nothing here produces HTML: text is carried
 * verbatim as strings (so `<script>` is just text, escaped by React when rendered), and the only structure is the
 * fixed node set below. Links are kept only for http(s), relative and fragment URLs.
 */
export type Inline =
  | { t: "text"; text: string }
  | { t: "code"; text: string }
  | { t: "strong"; children: Inline[] }
  | { t: "em"; children: Inline[] }
  | { t: "link"; href: string; children: Inline[] };

export type Block =
  | { t: "heading"; level: 1 | 2 | 3; children: Inline[] }
  | { t: "code"; text: string }
  | { t: "list"; ordered: boolean; items: Inline[][] }
  | { t: "p"; children: Inline[] };

export function isSafeUrl(url: string): boolean {
  return /^https?:\/\/[^\s]+$/i.test(url) || /^(#|\.{1,2}\/|\/(?![/\\]))[^\s\\]*$/.test(url);
}

/** Index of the closing single `*` at or after `from`, stepping over `**` pairs so emphasis can nest bold. */
function closeEm(s: string, from: number): number {
  for (let j = from; j < s.length; j++) {
    if (s.startsWith("**", j)) j++;
    else if (s.charAt(j) === "*") return j;
  }
  return -1;
}

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => {
    if (buf) out.push({ t: "text", text: buf });
    buf = "";
  };
  let i = 0;
  while (i < s.length) {
    const c = s.charAt(i);
    if (c === "`") {
      const j = s.indexOf("`", i + 1);
      if (j > i + 1) {
        flush();
        out.push({ t: "code", text: s.slice(i + 1, j) });
        i = j + 1;
        continue;
      }
    } else if (s.startsWith("**", i)) {
      const j = s.indexOf("**", i + 2);
      if (j > i + 2) {
        flush();
        out.push({ t: "strong", children: parseInline(s.slice(i + 2, j)) });
        i = j + 2;
        continue;
      }
    } else if (c === "*") {
      const j = closeEm(s, i + 1);
      if (j > i + 1) {
        flush();
        out.push({ t: "em", children: parseInline(s.slice(i + 1, j)) });
        i = j + 1;
        continue;
      }
    } else if (c === "[") {
      const m = /^\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/.exec(s.slice(i));
      if (m) {
        const [, label = "", url = ""] = m;
        if (isSafeUrl(url)) {
          flush();
          out.push({ t: "link", href: url, children: parseInline(label) });
        } else buf += m[0]; // an unsafe target stays visible text, never a link
        i += m[0].length;
        continue;
      }
    }
    buf += c;
    i++;
  }
  flush();
  return out;
}

const BULLET = /^\s*[-*]\s+(.*)$/;
const NUMBER = /^\s*\d+\.\s+(.*)$/;
const HEADING = /^(#{1,3})\s+(.*)$/;

export function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const endPara = () => {
    if (para.length) blocks.push({ t: "p", children: parseInline(para.join("\n")) });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    if (line.trimStart().startsWith("```")) {
      endPara();
      const body: string[] = [];
      for (i++; i < lines.length && !(lines[i] ?? "").trimStart().startsWith("```"); i++) body.push(lines[i] ?? "");
      blocks.push({ t: "code", text: body.join("\n") }); // an unterminated fence runs to the end
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      endPara();
      blocks.push({ t: "heading", level: (h[1] ?? "#").length as 1 | 2 | 3, children: parseInline(h[2] ?? "") });
      continue;
    }
    const kind = BULLET.test(line) ? BULLET : NUMBER.test(line) ? NUMBER : null;
    if (kind) {
      endPara();
      const items: Inline[][] = [];
      for (; i < lines.length; i++) {
        const m = kind.exec(lines[i] ?? "");
        if (!m) break;
        items.push(parseInline(m[1] ?? ""));
      }
      i--;
      blocks.push({ t: "list", ordered: kind === NUMBER, items });
      continue;
    }
    if (!line.trim()) endPara();
    else para.push(line);
  }
  endPara();
  return blocks;
}
