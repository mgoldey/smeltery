import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { rdkit } from "../../../chem/rdkit";
import type { StepViewProps } from "./registry";

/** `POST /api/pipelines/ligands` rows. Typed here until `npm run gen:api` picks up the new models. */
export type LigandCheck = {
  name: string;
  smiles: string;
  is_parent: boolean;
  smiles_ok: boolean;
  formal_charge: number | null;
  electrons: number | null;
  electrons_even: boolean | null;
  sto3g_ok: boolean | null;
  sto3g_missing: string[];
  core_atoms: number[];
  problems: string[];
};
type LigandsOut = { parent: string; candidates: LigandCheck[]; problems: string[] };
type Entry = { name: string; smiles: string };

/** The edit route's only caller wiring: the workspace hands the view a way to replace the draft text. */
export type LigandsViewProps = StepViewProps;

async function post<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const j = (await r.json().catch(() => ({}))) as { detail?: unknown };
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `${r.status} ${r.statusText}`);
  return j as T;
}

/** A token colour as RDKit wants it: [r, g, b] in 0..1, read from the page's own CSS variable (so night mode follows). */
function token(name: string): [number, number, number] {
  const hex = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return [0.2, 0.6, 0.2];
  const n = parseInt(m[1]!, 16);
  return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/**
 * The candidate drawn by RDKit.js with its core highlighted. `core` indexes the SMILES with explicit hydrogens;
 * AddHs appends hydrogens after the heavy atoms, so the heavy atoms keep their index and the hydrogens (not drawn)
 * are simply not highlightable.
 */
function CoreStructure({ smiles, core }: { smiles: string; core: number[] }) {
  const [svg, setSvg] = useState<string | null>(null);
  const key = core.join(",");
  useEffect(() => {
    let live = true;
    void rdkit().then((RDKit) => {
      const mol = RDKit.get_mol(smiles);
      if (!mol) return;
      const n = mol.get_num_atoms();
      const atoms = core.filter((i) => i < n);
      const inCore = new Set(atoms);
      const bonds: number[] = [];
      const json = JSON.parse(mol.get_json()) as { molecules: { bonds?: { atoms: [number, number] }[] }[] };
      (json.molecules[0]?.bonds ?? []).forEach((b, i) => {
        if (inCore.has(b.atoms[0]) && inCore.has(b.atoms[1])) bonds.push(i);
      });
      const colour = token("--accent");
      const out = mol.get_svg_with_highlights(
        JSON.stringify({
          width: 170,
          height: 120,
          backgroundColour: [1, 1, 1, 0],
          bondLineWidth: 1.4,
          atoms,
          bonds,
          highlightColour: colour,
          highlightRadius: 0.3,
        }),
      );
      mol.delete();
      if (live) setSvg(out);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [smiles, key]);
  return (
    <span
      className="structure2d"
      role="img"
      aria-label={`structure ${smiles}`}
      data-smiles={smiles}
      data-core={key}
      style={{ width: 170, height: 120 }}
      // RDKit's own SVG, generated locally from the SMILES.
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    />
  );
}

const Mark = ({ ok, children }: { ok: boolean | null; children: React.ReactNode }) => (
  <span style={{ color: ok === null ? "var(--ink-muted)" : ok ? "var(--ok)" : "var(--bad)" }}>{children}</span>
);

function Row({
  c,
  heavy,
  busy,
  canEdit,
  onCommit,
}: {
  c: LigandCheck;
  heavy: number;
  busy: boolean;
  canEdit: boolean;
  onCommit: (kind: "rename" | "smiles" | "parent" | "remove", c: LigandCheck, value?: string) => void;
}) {
  const [name, setName] = useState(c.name);
  const [smiles, setSmiles] = useState(c.smiles);
  useEffect(() => setName(c.name), [c.name]);
  useEffect(() => setSmiles(c.smiles), [c.smiles]);
  const off = busy || !canEdit;
  const coreHeavy = c.core_atoms.filter((i) => i < heavy).length;
  return (
    <tr data-candidate={c.name} data-parent={c.is_parent}>
      <td>
        <CoreStructure smiles={c.smiles} core={c.core_atoms} />
      </td>
      <td>
        <input
          aria-label={`Name of ${c.name}`}
          value={name}
          disabled={off}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name !== c.name && onCommit("rename", c, name)}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        />
        <div>
          <label>
            <input type="radio" name="parent" checked={c.is_parent} disabled={off} onChange={() => onCommit("parent", c)} /> parent
          </label>
        </div>
        <button type="button" disabled={off} onClick={() => onCommit("remove", c)} aria-label={`Remove ${c.name}`}>
          Remove
        </button>
      </td>
      <td>
        <input
          aria-label={`SMILES of ${c.name}`}
          value={smiles}
          disabled={off}
          spellCheck={false}
          onChange={(e) => setSmiles(e.target.value)}
          onBlur={() => smiles !== c.smiles && onCommit("smiles", c, smiles)}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        />
      </td>
      <td data-check="smiles_ok">
        <Mark ok={c.smiles_ok}>{c.smiles_ok ? "reads" : "cannot read"}</Mark>
      </td>
      <td data-check="formal_charge" className="num">
        {c.formal_charge === null ? "-" : c.formal_charge > 0 ? `+${c.formal_charge}` : c.formal_charge}
      </td>
      <td data-check="electrons_even">
        <Mark ok={c.electrons_even}>{c.electrons_even === null ? "-" : c.electrons_even ? `even (${c.electrons})` : `odd (${c.electrons})`}</Mark>
      </td>
      <td data-check="sto3g_ok">
        <Mark ok={c.sto3g_ok}>{c.sto3g_ok === null ? "-" : c.sto3g_ok ? "covered" : `no STO-3G for ${c.sto3g_missing.join(", ")}`}</Mark>
      </td>
      <td data-check="core_atoms" className="num">
        {c.is_parent ? "the reference" : c.core_atoms.length ? `${coreHeavy} heavy of ${heavy}` : "none"}
      </td>
    </tr>
  );
}

/**
 * The ligands step: one row per candidate with what is known before any run (it parses, its charge, whether the
 * closed-shell tier can take its electron count, whether STO-3G covers its elements, the core it shares with the
 * parent, highlighted), and the list edited in place. The text is the only state: an edit goes to the API, which
 * writes `[candidates]` and `parent` into it, and `onToml` replaces the draft. Nothing here runs an SCF or docking.
 */
export function LigandsView({ toml: live, onToml }: LigandsViewProps) {
  // The check is debounced: typing in the TOML pane must not run a core search per keystroke. The server caches by
  // (parent SMILES, candidate SMILES), so an unchanged pair costs nothing even when the text around it changes.
  const [toml, setToml] = useState(live);
  useEffect(() => {
    const t = setTimeout(() => setToml(live), 300);
    return () => clearTimeout(t);
  }, [live]);
  const q = useQuery({
    queryKey: ["ligands", toml],
    queryFn: () => post<LigandsOut>("/api/pipelines/ligands", { toml }),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState({ name: "", smiles: "" });
  const data = q.data;
  const canEdit = onToml !== undefined;

  async function write(candidates: Entry[], parent: string): Promise<boolean> {
    if (!onToml) return false;
    const dup = candidates.find((c, i) => candidates.findIndex((d) => d.name === c.name) !== i);
    if (dup) {
      setError(`there is already a candidate named ${JSON.stringify(dup.name)}; names must be unique`);
      return false;
    }
    if (candidates.some((c) => !c.name.trim())) {
      setError("a candidate needs a name");
      return false;
    }
    setBusy(true);
    try {
      const r = await post<{ toml: string }>("/api/pipelines/ligands/edit", { toml: live, candidates, parent });
      setError(null);
      onToml(r.toml);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (q.isError) return <p className="field-error" role="alert" data-view="ligands">{(q.error as Error).message}</p>;
  if (!data) return <p className="lede" data-view="ligands">Checking the candidates...</p>;

  const onCommit = (kind: "rename" | "smiles" | "parent" | "remove", c: LigandCheck, value?: string) => {
    const next: Entry[] = [];
    let parent = data.parent;
    for (const x of data.candidates) {
      if (x.name !== c.name) next.push({ name: x.name, smiles: x.smiles });
      else if (kind === "remove") continue;
      else if (kind === "rename") next.push({ name: (value ?? "").trim(), smiles: x.smiles });
      else next.push({ name: x.name, smiles: kind === "smiles" ? (value ?? "") : x.smiles });
    }
    if (kind === "rename" && c.is_parent) parent = (value ?? "").trim();
    if (kind === "parent") parent = c.name;
    void write(next, parent);
  };

  const heavy = (s: string) => (s.match(/\[[^\]]+\]|Cl|Br|[BCNOPSFI]|[bcnops]/g) ?? []).length;
  const add = () => {
    const next: Entry[] = data.candidates.map((x) => ({ name: x.name, smiles: x.smiles }));
    next.push({ name: fresh.name.trim(), smiles: fresh.smiles.trim() });
    void write(next, data.parent).then((ok) => ok && setFresh({ name: "", smiles: "" }));
  };

  return (
    <div data-view="ligands">
      <p className="lede">
        Checked with RDKit only: no docking and no SCF has run. The highlighted atoms are the core each analogue shares with the parent,
        the part whose coordinates are copied exactly.
        {canEdit ? "" : " Editing is unavailable here: the workspace has not given this view a way to change the text."}
      </p>
      {error ? (
        <p className="field-error" role="alert" data-testid="ligands-error">
          {error}
        </p>
      ) : null}
      <table className="grid" aria-label="Candidates">
        <thead>
          <tr>
            <th>Structure</th>
            <th>Name</th>
            <th>SMILES</th>
            <th>Parses</th>
            <th className="num">Charge</th>
            <th>Electrons</th>
            <th>STO-3G</th>
            <th className="num">Core</th>
          </tr>
        </thead>
        <tbody>
          {data.candidates.map((c) => (
            <Row key={c.name} c={c} heavy={heavy(c.smiles)} busy={busy} canEdit={canEdit} onCommit={onCommit} />
          ))}
        </tbody>
      </table>
      {data.candidates.length === 0 ? <p className="empty-state">No candidates yet. Add the parent first.</p> : null}
      {data.candidates.some((c) => c.problems.length) || data.problems.length ? (
        <ul data-testid="ligands-problems">
          {data.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
          {data.candidates.flatMap((c) => c.problems.map((p) => <li key={`${c.name}:${p}`}>{p}</li>))}
        </ul>
      ) : null}
      <form
        className="controls"
        aria-label="Add a candidate"
        onSubmit={(e) => {
          e.preventDefault();
          add();
        }}
      >
        <input aria-label="New candidate name" placeholder="name" value={fresh.name} disabled={busy || !canEdit} onChange={(e) => setFresh({ ...fresh, name: e.target.value })} />
        <input aria-label="New candidate SMILES" placeholder="SMILES" value={fresh.smiles} disabled={busy || !canEdit} spellCheck={false} onChange={(e) => setFresh({ ...fresh, smiles: e.target.value })} />
        <button type="submit" disabled={busy || !canEdit || !fresh.name.trim() || !fresh.smiles.trim()}>
          Add candidate
        </button>
      </form>
    </div>
  );
}
