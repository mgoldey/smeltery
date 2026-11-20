import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, unwrap, type Schemas } from "../../../api/client";
import { Structure2D } from "../../Structure2D";
import type { StepViewProps } from "./registry";

export type LigandCheck = Schemas["LigandCheck"];
/** One edit of the list, as the edit route takes it less the draft (added when it is sent). */
type Op = Omit<Schemas["LigandsEdit"], "toml">;

/** The edit route's only caller wiring: the workspace hands the view a way to replace the draft text. */
export type LigandsViewProps = StepViewProps;

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
        <Structure2D smiles={c.smiles} width={170} height={120} highlightAtoms={c.core_atoms} />
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
export function LigandsView({ pipeline, toml: live, onToml }: LigandsViewProps) {
  // The check is debounced: typing in the TOML pane must not run a core search per keystroke. The server caches by
  // (parent SMILES, candidate SMILES), so an unchanged pair costs nothing even when the text around it changes.
  const [toml, setToml] = useState(live);
  useEffect(() => {
    const t = setTimeout(() => setToml(live), 300);
    return () => clearTimeout(t);
  }, [live]);
  const q = useQuery({
    queryKey: ["ligands", toml],
    queryFn: () => unwrap(api.POST("/api/pipelines/ligands", { body: { toml } })),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState({ name: "", smiles: "" });
  const [rejected, setRejected] = useState<Schemas["RejectedRow"][] | null>(null);
  const data = q.data;
  const canEdit = onToml !== undefined;

  /** One operation, applied by the server to the LIVE text: the list shown may be a stale check, so the view never
   * builds the whole list itself (that would drop what was added since). The server refuses duplicates and the parent. */
  async function write(op: Op): Promise<boolean> {
    if (!onToml) return false;
    setRejected(null); // a later edit makes the last import's report stale
    setBusy(true);
    try {
      const r = await unwrap(api.POST("/api/pipelines/ligands/edit", { body: { toml: live, ...op } }));
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

  /** A CSV or SDF picked in the browser: the server appends what RDKit reads to the LIVE text and reports the rest. */
  async function importFile(file: File) {
    if (!onToml) return;
    setBusy(true);
    try {
      const r = await unwrap(
        api.POST("/api/pipelines/ligands/import", { body: { toml: live, filename: file.name, content: await file.text() } }),
      );
      setError(null);
      setRejected(r.rejected);
      if (r.accepted.length) onToml(r.toml);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (q.isError) return <p className="field-error" role="alert" data-view="ligands">{(q.error as Error).message}</p>;
  if (!data) return <p className="lede" data-view="ligands">Checking the candidates...</p>;

  const onCommit = (kind: "rename" | "smiles" | "parent" | "remove", c: LigandCheck, value?: string) => {
    const op: Op =
      kind === "rename"
        ? { op: "rename", name: c.name, new_name: value ?? "" }
        : kind === "smiles"
          ? { op: "set_smiles", name: c.name, smiles: value ?? "" }
          : kind === "parent"
            ? { op: "set_parent", name: c.name }
            : { op: "remove", name: c.name };
    void write(op);
  };

  const heavy = (s: string) => (s.match(/\[[^\]]+\]|Cl|Br|[BCNOPSFI]|[bcnops]/g) ?? []).length;
  const add = () => {
    void write({ op: "add", name: fresh.name.trim(), smiles: fresh.smiles.trim() }).then(
      (ok) => ok && setFresh({ name: "", smiles: "" }),
    );
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
      <div className="controls">
        <label>
          Import CSV/SDF{" "}
          <input
            type="file"
            accept=".csv,.sdf"
            aria-label="Import CSV or SDF"
            disabled={busy || !canEdit}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void importFile(f);
            }}
          />
        </label>
        <a href={`/api/pipelines/${encodeURIComponent(pipeline)}/ligands.csv`} download>
          Download CSV
        </a>{" "}
        <a href={`/api/pipelines/${encodeURIComponent(pipeline)}/ligands.sdf`} download>
          Download SDF
        </a>
        <span className="lede"> (the saved candidates)</span>
      </div>
      {rejected && rejected.length ? (
        <table className="grid" aria-label="Rejected rows" data-testid="ligands-rejected">
          <thead>
            <tr>
              <th className="num">Row</th>
              <th>Input</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {rejected.map((r, i) => (
              <tr key={i}>
                <td className="num">{r.row}</td>
                <td>{r.input}</td>
                <td>{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
