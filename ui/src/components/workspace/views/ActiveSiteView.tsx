import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ApiError, reasonOf } from "../../../api/http";
import type { Schemas } from "../../../api/client";
import { MolstarViewer } from "../../MolstarViewer";
import { receptorScene } from "../../scenes";
import type { StepViewProps } from "./registry";
import { FilePick } from "../../FilePick";

/** Response types of the two routes this view calls; typed here until the API schema is regenerated. */
type Preview = {
  n_charges: number;
  net_charge: number;
  nearest_ang: number | null;
  scene: Record<string, unknown> | null;
  source: string;
};
type Opened = Schemas["StructureInfo"];
type Choice = "structure" | "pocket_file" | "hand_placed";

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
  const v = s
    .split(/[\s,]+/)
    .filter(Boolean)
    .map(Number);
  return v.length === 3 && v.every(Number.isFinite) ? v : undefined;
};

const WATER = new Set(["HOH", "WAT", "H2O", "DOD"]);

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/**
 * Step 1: the active site, three ways. Each way writes its own sections into the draft through
 * `POST /api/pipelines/active_site`; the preview builds the point-charge field with no docking and no SCF.
 */
/** The value of `key` in `[table]` of the draft, so the view starts from what the config already says. */
export function tomlValue(toml: string, table: string, key: string): string {
  const section = new RegExp(`^\\[${table}\\]\\s*$([\\s\\S]*?)(?=^\\[|(?![\\s\\S]))`, "m").exec(toml);
  const m = new RegExp(`^${key}\\s*=\\s*(.+)$`, "m").exec(section?.[1] ?? "");
  const raw = m?.[1]?.trim() ?? "";
  // A quoted value may be followed by a comment, and a comment character may sit inside the quotes.
  const quoted = /^"([^"]*)"/.exec(raw) ?? /^'([^']*)'/.exec(raw);
  return quoted ? (quoted[1] ?? "") : raw.replace(/\s*#.*$/, "").trim();
}

