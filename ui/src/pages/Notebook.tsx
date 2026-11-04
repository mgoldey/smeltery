import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { Shell } from "../components/Shell";

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
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [attach, setAttach] = useState<number[]>([]);
  const [supersedes, setSupersedes] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/campaigns/{campaign_id}/entries", {
          params: { path: { campaign_id: id } },
          body: { title, body, runs: attach, supersedes },
        }),
      ),
    onSuccess: () => {
      setTitle("");
      setBody("");
      setAttach([]);
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
                <p className="entry-body">{e.body}</p>
                {e.artefacts.length ? (
                  <ul className="artefacts" aria-label="Attached runs">
                    {e.artefacts.map((h) => (
                      <li key={h} className="mono" data-artefact={h}>
                        {h}
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
          {entries.data && entries.data.length === 0 ? <p className="empty-state">No entries yet. Write the first one below.</p> : null}
          <section aria-label="New entry">
            <h2>{supersedes ? `Correct “${titleOf.get(supersedes)}”` : "New entry"}</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (title.trim()) add.mutate();
              }}
            >
              <label htmlFor="entry-title">Title</label>
              <input id="entry-title" value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: "100%", maxWidth: "64ch" }} />
              <label htmlFor="entry-body">Entry</label>
              <textarea id="entry-body" rows={4} value={body} onChange={(e) => setBody(e.target.value)} style={{ width: "100%", maxWidth: "64ch" }} />
              <fieldset className="attach">
                <legend>Attach runs (recorded by the hash of their run record)</legend>
                {(runs.data ?? []).map((r) => (
                  <label key={r.idx} className="check">
                    <input
                      type="checkbox"
                      checked={attach.includes(r.idx)}
                      onChange={(e) => setAttach((a) => (e.target.checked ? [...a, r.idx] : a.filter((x) => x !== r.idx)))}
                    />{" "}
                    {r.campaign} <span className="mono">{r.digest?.slice(0, 12)}</span>
                  </label>
                ))}
              </fieldset>
              <p>
                <button className="primary" type="submit" disabled={add.isPending || !title.trim()}>
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
