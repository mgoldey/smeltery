import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCanWrite } from "../api/capabilities";
import { api, unwrap, type RunSummary } from "../api/client";
import { ApiError, reasonOf } from "../api/http";
import { EmptyState } from "../components/EmptyState";
import { Markdown } from "../components/Markdown";
import { Shell } from "../components/Shell";

type FileRef = {
  digest: string;
  filename: string;
  media_type: string;
  kind: "file";
};
const INLINE_IMAGES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);
const fileUrl = (f: FileRef) =>
  `/api/notebook/artifacts/${f.digest}?name=${encodeURIComponent(f.filename)}`;

async function uploadNotebookFile(file: File): Promise<FileRef> {
  const res = await fetch(
    `/api/notebook/artifacts?filename=${encodeURIComponent(file.name)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    },
  );
  if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
  return (await res.json()) as FileRef;
}

/**
 * An attached run's hash, turned back into a run you can open.
 *
 * The notebook stores an attachment as the sha256 of the run record's *bytes*, which is deliberately not the run's
 * id (an id names a run directory that could be moved or re-listed; the hash names the content, which is the point
 * of an archive). Nothing serves that mapping, so the join is done here out of two routes that already exist: the
 * archived bytes are fetched back and their `input_digest` read, and that digest is matched against the one every
 * run in `GET /api/runs` reports. It is an identity, not a guess -- an unmatched hash gets no link rather than a
 * likely one.
 *
 * `null` means the archived record was read and no run on this Bench has that digest: the usual reason is a record
 * archived from a run directory that is no longer served, which is exactly the case the hash exists to survive.
 */
function useAttachedRuns(digests: string[], runs: RunSummary[] | undefined) {
  const byDigest = new Map((runs ?? []).filter((r) => r.digest).map((r) => [r.digest, r]));
  const records = useQueries({
    queries: digests.map((d) => ({
      queryKey: ["notebook-record", d],
      // Not `api.GET`: the artifact route serves bytes with a media type from the filename, so it is outside the
      // generated client's typed surface. The record is JSON and small (a few KB); only `input_digest` is read.
      queryFn: async (): Promise<string | null> => {
        const res = await fetch(`/api/notebook/artifacts/${d}`);
        if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
        const rec = (await res.json()) as { input_digest?: unknown };
        return typeof rec.input_digest === "string" ? rec.input_digest : null;
      },
      staleTime: Infinity, // content-addressed: these bytes cannot change under this hash
      retry: false,
    })),
  });
  const out = new Map<string, RunSummary | null | undefined>();
  digests.forEach((d, i) => {
    // In flight, failed, or the record carried no digest: all three are "not known to be a run served here", and
    // the hash is shown with no claim either way rather than with a wrong one.
    const data = records[i]?.data;
    out.set(d, data == null ? undefined : (byDigest.get(data) ?? null));
  });
  return out;
}

/** An entry's attached runs: each one's hash, linked to the run when this Bench still serves it. */
function AttachedRuns({
  digests,
  resolved,
}: {
  digests: string[];
  resolved: Map<string, RunSummary | null | undefined>;
}) {
  return (
    <ul className="artefacts" aria-label="Attached runs">
      {digests.map((h) => {
        const run = resolved.get(h);
        return (
          <li key={h} data-artefact={h}>
            {run ? (
              <Link to={`/run/${run.id}/results`} className="mono">
                {h}
              </Link>
            ) : (
              <span className="mono">{h}</span>
            )}
            {run ? (
              <span className="lede">
                {" "}
                · {run.campaign} {run.digest?.slice(0, 12)}
              </span>
            ) : run === null ? (
              // Not "the run was deleted": the record is archived and readable, it is the run directory that this
              // Bench no longer lists. Offering the archived bytes is the one thing that is certainly still true.
              <span className="lede">
                {" "}
                · no run with this record is served here;{" "}
                <a href={`/api/notebook/artifacts/${h}?name=${h.slice(0, 12)}.json`} download>
                  download the archived record
                </a>
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
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
    queryFn: () =>
      unwrap(
        api.GET("/api/campaigns/{campaign_id}", {
          params: { path: { campaign_id: id } },
        }),
      ),
  });
  const entries = useQuery({
    queryKey: ["entries", id],
    queryFn: () =>
      unwrap(
        api.GET("/api/campaigns/{campaign_id}/entries", {
          params: { path: { campaign_id: id } },
        }),
      ),
  });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => unwrap(api.GET("/api/runs")),
  });
  const titleRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [attach, setAttach] = useState<string[]>([]);
  const [files, setFiles] = useState<FileRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [supersedes, setSupersedes] = useState<string | null>(null);
  const canWrite = useCanWrite();
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
  // One read per distinct hash, however many entries attached it: an entry and its correction normally attach the
  // same run, and the records are content-addressed so a repeat read would answer identically.
  const attached = useAttachedRuns(
    [...new Set((entries.data ?? []).flatMap((e) => e.artefacts))],
    runs.data,
  );
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
          <p className="lede">
            Entries are never edited. To correct one, write a new entry that
            supersedes it; the old one stays readable.
          </p>
          {/* An empty <ol> on a failed read is indistinguishable from a notebook nobody has written in. The
              EmptyState below claims the second; this claims only the first. */}
          {entries.isError ? (
            <div className="banner" role="alert">
              The entries could not be read: {entries.error.message}. This
              notebook may well have entries; none can be shown. Reload to try
              again.
            </div>
          ) : null}
          <ol className="entries">
            {(entries.data ?? []).map((e) => (
              <li
                key={e.id}
                className={e.superseded_by ? "entry superseded" : "entry"}
                data-entry={e.id}
                id={`entry-${e.id}`}
              >
                <h3>
                  {e.title}
                  {e.superseded_by ? (
                    <span className="lede">
                      {" "}
                      superseded by{" "}
                      <a href={`#entry-${e.superseded_by}`}>
                        {titleOf.get(e.superseded_by) ?? "a later entry"}
                      </a>
                    </span>
                  ) : null}
                </h3>
                <p className="lede">
                  {e.author}, {e.created_at.replace("T", " ").replace("Z", "")}
                  {e.supersedes
                    ? `, correcting “${titleOf.get(e.supersedes) ?? e.supersedes}”`
                    : ""}
                </p>
                <Markdown source={e.body} />
                {e.artefacts.length ? (
                  <AttachedRuns digests={e.artefacts} resolved={attached} />
                ) : null}
                {e.files.length ? (
                  <ul className="artefacts files" aria-label="Attached files">
                    {e.files.map((f) => (
                      <li key={f.digest + f.filename} data-file={f.digest}>
                        {INLINE_IMAGES.has(f.media_type) ? (
                          <a
                            href={fileUrl(f)}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <img
                              className="thumb"
                              src={fileUrl(f)}
                              alt={f.filename}
                              loading="lazy"
                            />
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
                {!e.superseded_by && canWrite ? (
                  <button
                    type="button"
                    onClick={() => setSupersedes(e.id)}
                    aria-label={`Write a correction to ${e.title}`}
                  >
                    Write a correction
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
          {entries.data && entries.data.length === 0 ? (
            <EmptyState
              action={
                !canWrite
                  ? undefined
                  : {
                      label: "Write the first entry",
                      onClick: () => titleRef.current?.focus(),
                    }
              }
            >
              No entries yet: record what you decided and why.
            </EmptyState>
          ) : null}
          {canWrite ? (
            <section aria-label="New entry">
              <h2>
                {supersedes
                  ? `Correct “${titleOf.get(supersedes)}”`
                  : "New entry"}
              </h2>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (title.trim()) add.mutate();
                }}
              >
                <label htmlFor="entry-title">Title</label>
                <input
                  id="entry-title"
                  ref={titleRef}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  style={{ width: "100%", maxWidth: "64ch" }}
                />
                <label htmlFor="entry-body">Entry</label>
                <textarea
                  id="entry-body"
                  rows={4}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  style={{ width: "100%", maxWidth: "64ch" }}
                />
                <fieldset className="attach">
                  <legend>
                    Attach runs (recorded by the hash of their run record)
                  </legend>
                  {(runs.data ?? []).map((r) => (
                    <label key={r.id} className="check">
                      <input
                        type="checkbox"
                        checked={attach.includes(r.id)}
                        onChange={(e) =>
                          setAttach((a) =>
                            e.target.checked
                              ? [...a, r.id]
                              : a.filter((x) => x !== r.id),
                          )
                        }
                      />{" "}
                      {r.campaign}{" "}
                      <span className="mono">{r.digest?.slice(0, 12)}</span>
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
                        setFiles((a) =>
                          a.some(
                            (x) =>
                              x.digest === ref.digest &&
                              x.filename === ref.filename,
                          )
                            ? a
                            : [...a, ref],
                        );
                      } catch (err) {
                        setUploadError(
                          err instanceof Error ? err.message : String(err),
                        );
                      } finally {
                        setUploading(false);
                      }
                    }}
                  />
                  {uploading ? <span className="lede"> Uploading…</span> : null}
                  {uploadError ? (
                    <p className="field-error">{uploadError}</p>
                  ) : null}
                  <ul>
                    {files.map((f) => (
                      <li key={f.digest + f.filename}>
                        {f.filename}{" "}
                        <button
                          type="button"
                          onClick={() =>
                            setFiles((a) => a.filter((x) => x !== f))
                          }
                          aria-label={`Remove ${f.filename}`}
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                </fieldset>
                <p>
                  <button
                    className="primary"
                    type="submit"
                    disabled={add.isPending || uploading || !title.trim()}
                  >
                    {supersedes ? "Add correction" : "Add entry"}
                  </button>{" "}
                  {supersedes ? (
                    <button type="button" onClick={() => setSupersedes(null)}>
                      Cancel correction
                    </button>
                  ) : null}
                </p>
                {add.isError ? (
                  <p className="field-error">{add.error.message}</p>
                ) : null}
              </form>
            </section>
          ) : null}
        </main>
      </div>
    </Shell>
  );
}
