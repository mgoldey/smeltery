import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, runByRef, unwrap, type RunSummary } from "../../api/client";
import { ApiError, describeReason } from "../../api/http";

/** The workspace name of the pipeline made from a run: its campaign and digest, so reopening finds the same one. */
export function pipelineName(run: Pick<RunSummary, "id" | "campaign" | "digest">): string {
  const slug = (run.campaign ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[^a-z0-9]+/, "")
    .slice(0, 40)
    .replace(/-+$/, "");
  const tag = run.digest ? run.digest.slice(0, 8).toLowerCase() : `run${run.id}`;
  return slug ? `${slug}-${tag}` : `run-${tag}`;
}

/** The pipeline that made run `runId`, and the run's stable id: created from the run's recorded config the first
 * time, reopened after. The id is returned so an old positional link is normalised before anything new links to it. */
export async function pipelineForRun(runId: string, runs: RunSummary[]): Promise<{ name: string; id: string }> {
  const run = runByRef(runs, runId);
  if (!run) throw new Error(`no run ${runId}`);
  const name = pipelineName(run);
  // Made earlier (and maybe edited since): reopen it. Only a 404 means "make it"; the create's own refusals
  // (a recorded input missing from the run directory, 409) are shown as they are.
  const got = await api.GET("/api/pipelines/{name}", { params: { path: { name } } });
  if (got.response.ok) return { name, id: run.id };
  if (got.response.status !== 404) throw new ApiError(got.response.status, describeReason(got.response.status, got.response.statusText, got.error));
  const made = await api.POST("/api/pipelines", { body: { name, from_run: run.id } });
  if (made.response.ok) return { name, id: run.id };
  throw new ApiError(made.response.status, describeReason(made.response.status, made.response.statusText, made.error));
}

/** Open the workspace on the pipeline that made run `runId`, optionally on one step. */
export function useOpenRunPipeline(runId: string) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: async ({ step, replace = false }: { step?: string; replace?: boolean } = {}) => {
      const runs = await qc.fetchQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
      return { ...(await pipelineForRun(runId, runs)), step, replace };
    },
    onSuccess: ({ name, id, step, replace }) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      navigate(`/pipelines/${name}?run=${id}${step ? `&step=${step}` : ""}`, { replace });
    },
  });
}

/**
 * Start the next round from a run's decisions and accepted proposals.
 *
 * This is the edge the app was missing: a decision recorded here had no way to reach the next run, so the user left
 * Bench, hand-edited `[decisions] file = "..."` into a TOML, and came back. The server does the carrying
 * (`use_cases/next_round.py`); this opens the workspace on the new pipeline's Ligands step, where the candidate list
 * is what the round is about.
 *
 * The server's `notes` explain what it carried and dropped. The workspace reads them from the pipeline itself
 * (they are written into its config and read back on every GET), so they survive a reload rather than vanishing
 * like a toast.
 */
export function useStartNextRound(runId: string) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: async ({
      addProposals = [],
      drop = [],
      carryDecisions = true,
    }: { addProposals?: string[]; drop?: string[]; carryDecisions?: boolean } = {}) => {
      const runs = await qc.fetchQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/api/runs")) });
      const run = runByRef(runs, runId);
      if (!run) throw new Error(`no run ${runId}`);
      const made = await api.POST("/api/pipelines", {
        body: {
          name: `${pipelineName(run)}-next`,
          next_round: { run: run.id, carry_decisions: carryDecisions, add_proposals: addProposals, drop },
        },
      });
      if (!made.response.ok) {
        throw new ApiError(
          made.response.status,
          describeReason(made.response.status, made.response.statusText, made.error),
        );
      }
      return made.data!;
    },
    onSuccess: (doc) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      navigate(`/pipelines/${doc.name}?step=ligands`);
    },
  });
}