export function ActiveSiteView({ pipeline, toml, onToml }: StepViewProps) {
  const current = chosen(toml);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Seeded from the draft, so the View and the Method column show one structure, not two unrelated boxes.
  const [source, setSource] = useState(() => tomlValue(toml, "structure", "spec"));
  const [info, setInfo] = useState<Opened | null>(null);
  const [ligand, setLigand] = useState(() => tomlValue(toml, "structure", "reference_ligand"));
  const [cutoff, setCutoff] = useState(() => tomlValue(toml, "structure", "pocket_cutoff") || "12");
  const [file, setFile] = useState(() => tomlValue(toml, "pocket", "file"));
  const [receptor, setReceptor] = useState(() => tomlValue(toml, "docking", "receptor"));
  const [boxCenter, setBoxCenter] = useState("");
  const [boxSize, setBoxSize] = useState("24 24 24");

  // The draft is the state, and this view is not remounted when it changes (the Method column's own picker writes
  // the same keys). One effect follows every field whose config value changed, so the two never show different
  // structures; a field the config does not set is left alone, so typing is never wiped.
  const seeded = useRef({ source, ligand, cutoff, file, receptor });
  useEffect(() => {
    const now = {
      source: tomlValue(toml, "structure", "spec"),
      ligand: tomlValue(toml, "structure", "reference_ligand"),
      cutoff: tomlValue(toml, "structure", "pocket_cutoff"),
      file: tomlValue(toml, "pocket", "file"),
      receptor: tomlValue(toml, "docking", "receptor"),
    };
    const was = seeded.current;
    const set = { source: setSource, ligand: setLigand, cutoff: setCutoff, file: setFile, receptor: setReceptor };
    for (const k of Object.keys(now) as (keyof typeof now)[]) {
      if (now[k] !== was[k] && now[k] !== "") set[k](now[k]);
    }
    seeded.current = now;
    // Only the draft drives this; the fields themselves are free to be typed into between drafts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toml]);

  const sites = (toml.match(/^\[\[pocket\.site\]\]/gm) ?? []).length;
  const [confirm, setConfirm] = useState<Record<string, unknown> | null>(null);
  const [way, setWay] = useState<Choice>(current);
  useEffect(() => setWay(current), [current]);

  /** A switch that would drop hand-placed charges waits for a yes, saying how many go. */
  const ask = (body: Record<string, unknown>) => {
    if (sites > 0 && body.choice !== "hand_placed") setConfirm(body);
    else void write(body);
  };

  const write = async (body: Record<string, unknown>) => {
    setConfirm(null);
    setBusy(true);
    try {
      const r = await post<{ toml: string }>("/api/pipelines/active_site", {
        toml,
        ...body,
      });
      setError(null);
      onToml(r.toml);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const open = async (which?: string) => {
    const from = which ?? source;
    setBusy(true);
    try {
      const r = await post<Opened>("/api/structure", {
        source: from,
        allow_network: false,
        pipeline, // a relative spec is read from the pipeline folder, as the run reads it
      });
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
    queryKey: ["pocket_preview", pipeline, toml],
    queryFn: () =>
      post<Preview>("/api/pipelines/pocket_preview", { toml, pipeline }),
    enabled: hasField,
    retry: false,
    staleTime: Infinity,
  });
  const radio = (c: Choice, label: string) => (
    <label className="choice" data-choice={c}>
      <input
        type="radio"
        name="active-site-way"
        checked={way === c}
        onChange={() => setWay(c)}
        disabled={busy}
      />
      {label}
    </label>
  );
  const center = vec(boxCenter);
  const structureReady =
    info !== null && ligand !== "" && Number.isFinite(Number(cutoff));

  return (
    <div data-view="active_site" data-choice-now={current}>
      <fieldset className="choices">
        <legend>The active site comes from</legend>
        {radio("structure", "A protein structure")}
        {radio("pocket_file", "A prepared pocket file and receptor")}
        {radio("hand_placed", "Hand-placed charges")}
      </fieldset>

      {way === "structure" ? (
        <section aria-label="Protein structure" data-way="structure">
          <h4>Protein structure</h4>
          <FilePick
            pipeline={pipeline}
            accept=".pdb,.ent,.cif"
            label="a PDB file"
            disabled={busy}
            onPicked={(path) => {
              setSource(path);
              void open(path);
            }}
          />
          <Field
            label="or a path, PDB id or UniProt accession"
            value={source}
            onChange={setSource}
          />
          <button
            type="button"
            disabled={busy || !source}
            onClick={() => void open()}
          >
            Open structure
          </button>
          {info ? (
            <div>
              <p className="lede">
                <span className="mono" data-testid="active-site-sha">
                  {info.sha256.slice(0, 12)}
                </span>{" "}
                · {info.origin} · {info.n_atoms} atoms
              </p>
              <MolstarViewer
                state={receptorScene(info.file_url)}
                label="active site structure"
                height={320}
              />
              <label className="field">
                <span>Reference ligand</span>
                <select
                  value={ligand}
                  onChange={(e) => setLigand(e.target.value)}
                >
                  <option value="">none</option>
                  {info.dropped
                    .filter((d) => !WATER.has(d))
                    .map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                </select>
              </label>
              <Field
                label="Pocket cutoff (Å)"
                value={cutoff}
                onChange={setCutoff}
              />
              <button
                type="button"
                disabled={busy || !structureReady}
                onClick={() =>
                  ask({
                    choice: "structure",
                    spec: source,
                    reference_ligand: ligand,
                    pocket_cutoff: Number(cutoff),
                  })
                }
              >
                Use as active site
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {way === "pocket_file" ? (
        <section aria-label="Pocket file" data-way="pocket_file">
          <h4>Pocket file and receptor</h4>
          <FilePick pipeline={pipeline} accept=".pqr,.pdb" label="a pocket file" disabled={busy} onPicked={setFile} />
          <Field
            label="Pocket file (.pqr or .pdb)"
            value={file}
            onChange={setFile}
          />
          <FilePick pipeline={pipeline} accept=".pdbqt" label="a receptor" disabled={busy} onPicked={setReceptor} />
          <Field
            label="Receptor (.pdbqt)"
            value={receptor}
            onChange={setReceptor}
          />
          <Field
            label="Box centre (Å)"
            value={boxCenter}
            onChange={setBoxCenter}
            placeholder="x y z"
          />
          <Field label="Box size (Å)" value={boxSize} onChange={setBoxSize} />
          <button
            type="button"
            disabled={busy || !file || !receptor || !center || !vec(boxSize)}
            onClick={() =>
              ask({
                choice: "pocket_file",
                file,
                receptor,
                box_center: center,
                box_size: vec(boxSize),
              })
            }
          >
            Use pocket file
          </button>
        </section>
      ) : null}

      {way === "hand_placed" ? (
        <section aria-label="Hand-placed way" data-way="hand_placed">
          <h4>Hand-placed charges</h4>
          <p className="lede">
            Click two atoms of the parent below to place a charge; no receptor
            and no docking are used.
          </p>
          <button
            type="button"
            disabled={busy || current === "hand_placed"}
            onClick={() => void write({ choice: "hand_placed" })}
          >
            Use hand-placed charges
          </button>
        </section>
      ) : null}

      {confirm ? (
        <div
          className="banner"
          role="alertdialog"
          aria-label="Confirm switch"
          data-testid="confirm-switch"
        >
          <p>
            Switching drops the {sites} hand-placed charge
            {sites === 1 ? "" : "s"} in <code>[[pocket.site]]</code>. Cancel
            keeps them.
          </p>
          <button type="button" onClick={() => void write(confirm)}>
            Drop {sites} and switch
          </button>{" "}
          <button type="button" onClick={() => setConfirm(null)}>
            Cancel
          </button>
        </div>
      ) : null}

      {error ? (
        <div className="banner" role="alert" data-testid="active-site-error">
          {error}
        </div>
      ) : null}

      <section aria-label="Field preview" data-testid="field-preview">
        <h4>Field preview</h4>
        {!hasField ? (
          <p className="lede">
            Charges placed by hand are positioned on the parent's pose when the
            pipeline runs; nothing to preview before then.
          </p>
        ) : preview.isPending ? (
          <p className="lede">Building the field (no docking, no SCF)…</p>
        ) : preview.isError ? (
          <div className="banner" role="alert" data-testid="preview-error">
            {preview.error.message}
          </div>
        ) : (
          <div>
            <p data-testid="preview-numbers">
              <span data-testid="n-charges">{preview.data.n_charges}</span>{" "}
              charges · net{" "}
              <span data-testid="net-charge">
                {preview.data.net_charge.toFixed(2)}
              </span>{" "}
              e ·{" "}
              {preview.data.nearest_ang === null
                ? "nearest charge: no box centre"
                : `nearest charge ${preview.data.nearest_ang.toFixed(2)} Å from the centre`}
            </p>
            <p className="lede">
              {preview.data.source}. Docking and the SCFs need a run.
            </p>
            {preview.data.scene ? (
              <MolstarViewer
                state={preview.data.scene}
                label="pocket preview"
                height={320}
              />
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}
