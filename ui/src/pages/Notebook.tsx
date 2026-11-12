import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { ApiError, reasonOf } from "../api/http";
import { EmptyState } from "../components/EmptyState";
import { Markdown } from "../components/Markdown";
import { Shell } from "../components/Shell";

type FileRef = { digest: string; filename: string; media_type: string; kind: "file" };
const INLINE_IMAGES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
const fileUrl = (f: FileRef) => `/api/notebook/artifacts/${f.digest}?name=${encodeURIComponent(f.filename)}`;

async function uploadNotebookFile(file: File): Promise<FileRef> {
  const res = await fetch(`/api/notebook/artifacts?filename=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
  });
  if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
  return (await res.json()) as FileRef;
}

/**
 * A campaign's notebook. Entries are append-only: there is no way to edit one. A correction is a new entry that
 * supersedes it, and the superseded entry stays readable, marked. Attached runs are recorded by their record's hash.
 */
export function Notebook() {
  const id = useParams().id ?? "";
  const qc = useQueryClient();
  const campaign = useQuery({
    queryKey: ["campaign", id],
    queryFn: () => unwrap(api.GET("/api/campaigns/{campaign_id}", { params: { path: { campaign_id: id } } })),
  });
  const entries = useQuery({
    queryKey: ["entries", id],
    queryFn: () => unwrap(api.GET("/api/campaigns/{campaign_id}/entries", { params: { path: { campaign_id: id } } })),
  });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
  const titleRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [attach, setAttach] = useState<string[]>([]);
  const [files, setFiles] = useState<FileRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [supersedes, setSupersedes] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/campaigns/{campaign_id}/entries", {
          params: { path: { campaign_id: id } },
          body: { title, body, runs: attach, files, supersedes },
        }),
      ),
    onSuccess: () => {
      setTitle("");
      setBody("");
      setAttach([]);
      setFiles([]);
      setSupersedes(null);
      return qc.invalidateQueries({ queryKey: ["entries", id] });
    },
  });
  const titleOf = new Map((entries.data ?? []).map((e) => [e.id, e.title]));
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>{campaign.data?.name ?? "Notebook"}</h1>
          {campaign.isError ? (
            <div className="banner" role="alert">
              {campaign.error.message}
            </div>
          ) : null}
          <p className="lede">Entries are never edited. To correct one, write a new entry that supersedes it; the old one stays readable.</p>
          <ol className="entries">
            {(entries.data ?? []).map((e) => (
              <li key={e.id} className={e.superseded_by ? "entry superseded" : "entry"} data-entry={e.id} id={`entry-${e.id}`}>
                <h3>
                  {e.title}
                  {e.superseded_by ? (
                    <span className="lede">
                      {" "}
                      superseded by <a href={`#entry-${e.superseded_by}`}>{titleOf.get(e.superseded_by) ?? "a later entry"}</a>
                    </span>
                  ) : null}
                </h3>
                <p className="lede">
                  {e.author}, {e.created_at.replace("T", " ").replace("Z", "")}
                  {e.supersedes ? `, correcting “${titleOf.get(e.supersedes) ?? e.supersedes}”` : ""}
                </p>
                <Markdown source={e.body} />
                {e.artefacts.length ? (
                  <ul className="artefacts" aria-label="Attached runs">
                    {e.artefacts.map((h) => (
                      <li key={h} className="mono" data-artefact={h}>
                        {h}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {e.files.length ? (
                  <ul className="artefacts files" aria-label="Attached files">
                    {e.files.map((f) => (
                      <li key={f.digest + f.filename} data-file={f.digest}>
                        {INLINE_IMAGES.has(f.media_type) ? (
                          <a href={fileUrl(f)} target="_blank" rel="noopener noreferrer">
                            <img className="thumb" src={fileUrl(f)} alt={f.filename} loading="lazy" />
                          </a>
                        ) : (
                          <a href={fileUrl(f)} download={f.filename}>
                            {f.filename}
                          </a>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {!e.superseded_by ? (
                  <button type="button" onClick={() => setSupersedes(e.id)} aria-label={`Write a correction to ${e.title}`}>
                    Write a correction
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
          {entries.data && entries.data.length === 0 ? <EmptyState action={{ label: "Write the first entry", onClick: () => titleRef.current?.focus() }}>
              No entries yet: record what you decided and why.
            </EmptyState> : null}
          <section aria-label="New entry">
            <h2>{supersedes ? `Correct “${titleOf.get(supersedes)}”` : "New entry"}</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (title.trim()) add.mutate();
              }}
            >
              <label htmlFor="entry-title">Title</label>
              <input id="entry-title" ref={titleRef} value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: "100%", maxWidth: "64ch" }} />
              <label htmlFor="entry-body">Entry</label>
              <textarea id="entry-body" rows={4} value={body} onChange={(e) => setBody(e.target.value)} style={{ width: "100%", maxWidth: "64ch" }} />
              <fieldset className="attach">
                <legend>Attach runs (recorded by the hash of their run record)</legend>
                {(runs.data ?? []).map((r) => (
                  <label key={r.id} className="check">
                    <input
                      type="checkbox"
                      checked={attach.includes(r.id)}
                      onChange={(e) => setAttach((a) => (e.target.checked ? [...a, r.id] : a.filter((x) => x !== r.id)))}
                    />{" "}
                    {r.campaign} <span className="mono">{r.digest?.slice(0, 12)}</span>
                  </label>
                ))}
              </fieldset>
              <fieldset className="attach">
                <legend>Attach files (figures, PDFs, CSVs)</legend>
                <input
                  type="file"
                  aria-label="Attach a file"
                  disabled={uploading}
                  onChange={async (ev) => {
                    const f = ev.target.files?.[0];
                    ev.target.value = "";
                    if (!f) return;
                    setUploading(true);
                    setUploadError(null);
                    try {
                      const ref = await uploadNotebookFile(f);
                      setFiles((a) => (a.some((x) => x.digest === ref.digest && x.filename === ref.filename) ? a : [...a, ref]));
                    } catch (err) {
                      setUploadError(err instanceof Error ? err.message : String(err));
                    } finally {
                      setUploading(false);
                    }
                  }}
                />
                {uploading ? <span className="lede"> Uploading…</span> : null}
                {uploadError ? <p className="field-error">{uploadError}</p> : null}
                <ul>
                  {files.map((f) => (
                    <li key={f.digest + f.filename}>
                      {f.filename}{" "}
                      <button type="button" onClick={() => setFiles((a) => a.filter((x) => x !== f))} aria-label={`Remove ${f.filename}`}>
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              </fieldset>
              <p>
                <button className="primary" type="submit" disabled={add.isPending || uploading || !title.trim()}>
                  {supersedes ? "Add correction" : "Add entry"}
                </button>{" "}
                {supersedes ? (
                  <button type="button" onClick={() => setSupersedes(null)}>
                    Cancel correction
                  </button>
                ) : null}
              </p>
              {add.isError ? <p className="field-error">{add.error.message}</p> : null}
            </form>
          </section>
        </main>
      </div>
    </Shell>
  );
}
