import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, unwrap } from "../api/client";
import { Shell } from "../components/Shell";
import { TaskInspector, WorkPanel } from "../components/WorkPanel";

/** "name SMILES" per line; at least one analogue. */
export function parseAnalogues(text: string): { ok: boolean; candidates: Record<string, string>; why: string } {
  const candidates: Record<string, string> = {};
  for (const line of text.split("\n").map((l) => l.trim()).filter(Boolean)) {
    const [name, smiles, ...rest] = line.split(/\s+/);
    if (!name || !smiles || rest.length) return { ok: false, candidates, why: `"${line}": give a name and a SMILES` };
    candidates[name] = smiles;
  }
  const ok = Object.keys(candidates).length > 0;
  return { ok, candidates, why: ok ? "" : "at least one analogue" };
}

export function BenchHome() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
  const [task, setTask] = useState<string | null>(null);
  const studies = useQuery({ queryKey: ["studies"], queryFn: () => unwrap(api.GET("/api/studies")) });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: () => unwrap(api.GET("/api/campaigns")) });
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: () => unwrap(api.GET("/api/pipelines")) });
  const [pipeName, setPipeName] = useState("");
  const [fromRun, setFromRun] = useState("");
  const newPipeline = useMutation({
    mutationFn: () => unwrap(api.POST("/api/pipelines", { body: { name: pipeName.trim(), from_run: Number(fromRun) } })),
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      navigate(`/pipelines/${d.name}?run=${fromRun}`);
    },
  });
  const prebuilt = useQuery({ queryKey: ["prebuilt"], queryFn: () => unwrap(api.GET("/api/prebuilt")) });
  const [prebuiltName, setPrebuiltName] = useState<Record<string, string>>({});
  const startPrebuilt = useMutation({
    mutationFn: async (p: string) => {
      const name = (prebuiltName[p] ?? p).trim();
      const made = await unwrap(api.POST("/api/pipelines", { body: { name, from_prebuilt: p } }));
      // The example run was copied into the new pipeline's folder, so the run store lists it: find its index now.
      const list = await unwrap(api.GET("/api/runs"));
      const mine = list.find((r) => r.path.includes(`/pipelines/${made.name}/run/`));
      return { name: made.name, idx: mine?.idx ?? null };
    },
    onSuccess: ({ name, idx }) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      void qc.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/pipelines/${name}?${idx === null ? "" : `run=${idx}&`}step=measure`);
    },
  });
  const [blank, setBlank] = useState({ name: "", campaign: "", parent: "", parentSmiles: "", analogues: "" });
  const analogues = parseAnalogues(blank.analogues);
  const blankReady =
    !!blank.name.trim() && !!blank.campaign.trim() && !!blank.parent.trim() && !!blank.parentSmiles.trim() && analogues.ok;
  const newBlank = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/pipelines", {
          body: {
            name: blank.name.trim(),
            blank: {
              campaign: blank.campaign.trim(),
              parent: blank.parent.trim(),
              candidates: { [blank.parent.trim()]: blank.parentSmiles.trim(), ...analogues.candidates },
            },
          },
        }),
      ),
    onSuccess: (d) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      navigate(`/pipelines/${d.name}?step=ligands`);
    },
  });
  const field = (k: keyof typeof blank) => ({
    value: blank[k],
    onChange: (e: { target: { value: string } }) => setBlank((b) => ({ ...b, [k]: e.target.value })),
  });
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
            A pipeline is six steps from an active site to a ranked shortlist. Start one below, then edit its steps and
            run it. Finished runs are listed under Your work, and two of them <Link to="/compare">compare</Link>.
          </p>

          <section className="start" aria-labelledby="start-h" role="region">
            <h2 id="start-h">Start here</h2>
            <div className="start-ways">
              <div className="start-way" data-way="prebuilt">
                <h3 className="start-step">
                  <span className="start-n">1</span> Copy a working pipeline
                  <span className="start-best">easiest</span>
                </h3>
                <p className="lede">
                  Five pipelines that already run, each with a finished example run to read. You get your own copy to
                  change; the original is never touched.
                </p>
                <div role="group" aria-labelledby="prebuilt-h">
                  <h4 id="prebuilt-h">Start from a working pipeline</h4>
                  {prebuilt.isError ? (
                    <div className="banner" role="alert">
                      Working pipelines could not be listed: {prebuilt.error.message}
                    </div>
                  ) : (
                    <ul className="prebuilt-list">
                      {(prebuilt.data ?? []).map((p) => (
                        <li key={p.name} data-prebuilt={p.name}>
                          <div className="prebuilt-head">
                            <strong className="mono">{p.name}</strong>{" "}
                            <span className="status" data-state={p.verdict.state}>
                              {p.verdict.state === "UNRANKED" ? "does not resolve" : p.verdict.state}
                            </span>
                          </div>
                          <div className="prebuilt-about">{p.about}</div>
                          <div className="lede prebuilt-needs">{p.needs}</div>
                          <div className="prebuilt-go">
                            <label htmlFor={`prebuilt-name-${p.name}`}>Name of your copy</label>{" "}
                            <input
                              id={`prebuilt-name-${p.name}`}
                              className="mono"
                              value={prebuiltName[p.name] ?? p.name}
                              onChange={(e) => setPrebuiltName((m) => ({ ...m, [p.name]: e.target.value }))}
                            />{" "}
                            <button type="button" disabled={startPrebuilt.isPending} onClick={() => startPrebuilt.mutate(p.name)}>
                              Start from {p.name}
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                  {startPrebuilt.isError ? <p className="field-error">{startPrebuilt.error.message}</p> : null}
                </div>
              </div>

              <div className="start-way" data-way="blank">
                <h3 className="start-step">
                  <span className="start-n">2</span> Start from your own molecules
                </h3>
                <p className="lede">
                  A parent and its analogues, with the steps placed but not yet filled in: you give the active site (a
                  PDB file, a prepared pocket, or charges placed by hand) on step 1.
                </p>
                <form
                  aria-label="New blank pipeline"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (blankReady) newBlank.mutate();
                  }}
                >
                  <div className="controls">
                    <div>
                      <label htmlFor="blank-name">Pipeline name</label>
                      <input id="blank-name" className="mono" {...field("name")} />
                    </div>
                    <div>
                      <label htmlFor="blank-campaign">Campaign</label>
                      <input id="blank-campaign" {...field("campaign")} />
                    </div>
                    <div>
                      <label htmlFor="blank-parent">Parent name</label>
                      <input id="blank-parent" {...field("parent")} />
                    </div>
                    <div>
                      <label htmlFor="blank-parent-smiles">Parent SMILES</label>
                      <input id="blank-parent-smiles" className="mono" {...field("parentSmiles")} />
                    </div>
                  </div>
                  <label htmlFor="blank-analogues">Analogues, one per line: name, then SMILES</label>
                  <textarea id="blank-analogues" className="mono" rows={3} cols={60} {...field("analogues")} />
                  {blank.analogues.trim() && !analogues.ok ? <p className="field-error">{analogues.why}</p> : null}
                  <p>
                    <button type="submit" disabled={newBlank.isPending || !blankReady}>
                      New blank pipeline
                    </button>
                  </p>
                </form>
                {newBlank.isError ? <p className="field-error">{newBlank.error.message}</p> : null}
              </div>

              <div className="start-way" data-way="from-run">
                <h3 className="start-step">
                  <span className="start-n">3</span> Continue from a finished run
                </h3>
                <p className="lede">
                  Copies the config that made that run, with its input files. The run itself is never changed: running
                  your copy writes a new run directory.
                </p>
                <form
                  className="controls"
                  aria-label="New pipeline"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (pipeName.trim() && fromRun !== "") newPipeline.mutate();
                  }}
                >
                  <div>
                    <label htmlFor="pipeline-name">Pipeline name</label>
                    <input id="pipeline-name" className="mono" value={pipeName} onChange={(e) => setPipeName(e.target.value)} />
                  </div>
                  <div>
                    <label htmlFor="pipeline-from">Start from run</label>
                    <select id="pipeline-from" value={fromRun} onChange={(e) => setFromRun(e.target.value)}>
                      <option value="">choose a run</option>
                      {(runs.data ?? []).map((r) => (
                        <option key={r.idx} value={String(r.idx)}>
                          {r.idx}: {r.campaign ?? r.path} {r.digest?.slice(0, 12)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button type="submit" disabled={newPipeline.isPending || !pipeName.trim() || fromRun === ""}>
                    New pipeline
                  </button>
                </form>
                {newPipeline.isError ? <p className="field-error">{newPipeline.error.message}</p> : null}
                {runs.data && runs.data.length === 0 ? <p className="empty-state">No runs yet to start from.</p> : null}
              </div>
            </div>
          </section>

          <h2 className="section-break">Your work</h2>
          <section aria-labelledby="pipelines-h">
            <h3 id="pipelines-h">Pipelines</h3>
            {pipelines.isError ? (
              <div className="banner" role="alert">
                Pipelines could not be listed: {pipelines.error.message}
              </div>
            ) : pipelines.data && pipelines.data.length ? (
              <ul>
                {pipelines.data.map((n) => (
                  <li key={n}>
                    <Link className="mono" to={`/pipelines/${n}`}>
                      {n}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty-state">No pipelines yet. Start one above.</p>
            )}
          </section>
          <section aria-labelledby="runs-h">
            <h3 id="runs-h">Runs</h3>
            {runs.isError ? (
              <div className="banner" role="alert">
                Runs could not be listed: {String(runs.error.message)}
              </div>
            ) : runs.data && runs.data.length === 0 ? (
              <p className="empty-state">
                Nothing has run yet. Start a pipeline above and press Run, or point Bench at a directory that already
                holds runs.
              </p>
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
          <div className="more">
          <section aria-labelledby="campaigns-h">
            <h3 id="campaigns-h">Notebooks</h3>
            {campaigns.data && campaigns.data.length ? (
              <ul>
                {campaigns.data.map((c) => (
                  <li key={c.id}>
                    <Link to={`/campaigns/${c.id}`}>{c.name}</Link>
                    <span style={{ color: "var(--ink-muted)" }}>
                      {" "}
                      {c.n_entries} {c.n_entries === 1 ? "entry" : "entries"} · {c.n_runs}{" "}
                      {c.n_runs === 1 ? "run" : "runs"} · updated {c.updated_at.slice(0, 10)}
                    </span>
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
            <h3 id="studies-h">Studies</h3>
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
          </div>
        </main>
        {task ? <TaskInspector id={task} onClose={() => setTask(null)} /> : null}
      </div>
    </Shell>
  );
}
