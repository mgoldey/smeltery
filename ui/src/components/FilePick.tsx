import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { ApiError, reasonOf } from "../api/http";

/** Upload one file into the pipeline folder; the server answers with the name to write into the config. */
export async function uploadToPipeline(pipeline: string, file: File): Promise<string> {
  const res = await fetch(`/api/pipelines/${encodeURIComponent(pipeline)}/files/${encodeURIComponent(file.name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
  return ((await res.json()) as { path: string }).path;
}

function fmtSize(n: number): string {
  return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} kB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Pick an input file for a pipeline: choose one from this computer (it is copied into the pipeline folder, beside
 * config.toml, where the run reads it) or reuse one already there. `onPicked` gets the name relative to the folder,
 * which is what the config holds.
 */
export function FilePick({
  pipeline,
  accept,
  label,
  disabled,
  onPicked,
}: {
  pipeline: string;
  accept: string;
  label: string;
  disabled?: boolean;
  onPicked: (path: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const selectId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const exts = accept.split(",").map((s) => s.trim().toLowerCase());
  const existing = useQuery({
    queryKey: ["pipeline-files", pipeline],
    queryFn: async () => {
      const res = await fetch(`/api/pipelines/${encodeURIComponent(pipeline)}/files`);
      if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
      return (await res.json()) as { name: string; size: number; mtime: string; sha256: string }[];
    },
  });
  const here = (existing.data ?? []).filter((f) => exts.some((e) => f.name.toLowerCase().endsWith(e)));

  const chosen = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try {
      const path = await uploadToPipeline(pipeline, f);
      setError(null);
      await qc.invalidateQueries({ queryKey: ["pipeline-files", pipeline] });
      onPicked(path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div className="file-pick">
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        aria-label={`${label}: choose a file`}
        onChange={(e) => void chosen(e.target.files?.[0])}
      />
      <button type="button" disabled={disabled || busy} onClick={() => input.current?.click()}>
        {busy ? "Uploading…" : `Choose ${label}…`}
      </button>
      {here.length ? (
        <>
          <label htmlFor={selectId} className="lede">
            or use
          </label>
          <select
            id={selectId}
            disabled={disabled || busy}
            value=""
            aria-label={`${label}: a file already in this pipeline`}
            onChange={(e) => e.target.value && onPicked(e.target.value)}
          >
            <option value="">a file in this pipeline</option>
            {here.map((f) => (
              <option key={f.name} value={f.name}>
                {f.name} · {fmtSize(f.size)} · {f.sha256.slice(0, 12)}
              </option>
            ))}
          </select>
        </>
      ) : null}
      {error ? (
        <span className="field-error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}
