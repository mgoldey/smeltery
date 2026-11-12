import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { reasonOf } from "../api/http";

type Imported = { pipeline: { name: string } | null; files: { path: string; ok: boolean; message?: string | null }[] };

/** Import a pipeline from a bundle zip (what "Download saved bundle" writes). Any bad entry refuses the whole bundle
 * and is listed with its reason; nothing is created then. */
export function ImportBundle() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const pick = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setProblems([]);
    try {
      const res = await fetch("/api/pipelines/import", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file,
      });
      if (res.status === 422) {
        const body = (await res.json()) as Imported;
        setProblems((body.files ?? []).filter((f) => !f.ok).map((f) => `${f.path}: ${f.message ?? "refused"}`));
      } else if (!res.ok) {
        setProblems([await reasonOf(res)]);
      } else {
        const body = (await res.json()) as Imported;
        void qc.invalidateQueries({ queryKey: ["pipelines"] });
        if (body.pipeline) navigate(`/pipelines/${body.pipeline.name}`);
      }
    } catch (e) {
      setProblems([e instanceof Error ? e.message : String(e)]);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <label htmlFor="import-bundle">Import bundle (.zip)</label>
      <input
        id="import-bundle"
        type="file"
        accept=".zip,application/zip"
        disabled={busy}
        onChange={(e) => {
          void pick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {problems.length ? (
        <ul className="field-error" role="alert">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
