import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../../../api/client";
import { MolstarViewer } from "../../MolstarViewer";
import { NoPoseOutput, PosePicker, useScene } from "../../PosePicker";
import type { StepViewProps } from "./registry";
import { runQuery } from "../../../api/queries";

export function useRunDetail(runId: string | null) {
  return useQuery({ ...runQuery(runId), enabled: runId !== null });
}

/** What a step shows before any run has made what it would show. */
export function NotRun({ step, shows }: { step: string; shows: string }) {
  return (
    <p className="lede" data-testid="not-run">
      The {step} step has not run. Open this pipeline from a run (or run it) and this view will show {shows}.
    </p>
  );
}

/** The pairs of a run's `results.pose_maps`, read once per run. */
export function usePoseMaps(runId: string | null) {
  return useQuery({
    queryKey: ["pose_maps", runId],
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/pose_maps", { params: { path: { run_id: runId ?? "" } } })),
    enabled: runId !== null,
  });
}

/** One ligand's pose in the site, its shared core drawn solid and the other atoms as lines. */
export function CorePose({ runId, candidate, pose, label }: { runId: string; candidate: string; pose: number; label: string }) {
  const scene = useScene(runId, candidate, pose, { core: true });
  return (
    <>
      {scene.isError ? (
        <div className="banner" role="alert">
          {scene.error.message}
        </div>
      ) : null}
      <MolstarViewer state={scene.data} label={label} />
    </>
  );
}

export function coreCaption(n: number | undefined, candidate: string, parent: string): string {
  if (candidate === parent) return `${parent} is the parent: every atom is drawn solid.`;
  if (!n) return "This run did not record which atoms the analogue shares with the parent, so every atom is drawn solid.";
  return `${n} atoms of ${candidate} are copied exactly from the parent's pose and drawn solid; the other atoms are small and translucent. The parent's pose is shown muted.`;
}

/** Step 3: the poses, one ligand at a time. Needs a run record, not an SCF. */
export function PosesView({ sourceRun }: StepViewProps) {
  const detail = useRunDetail(sourceRun);
  const maps = usePoseMaps(sourceRun);
  const [candidate, setCandidate] = useState<string | null>(null);
  const [pose, setPose] = useState(0);
  if (sourceRun === null) return <NotRun step="poses" shows="each ligand's poses in the site, the atoms shared with the parent solid" />;
  if (detail.isError) return <div className="banner" role="alert">{detail.error.message}</div>;
  const d: Schemas["RunDetail"] | undefined = detail.data;
  if (!d) return <p className="lede">Loading the run's poses.</p>;
  if (!d.has_pose_output) return <NoPoseOutput />;
  const ligands = [...d.analogues, d.parent];
  const chosen = candidate ?? d.analogues[0] ?? d.parent;
  const n = d.n_poses[chosen] ?? 0;
  const core = new Set((maps.data?.maps[chosen] ?? []).map((p) => p[0])).size;
  return (
    <div data-view="poses">
      <PosePicker
        candidates={ligands}
        candidate={chosen}
        pose={pose}
        nPoses={n}
        onCandidate={(c) => {
          setCandidate(c);
          setPose(0);
        }}
        onPose={setPose}
        candidateLabel="Ligand"
        ids={{ candidate: "poses-ligand", pose: "poses-pose" }}
      />
      <p className="pairing" data-testid="core-caption" data-core={core}>
        {coreCaption(core, chosen, d.parent)}
      </p>
      <CorePose runId={sourceRun} candidate={chosen} pose={pose} label="poses step" />
    </div>
  );
}
