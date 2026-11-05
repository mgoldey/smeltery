import type { Schemas } from "../../api/client";

type Line = Schemas["DiffLine"];

/** Which lines to show: every changed line and `context` unchanged lines around each run of changes. */
export function visible(diff: Line[], context = 1): (Line | null)[] {
  const near = new Set<number>();
  diff.forEach((d, i) => {
    if (d.sign === " ") return;
    for (let j = i - context; j <= i + context; j++) near.add(j);
  });
  const out: (Line | null)[] = [];
  diff.forEach((d, i) => {
    if (near.has(i)) out.push(d);
    else if (out.length && out[out.length - 1] !== null) out.push(null); // a gap
  });
  while (out.length && out[out.length - 1] === null) out.pop();
  return out;
}

/** The lines the draft writes into the kept config: the real line diff of the file, from the API. */
export function ConfigDiff({ diff }: { diff: Line[] }) {
  const lines = visible(diff);
  return (
    <section aria-label="Config lines this edit writes">
      <h3 className="config-h">Config lines this edit writes</h3>
      {lines.length === 0 ? (
        <p className="empty-state">No edits: the draft is the kept config.</p>
      ) : (
        <pre className="config-diff" data-testid="config-diff">
          {lines.map((d, i) =>
            d === null ? (
              <div key={i} data-sign=" ">
                …
              </div>
            ) : (
              <div key={i} data-sign={d.sign}>
                {d.sign === " " ? "  " : `${d.sign} `}
                {d.line}
              </div>
            ),
          )}
        </pre>
      )}
    </section>
  );
}
