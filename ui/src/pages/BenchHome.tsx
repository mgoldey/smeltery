import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { Shell } from "../components/Shell";
import { TaskInspector, WorkPanel } from "../components/WorkPanel";

export function BenchHome() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
  const [task, setTask] = useState<string | null>(null);
  const studies = useQuery({ queryKey: ["studies"], queryFn: () => unwrap(api.GET("/api/studies")) });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: () => unwrap(api.GET("/api/campaigns")) });
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => unwrap(api.POST("/api/campaigns", { body: { name, description: "" } })),
    onSuccess: (c) => {
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
      navigate(`/campaigns/${c.id}`);
    },
  });
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>Bench</h1>
          <p className="lede">
            Runs under this root. Open one to see its result, or <Link to="/compare">compare two runs</Link>.
          </p>
          <section aria-labelledby="runs-h">
            <h2 id="runs-h">Runs</h2>
            {runs.isError ? (
              <div className="banner" role="alert">
                Runs could not be listed: {String(runs.error.message)}
              </div>
            ) : runs.data && runs.data.length === 0 ? (
              <p className="empty-state">No run records found. Run a pipeline, or start Bench on a directory that holds runs.</p>
            ) : (
              <table className="grid">
                <thead>
                  <tr>
                    <th>Campaign</th>
                    <th>Parent</th>
                    <th className="num">Candidates</th>
                    <th>Verdict</th>
                    <th>Digest</th>
                  </tr>
                </thead>
                <tbody>
                  {(runs.data ?? []).map((r) => (
                    <tr key={r.idx}>
                      <td>
                        <Link to={`/run/${r.idx}`}>{r.campaign ?? r.path}</Link>
                        {r.error ? <div className="field-error">{r.error}</div> : null}
                      </td>
                      <td>{r.parent}</td>
                      <td className="num">{r.n_candidates ?? ""}</td>
                      <td>
                        <span className="status" data-state={r.verdict?.state}>
                          {r.verdict?.state === "UNRANKED" ? "does not resolve" : (r.verdict?.state ?? "unreadable")}
                        </span>
                      </td>
                      <td className="mono">{r.digest?.slice(0, 12)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <WorkPanel selected={task} onSelect={setTask} />
          <section aria-labelledby="campaigns-h">
            <h2 id="campaigns-h">Notebooks</h2>
            {campaigns.data && campaigns.data.length ? (
              <ul>
                {campaigns.data.map((c) => (
                  <li key={c.id}>
                    <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-state">No notebooks yet. Start one for a campaign below.</p>
            )}
            <form
              className="controls"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) create.mutate();
              }}
            >
              <div>
                <label htmlFor="campaign-name">Campaign name</label>
                <input id="campaign-name" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <button type="submit" disabled={create.isPending || !name.trim()}>
                Start notebook
              </button>
            </form>
          </section>
          <section aria-labelledby="studies-h">
            <h2 id="studies-h">Studies</h2>
            {studies.data && studies.data.length ? (
              <ul>
                {studies.data.map((m) => (
                  <li key={m}>
                    <Link className="mono" to={`/studies/${encodeURIComponent(m)}`}>
                      {m}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-state">
                No studies configured. Start Bench with <code>--study module.path</code> to show one.
              </p>
            )}
          </section>
        </main>
        {task ? <TaskInspector id={task} onClose={() => setTask(null)} /> : null}
      </div>
    </Shell>
  );
}
