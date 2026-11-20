import { useState, type KeyboardEvent } from "react";
import type { Schemas } from "../../api/client";
import { FilePick } from "../FilePick";
import { paramText, parseParam } from "../paramValue";

export type Param = Schemas["StepParam"];
type Value = unknown;

const same = (a: Value, b: Value) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/**
 * One knob of a step: its value (editable), the method's own one-line help, and, when it differs, the kept value with
 * a one-click reset and the workspace default from the Methods tab. Every value comes from the steps API.
 */
/** What each file-naming knob reads, for the picker's filter. Anything else accepts any file. */
const ACCEPT: Record<string, string> = {
  "structure.spec": ".pdb,.ent,.cif",
  "pocket.file": ".pdb,.pqr",
  "docking.receptor": ".pdbqt",
  "bound_pose.file": ".xyz",
};

export function ParamRow({
  p,
  disabled,
  onSet,
  pipeline,
}: {
  p: Param;
  disabled: boolean;
  onSet: (v: Value) => void;
  /** The pipeline whose folder a picked file goes into; without it a file knob is a plain text box. */
  pipeline?: string;
}) {
  const id = `param-${p.method}-${p.name}`;
  const [text, setText] = useState(paramText(p.value));
  const [bad, setBad] = useState<string | null>(null);
  const changed = p.changed_from !== null && p.changed_from !== undefined;
  const commit = () => {
    try {
      const v = parseParam(p.kind, text);
      setBad(null);
      if (!same(v, p.value) && !(v === null && same(p.value, p.default))) onSet(v);
    } catch (e) {
      setBad(e instanceof Error ? e.message : String(e));
    }
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Enter") commit();
  };
  // Reset to the kept value; when that is the method's default, unset the knob rather than write the default out.
  const reset = () => onSet(same(p.changed_from, p.default) ? null : p.changed_from);
  let input;
  if (p.kind === "bool" || p.kind === "choice") {
    const options = p.kind === "bool" ? [true, false] : p.choices;
    input = (
      <select
        id={id}
        disabled={disabled}
        value={p.value === null || p.value === undefined ? "" : String(p.value)}
        onChange={(e) => {
          const raw = e.target.value;
          onSet(raw === "" ? null : p.kind === "bool" ? raw === "true" : raw);
        }}
      >
        <option value="">default ({String(p.default ?? "none")})</option>
        {options.map((c) => (
          <option key={String(c)} value={String(c)}>
            {String(c)}
          </option>
        ))}
      </select>
    );
  } else {
    input = (
      <input
        id={id}
        className={p.kind === "path" || p.kind === "str" ? "mono" : "num"}
        value={text}
        disabled={disabled}
        placeholder={p.default === null || p.default === undefined ? "" : paramText(p.default)}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={onKey}
        aria-invalid={bad ? true : undefined}
      />
    );
  }
  const wsDiffers = p.workspace_default !== null && p.workspace_default !== undefined && !same(p.workspace_default, p.value);
  return (
    <div className={changed ? "param changed" : "param"} data-param={p.name} data-changed={changed ? "true" : undefined}>
      <label htmlFor={id} className="mono">
        {p.name}
      </label>
      {input}
      {p.names_file && pipeline ? (
        <div className="param-file">
          <FilePick
            pipeline={pipeline}
            accept={ACCEPT[`${p.method}.${p.name}`] ?? ""}
            label="file"
            disabled={disabled}
            onPicked={(path) => {
              setText(path);
              onSet(path);
            }}
          />
        </div>
      ) : null}
      <span className="help">
        {bad ? <span className="field-error">{bad}</span> : p.help}
        {changed ? (
          <span className="was" data-testid="was">
            was {paramText(p.changed_from)} ·{" "}
            <button type="button" className="link" disabled={disabled} onClick={reset} aria-label={`Reset ${p.name}`}>
              reset
            </button>
          </span>
        ) : null}
        {wsDiffers ? (
          <span className="ws-default" data-testid="workspace-default">
            workspace default {paramText(p.workspace_default)}
          </span>
        ) : null}
      </span>
    </div>
  );
}
