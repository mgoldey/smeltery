import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../../api/client";
import { Shell } from "../../components/Shell";
import { TomlEditor } from "../../components/TomlEditor";
import { ConfigDiff } from "../../components/workspace/ConfigDiff";
import { ImpactStrip } from "../../components/workspace/ImpactStrip";
import { Launch } from "../../components/workspace/Launch";
import { useOpenRunPipeline } from "../../components/workspace/openFromRun";
import { PipelineDrawing } from "../../components/workspace/PipelineDrawing";
import { StepPanel, type EditExtra, type EditOp } from "../../components/workspace/StepPanel";
import { useRunIdx } from "./RunLayout";

type StepsOut = Schemas["StepsOut"];

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The run's "Pipeline" tab: open (or make, the first time) the pipeline that made this run, in the workspace. */
export function RunPipeline() {
  const idx = useRunIdx();
  const open = useOpenRunPipeline(idx);
  const { mutate } = open;
  useEffect(() => {
    mutate({ replace: true });
  }, [idx, mutate]);
  return (
    <div className="body">
      <main className="work">
        <h1>Pipeline</h1>
        {open.isError ? (
          <div className="banner" role="alert">
            The pipeline that made this run cannot be opened: {open.error.message}
          </div>
        ) : (
          <p className="lede">Opening the pipeline that made this run…</p>
        )}
      </main>
    </div>
  );
}

/**
 * The workspace: the pipeline drawn as its steps, the selected step edited in place, what the edit costs, the lines it
 * writes, and the TOML itself beside them. The TOML text is the only state: the draft is text, every step edit is
 * applied to it by the API, Keep saves it as the pipeline's config.toml, and Run runs what was kept.
 */
