import { useState } from "react";
import type { Schemas } from "../api/client";

export type PerPoseRow = Schemas["PerPoseRow"];
type Key = keyof PerPoseRow;

const COLUMNS: { key: Key; label: (unit: string) => string; num: boolean }[] = [
  { key: "candidate", label: () => "Candidate", num: false },
  { key: "pose", label: () => "Pose", num: true },
  { key: "value", label: (u) => `ΔE_int (${u})`, num: true },
  { key: "parent_value", label: (u) => `Parent ΔE_int (${u})`, num: true },
  { key: "paired", label: (u) => `Paired ΔΔE (${u})`, num: true },
];

/** RFC 4180 field: quoted (quotes doubled) when it holds a comma, quote or line break. */
const field = (v: string | number) => {
  const s = String(v);
  return /[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
};

/** The rows as CSV text, one line per row in the order given, under the same column labels as the table. */
export function perPoseCsv(rows: PerPoseRow[], unit: string): string {
  const head = COLUMNS.map((c) => field(c.label(unit)));
  const body = rows.map((r) => COLUMNS.map((c) => field(r[c.key])).join(","));
  return [head.join(","), ...body].join("\r\n") + "\r\n";
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Every (ligand, pose) value the paired ΔΔE is built from, sortable by any column, exportable as CSV. */
export function PerPoseTable({ rows, unit, fileName }: { rows: PerPoseRow[]; unit: string; fileName: string }) {
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 } | null>(null);
  const shown = sort
    ? [...rows].sort((a, b) => {
        const x = a[sort.key];
        const y = b[sort.key];
        return (typeof x === "string" ? x.localeCompare(y as string) : (x as number) - (y as number)) * sort.dir;
      })
    : rows;
  return (
    <>
      <p>
        <button type="button" onClick={() => download(fileName, perPoseCsv(shown, unit))}>
          Export CSV
        </button>
      </p>
      <table className="grid" aria-label="Per-pose values">
        <thead>
          <tr>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                className={c.num ? "num" : undefined}
                aria-sort={sort?.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
              >
                <button
                  type="button"
                  className="link"
                  onClick={() => setSort({ key: c.key, dir: sort?.key === c.key ? (-sort.dir as 1 | -1) : 1 })}
                >
                  {c.label(unit)}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <tr key={`${r.candidate}/${r.pose}`}>
              <td>{r.candidate}</td>
              <td className="num">{r.pose}</td>
              <td className="num">{r.value.toFixed(3)}</td>
              <td className="num">{r.parent_value.toFixed(3)}</td>
              <td className="num">{r.paired.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
