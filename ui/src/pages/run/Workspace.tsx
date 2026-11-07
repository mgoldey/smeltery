import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../../api/client";
import { ApiError } from "../../api/http";
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

type Issue = Schemas["Issue"];
type StepOut = Schemas["StepOut"];

/** One heading per step that has issues (its label, clickable to select it), then the pipeline-level ones. */
function groupIssues(pipelineIssues: Issue[], steps: StepOut[]) {
  const groups = steps
    .filter((s) => s.issues.length)
    .map((s) => ({ title: s.label, key: s.key as string | null, items: s.issues }));
  if (pipelineIssues.length) groups.unshift({ title: "Pipeline", key: null, items: pipelineIssues });
  return groups;
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
 * The workspace: the pipeline drawn as its steps, the selected step edited in place, what the edit costs, and the lines
 * it writes. The TOML text is the only state: the draft is text, every step edit is applied to it by the API, Keep
 * saves it as the pipeline's config.toml, and Run runs what was kept. The config leaves as a download (or is typed
 * under "Edit as TOML"), rather than sitting in a pane that crowds the steps.
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
  // A 400 from the steps route is text that does not parse (it names the line); anything else (a broken
  // methods.toml is a 409, a crash a 500) is shown as what it is.
  const stepsError = stepsQ.isError ? message(stepsQ.error) : null;
  const parseError = stepsQ.isError && stepsQ.error instanceof ApiError && stepsQ.error.status === 400 ? stepsError : null;
  const otherError = stepsError !== null && parseError === null ? stepsError : null;

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
            body: {
              toml: latest.current,
              op,
              key,
              params: extra.params ?? null,
              site: extra.site ?? null,
              baseline_toml: extra.baseline_toml ?? null,
            },
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

  /** A view that edits the pipeline hands back new TOML; it becomes the draft exactly as a step edit does. */
  const onToml = useCallback(
    (t: string) => {
      setDraft(t);
      setEditError(null);
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
  const disabled = busy || stepsError !== null || keep.isPending;
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
          {otherError ? (
            <div className="banner" role="alert" data-testid="steps-error">
              The steps cannot be drawn: {otherError}
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
              {doc.data ? <ConfigToolbar folder={doc.data.folder} text={text} dirty={dirty} name={name} onCommit={(t) => setDraft(t)} /> : null}
              {view && view.pipeline_issues.length ? (
                <div className="issues-grouped" aria-label="Pipeline issues">
                  {groupIssues(view.pipeline_issues, steps).map((g) => (
                    <section key={g.key ?? ""}>
                      <h3>{g.title}</h3>
                      <ul className="issues">
                        {g.items.map((iss, i) => (
                          <li key={i} className="issue" data-level={iss.level}>
                            <span className="issue-level">{iss.level}</span>
                            {g.key ? (
                              <button type="button" className="link" onClick={() => select(g.key!)}>
                                {iss.message}
                              </button>
                            ) : (
                              <span>{iss.message}</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              ) : null}
              {steps.length ? <PipelineDrawing steps={steps} selected={selected} onSelect={select} /> : null}
              {step ? (
                <StepPanel
                  key={step.key}
                  pipeline={name}
                  toml={text}
                  sourceRun={sourceRun}
                  step={step}
                  steps={steps}
                  index={index}
                  catalog={catalog.data}
                  ligands={view?.ligands}
                  disabled={disabled}
                  onEdit={onEdit}
                  onToml={onToml}
                />
              ) : null}
              {doc.data ? (
                <ImpactStrip
                  dirty={dirty}
                  impact={impact.data}
                  loading={impact.isFetching}
                  error={impact.isError ? message(impact.error) : null}
                  pending={keep.isPending || busy}
                  stepLabel={step?.label ?? null}
                  stepChanged={step?.state === "changed"}
                  onDiscardStep={() => {
                    if (step && saved !== undefined) void onEdit("revert", step.key, { baseline_toml: saved });
                  }}
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
          </div>
        </main>
      </div>
    </Shell>
  );
}

/**
 * The config as a file you take away, not a pane you have to read: a download of exactly the text on screen (the
 * draft when there are unkept edits, and the button says so), and the raw TOML editor folded away for whoever wants
 * to type it. The text is still the only state; this is just where it lives on the page.
 */
function ConfigToolbar({
  folder,
  text,
  dirty,
  name,
  onCommit,
}: {
  folder: string;
  text: string;
  dirty: boolean;
  name: string;
  onCommit: (text: string) => void;
}) {
  const download = () => {
    // The anchor goes into the document and the blob URL is revoked in a later task: Firefox starts the download
    // after the click handler returns, and revoking in the same task leaves it empty or never started. This is the
    // only way the config leaves the page now that the text is folded away, so it has to work outside Chromium.
    const url = URL.createObjectURL(new Blob([text], { type: "application/toml" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.config.toml`;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      a.remove();
      URL.revokeObjectURL(url);
    }, 0);
  };
  return (
    <div className="config-toolbar">
      <div className="config-toolbar-row">
        <button type="button" onClick={download}>
          {dirty ? "Download draft config.toml" : "Download config.toml"}
        </button>
        <a href={`/api/pipelines/${encodeURIComponent(name)}/bundle`} download>
          Download saved bundle
        </a>
        {dirty ? <span className="lede">(unsaved edits not included)</span> : null}
        <span className="lede mono" title={`${folder}/config.toml`}>
          {folder}/config.toml
        </span>
      </div>
      <details className="config-edit">
        <summary>Edit as TOML</summary>
        <TomlEditor value={text} onCommit={onCommit} label="Pipeline TOML" />
      </details>
    </div>
  );
}
