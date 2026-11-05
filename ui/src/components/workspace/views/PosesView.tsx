import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, unwrap, type Schemas } from "../../../api/client";
import { ApiError, reasonOf } from "../../../api/http";
import { MolstarViewer } from "../../MolstarViewer";
import type { StepViewProps } from "./registry";

/** GET a Bench route this lane added; typed locally until the API schema is regenerated. */
export async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new ApiError(res.status, await reasonOf(res));
  return (await res.json()) as T;
}

export function useRunDetail(idx: number | null) {
  return useQuery({
    queryKey: ["run", idx],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}", { params: { path: { idx: idx ?? 0 } } })),
    enabled: idx !== null,
  });
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
export function usePoseMaps(idx: number) {
  return useQuery({
    queryKey: ["pose_maps", idx],
    queryFn: () => getJson<{ maps: Record<string, number[][]> }>(`/api/runs/${idx}/pose_maps`),
  });
}

/** One ligand's pose in the site, its shared core drawn solid and the other atoms as lines. */
export function CorePose({ idx, candidate, pose, label }: { idx: number; candidate: string; pose: number; label: string }) {
  const scene = useQuery({
    queryKey: ["scene-core", idx, candidate, pose],
    queryFn: () =>
      getJson<Record<string, unknown>>(`/api/runs/${idx}/scene?candidate=${encodeURIComponent(candidate)}&pose=${pose}&core=true`),
  });
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
  return `${n} atoms of ${candidate} are copied exactly from the parent's pose and drawn solid; the other atoms are outlined as lines. The parent's pose is shown muted.`;
}

/** Step 3: the poses, one ligand at a time. Needs a run record, not an SCF. */
export function PosesView({ sourceRun }: StepViewProps) {
  const detail = useRunDetail(sourceRun);
  const maps = usePoseMaps(sourceRun ?? 0);
  const [candidate, setCandidate] = useState<string | null>(null);
  const [pose, setPose] = useState(0);
  if (sourceRun === null) return <NotRun step="poses" shows="each ligand's poses in the site, the atoms shared with the parent solid" />;
  if (detail.isError) return <div className="banner" role="alert">{detail.error.message}</div>;
  const d: Schemas["RunDetail"] | undefined = detail.data;
  if (!d) return <p className="lede">Loading the run's poses.</p>;
  if (!d.has_pose_output) {
    return (
      <div className="banner" role="alert">
        This run record predates pose output, so it has no poses to show. Re-run it with this version of smeltery to see them.
      </div>
    );
  }
  const ligands = [...d.analogues, d.parent];
  const chosen = candidate ?? d.analogues[0] ?? d.parent;
  const n = d.n_poses[chosen] ?? 0;
  const core = new Set((maps.data?.maps[chosen] ?? []).map((p) => p[0])).size;
  return (
    <div data-view="poses">
      <div className="controls">
        <div>
          <label htmlFor="poses-ligand">Ligand</label>
          <select
            id="poses-ligand"
            value={chosen}
            onChange={(e) => {
              setCandidate(e.target.value);
              setPose(0);
            }}
          >
            {ligands.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="poses-pose">Pose</label>
          <select id="poses-pose" value={pose} onChange={(e) => setPose(Number(e.target.value))}>
            {Array.from({ length: n }, (_, i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="pairing" data-testid="core-caption" data-core={core}>
        {coreCaption(core, chosen, d.parent)}
      </p>
      <CorePose idx={sourceRun} candidate={chosen} pose={pose} label="poses step" />
    </div>
  );
}