export function Workspace() {
  const { name = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const runParam = search.get("run");
  const sourceRun = runParam !== null && /^\d+$/.test(runParam) ? Number(runParam) : null;
  const qc = useQueryClient();

  const doc = useQuery({
    queryKey: ["pipeline", name],
    queryFn: () => unwrap(api.GET("/api/pipelines/{name}", { params: { path: { name } } })),
  });
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => unwrap(api.GET("/api/catalog")) });
  const saved = doc.data?.toml;

  const [draft, setDraftState] = useState<string | null>(null);
  const text = draft ?? saved ?? "";
  const latest = useRef(text);
  latest.current = text;
  const setDraft = useCallback((t: string | null) => {
    if (t !== null) latest.current = t;
    setDraftState(t);
  }, []);
  const dirty = draft !== null && saved !== undefined && draft !== saved;

  const stepsQ = useQuery({
    queryKey: ["steps", text, saved],
    queryFn: () => unwrap(api.POST("/api/pipelines/steps", { body: { toml: text, baseline_toml: saved ?? null } })),
    enabled: saved !== undefined,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const lastGood = useRef<StepsOut | undefined>(undefined);
  if (stepsQ.data && !stepsQ.isPlaceholderData) lastGood.current = stepsQ.data;
  const view = stepsQ.data ?? lastGood.current;
  const parseError = stepsQ.isError ? message(stepsQ.error) : null;

  const impact = useQuery({
    queryKey: ["impact", name, text, saved, sourceRun],
    queryFn: () =>
      unwrap(
        api.POST("/api/pipelines/impact", { body: { toml: text, baseline_toml: saved ?? "", source_run: sourceRun, name } }),
      ),
    enabled: saved !== undefined && stepsQ.isSuccess && !stepsQ.isPlaceholderData,
    placeholderData: keepPreviousData,
    retry: false,
  });

  const [busy, setBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const onEdit = useCallback(
    async (op: EditOp, key: string, extra: EditExtra = {}) => {
      setBusy(true);
      try {
        const r = await unwrap(
          api.POST("/api/pipelines/edit", {
            body: { toml: latest.current, op, key, params: extra.params ?? null, site: extra.site ?? null },
          }),
        );
        setDraft(r.toml);
        setEditError(null);
      } catch (e) {
        setEditError(message(e));
      } finally {
        setBusy(false);
      }
    },
    [setDraft],
  );

  const keep = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/pipelines/{name}", {
          params: { path: { name } },
          body: { toml: latest.current, base_sha256: doc.data?.sha256 ?? null },
        }),
      ),
    onSuccess: (d) => {
      qc.setQueryData(["pipeline", name], d);
      setDraft(null);
    },
  });
  const preview = useMutation({
    mutationFn: () => unwrap(api.POST("/api/pipelines/{name}/preview", { params: { path: { name } } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["impact", name] }),
  });

  const steps = view?.steps ?? [];
  const firstNeeding = steps.find((s) => s.state === "needs_input")?.key;
  const selected = search.get("step") ?? firstNeeding ?? steps[0]?.key ?? "";
  const index = steps.findIndex((s) => s.key === selected);
  const step = index >= 0 ? steps[index] : undefined;
  const select = (k: string) =>
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("step", k);
        return next;
      },
      { replace: true },
    );
  const disabled = busy || parseError !== null || keep.isPending;
  const keptErrors = (doc.data?.issues ?? []).filter((i) => i.level === "error");

  return (
    <Shell>
      <div className="body">
        <main className="work" data-testid="workspace">
          <h1>
            Pipeline <span className="mono">{name}</span>
          </h1>
          <p className="lede">
            The steps in the order <code>smeltery run</code> executes them. Open a step to change it in place: edits are a
            draft until Keep, which writes them into the config file (also while it has errors; only Run is blocked by them).
            {sourceRun !== null ? (
              <>
                {" "}
                Made from <Link to={`/run/${sourceRun}/results`}>run {sourceRun}</Link>, which is never changed: running makes a
                new run directory.
              </>
            ) : null}
          </p>
          {doc.isError ? (
            <div className="banner" role="alert">
              {doc.error.message}
            </div>
          ) : null}
          {doc.data?.notes.map((n) => (
            <p key={n} className="banner note" role="note">
              {n}
            </p>
          ))}
          {parseError ? (
            <div className="banner" role="alert" data-testid="toml-error">
              The TOML does not parse, so step edits are blocked until it does: {parseError}
            </div>
          ) : null}
          {editError ? (
            <div className="banner" role="alert">
              {editError}
            </div>
          ) : null}
          {keep.isError ? (
            <div className="banner" role="alert">
              Not kept: {keep.error.message}
            </div>
          ) : null}
          <div className="workspace">
            <div>
              {view && view.pipeline_issues.length ? (
                <ul className="issues" aria-label="Pipeline issues">
                  {view.pipeline_issues.map((iss, i) => (
                    <li key={i} className="issue" data-level={iss.level}>
                      <span className="issue-level">{iss.level}</span>
                      {iss.stage ? <span className="mono">{iss.stage}</span> : null}
                      <span>{iss.message}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {steps.length ? <PipelineDrawing steps={steps} selected={selected} onSelect={select} /> : null}
              {step ? (
                <StepPanel
                  key={step.key}
                  step={step}
                  steps={steps}
                  index={index}
                  catalog={catalog.data}
                  ligands={view?.ligands}
                  disabled={disabled}
                  onEdit={onEdit}
                />
              ) : null}
              {doc.data ? (
                <ImpactStrip
                  dirty={dirty}
                  impact={impact.data}
                  loading={impact.isFetching}
                  error={impact.isError ? message(impact.error) : null}
                  pending={keep.isPending || busy}
                  onDiscard={() => {
                    setDraft(null);
                    keep.reset();
                  }}
                  onKeep={() => keep.mutate()}
                  onPreview={() => preview.mutate()}
                  previewing={preview.isPending ? "pending" : null}
                />
              ) : null}
              {view ? <ConfigDiff diff={view.diff} /> : null}
              {doc.data ? <Launch name={name} errors={keptErrors} dirty={dirty} /> : null}
            </div>
            <section className="workspace-side" aria-label="Pipeline TOML">
              <h2>config.toml</h2>
              {doc.data ? (
                <>
                  <p className="lede mono" style={{ wordBreak: "break-all" }}>
                    {doc.data.folder}/config.toml
                  </p>
                  <TomlEditor value={text} onCommit={(t) => setDraft(t)} label="Pipeline TOML" />
                </>
              ) : null}
            </section>
          </div>
        </main>
      </div>
    </Shell>
  );
}
