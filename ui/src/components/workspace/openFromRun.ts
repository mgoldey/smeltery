import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, unwrap, type RunSummary } from "../../api/client";
import { ApiError, describeReason } from "../../api/http";

/** The workspace name of the pipeline made from a run: its campaign and digest, so reopening finds the same one. */
export function pipelineName(run: Pick<RunSummary, "idx" | "campaign" | "digest">): string {
  const slug = (run.campaign ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[^a-z0-9]+/, "")
    .slice(0, 40)
    .replace(/-+$/, "");
  const tag = run.digest ? run.digest.slice(0, 8).toLowerCase() : `run${run.idx}`;
  return slug ? `${slug}-${tag}` : `run-${tag}`;
}

/** The pipeline that made run `idx`: created from the run's recorded config the first time, reopened after. */
export async function pipelineForRun(idx: number, runs: RunSummary[]): Promise<string> {
  const run = runs.find((r) => r.idx === idx);
  if (!run) throw new Error(`no run ${idx}`);
  const name = pipelineName(run);
  // Made earlier (and maybe edited since): reopen it. Only a 404 means "make it"; the create's own refusals
  // (a recorded input missing from the run directory, 409) are shown as they are.
  const got = await api.GET("/api/pipelines/{name}", { params: { path: { name } } });
  if (got.response.ok) return name;
  if (got.response.status !== 404) throw new ApiError(got.response.status, describeReason(got.response.status, got.response.statusText, got.error));
  const made = await api.POST("/api/pipelines", { body: { name, from_run: idx } });
  if (made.response.ok) return name;
  throw new ApiError(made.response.status, describeReason(made.response.status, made.response.statusText, made.error));
}

/** Open the workspace on the pipeline that made run `idx`, optionally on one step. */
export function useOpenRunPipeline(idx: number) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: async ({ step, replace = false }: { step?: string; replace?: boolean } = {}) => {
      const runs = await qc.fetchQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
      return { name: await pipelineForRun(idx, runs), step, replace };
    },
    onSuccess: ({ name, step, replace }) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      navigate(`/pipelines/${name}?run=${idx}${step ? `&step=${step}` : ""}`, { replace });
    },
  });
}
