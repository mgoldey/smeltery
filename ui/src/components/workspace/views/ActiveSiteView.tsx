import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, reasonOf } from "../../../api/http";
import type { Schemas } from "../../../api/client";
import { MolstarViewer } from "../../MolstarViewer";
import { receptorScene } from "../../scenes";
import type { StepViewProps } from "./registry";

/** Response types of the two routes this view calls; typed here until the API schema is regenerated. */
type Preview = { n_charges: number; net_charge: number; nearest_ang: number | null; scene: Record<string, unknown> | null; source: string };
type Opened = Schemas["StructureInfo"];
type Choice = "structure" | "pocket_file" | "hand_placed";

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
  return (await res.json()) as T;
}

/** Which of the three ways the draft is written in. */
export function chosen(toml: string): Choice {
  if (/^\[structure\]/m.test(toml)) return "structure";
  const pocket = /^\[pocket\]\s*$([\s\S]*?)(?=^\[|(?![\s\S]))/m.exec(toml);
  if (pocket && /^file\s*=/m.test(pocket[1] ?? "")) return "pocket_file";
  return "hand_placed";
}

const vec = (s: string): number[] | undefined => {
  const v = s.split(/[\s,]+/).filter(Boolean).map(Number);
  return v.length === 3 && v.every(Number.isFinite) ? v : undefined;
};

const WATER = new Set(["HOH", "WAT", "H2O", "DOD"]);

function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/**
 * Step 1: the active site, three ways. Each way writes its own sections into the draft through
 * `POST /api/pipelines/active_site`; the preview builds the point-charge field with no docking and no SCF.
 */
export function ActiveSiteView({ toml, onToml }: StepViewProps & { onToml?: (text: string) => void }) {
  const current = chosen(toml);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState("");
  const [info, setInfo] = useState<Opened | null>(null);
  const [ligand, setLigand] = useState("");
  const [cutoff, setCutoff] = useState("12");
  const [file, setFile] = useState("");
  const [receptor, setReceptor] = useState("");
  const [boxCenter, setBoxCenter] = useState("");
  const [boxSize, setBoxSize] = useState("24 24 24");

  const write = async (body: Record<string, unknown>) => {
    setBusy(true);
    try {
      const r = await post<{ toml: string }>("/api/pipelines/active_site", { toml, ...body });
      setError(null);
      onToml?.(r.toml);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const open = async () => {
    setBusy(true);
    try {
      const r = await post<Opened>("/api/structure", { source, allow_network: false });
      setInfo(r);
      setLigand(r.dropped.find((d) => !WATER.has(d)) ?? "");
      setError(null);
    } catch (e) {
      setInfo(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const hasField = current !== "hand_placed";
  const preview = useQuery({
    queryKey: ["pocket_preview", toml],
    queryFn: () => post<Preview>("/api/pipelines/pocket_preview", { toml }),
    enabled: hasField,
    retry: false,
    staleTime: Infinity,
  });
  const radio = (c: Choice, label: string) => (
    <label className="choice" data-choice={c}>
      <input type="radio" name="active-site-way" checked={current === c} onChange={() => undefined} onClick={() => current !== c && c === "hand_placed" && void write({ choice: c })} disabled={busy} />
      {label}
    </label>
  );
  const center = vec(boxCenter);
  const structureReady = info !== null && ligand !== "" && Number.isFinite(Number(cutoff));

  return (
    <div data-view="active_site" data-choice-now={current}>
      <fieldset className="choices">
        <legend>The active site comes from</legend>
        {radio("structure", "A protein structure")}
        {radio("pocket_file", "A prepared pocket file and receptor")}
        {radio("hand_placed", "Hand-placed charges")}
      </fieldset>

      <section aria-label="Protein structure" data-way="structure">
        <h4>Protein structure</h4>
        <Field label="Path, PDB id or UniProt accession" value={source} onChange={setSource} />
        <button type="button" disabled={busy || !source} onClick={() => void open()}>
          Open structure
        </button>
        {info ? (
          <div>
            <p className="lede">
              <span className="mono" data-testid="active-site-sha">{info.sha256.slice(0, 12)}</span> · {info.origin} · {info.n_atoms} atoms
            </p>
            <MolstarViewer state={receptorScene(info.file_url)} label="active site structure" height={320} />
            <label className="field">
              <span>Reference ligand</span>
              <select value={ligand} onChange={(e) => setLigand(e.target.value)}>
                <option value="">none</option>
                {info.dropped.filter((d) => !WATER.has(d)).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <Field label="Pocket cutoff (Å)" value={cutoff} onChange={setCutoff} />
            <button
              type="button"
              disabled={busy || !structureReady}
              onClick={() =>
                void write({ choice: "structure", spec: source, reference_ligand: ligand, pocket_cutoff: Number(cutoff) })
              }
            >
              Use as active site
            </button>
          </div>
        ) : null}
      </section>

      <section aria-label="Pocket file" data-way="pocket_file">
        <h4>Pocket file and receptor</h4>
        <Field label="Pocket file (.pqr or .pdb)" value={file} onChange={setFile} />
        <Field label="Receptor (.pdbqt)" value={receptor} onChange={setReceptor} />
        <Field label="Box centre (Å)" value={boxCenter} onChange={setBoxCenter} placeholder="x y z" />
        <Field label="Box size (Å)" value={boxSize} onChange={setBoxSize} />
        <button
          type="button"
          disabled={busy || !file || !receptor || !center || !vec(boxSize)}
          onClick={() => void write({ choice: "pocket_file", file, receptor, box_center: center, box_size: vec(boxSize) })}
        >
          Use pocket file
        </button>
      </section>

      <section aria-label="Hand-placed way" data-way="hand_placed">
        <h4>Hand-placed charges</h4>
        <p className="lede">Click two atoms of the parent below to place a charge; no receptor and no docking are used.</p>
      </section>

      {error ? (
        <div className="banner" role="alert" data-testid="active-site-error">
          {error}
        </div>
      ) : null}

      <section aria-label="Field preview" data-testid="field-preview">
        <h4>Field preview</h4>
        {!hasField ? (
          <p className="lede">Charges placed by hand are positioned on the parent's pose when the pipeline runs; nothing to preview before then.</p>
        ) : preview.isPending ? (
          <p className="lede">Building the field (no docking, no SCF)…</p>
        ) : preview.isError ? (
          <div className="banner" role="alert" data-testid="preview-error">
            {preview.error.message}
          </div>
        ) : (
          <div>
            <p data-testid="preview-numbers">
              <span data-testid="n-charges">{preview.data.n_charges}</span> charges · net{" "}
              <span data-testid="net-charge">{preview.data.net_charge.toFixed(2)}</span> e ·{" "}
              {preview.data.nearest_ang === null ? "nearest charge: no box centre" : `nearest charge ${preview.data.nearest_ang.toFixed(2)} Å from the centre`}
            </p>
            <p className="lede">{preview.data.source}. Docking and the SCFs need a run.</p>
            {preview.data.scene ? <MolstarViewer state={preview.data.scene} label="pocket preview" height={320} /> : null}
          </div>
        )}
      </section>
    </div>
  );
}
