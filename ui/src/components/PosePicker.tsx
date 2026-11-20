import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";

/**
 * A pose's scene in the site, as `/scene` builds it. `core` draws the atoms an analogue shares with the parent solid
 * and the rest as lines; without it the pose is drawn whole beside the parent's. The two are different scenes, so
 * they are cached under different keys.
 */
export function useScene(runId: string, candidate: string, pose: number, { core = false, enabled = true } = {}) {
  return useQuery({
    queryKey: core ? ["scene", runId, candidate, pose, "core"] : ["scene", runId, candidate, pose],
    queryFn: () =>
      unwrap(
        api.GET("/api/runs/{run_id}/scene", {
          params: { path: { run_id: runId }, query: core ? { candidate, pose, core: true } : { candidate, pose } },
        }),
      ),
    enabled,
  });
}

/** What a pose view says for a run whose record has no pose output to draw. */
export function NoPoseOutput() {
  return (
    <div className="banner" role="alert">
      This run record predates pose output, so it has no poses to show. Re-run it with this version of smeltery to see
      them.
    </div>
  );
}

/**
 * The two selects every pose view opens with: which ligand, then which of its poses. Each caller keeps its own label
 * text and element ids, which tests and the pages' own labels address.
 */
export function PosePicker({
  candidates,
  candidate,
  pose,
  nPoses,
  onCandidate,
  onPose,
  candidateLabel,
  ids,
}: {
  candidates: string[];
  candidate: string;
  pose: number;
  nPoses: number;
  onCandidate: (c: string) => void;
  onPose: (p: number) => void;
  candidateLabel: string;
  ids: { candidate: string; pose: string };
}) {
  return (
    <div className="controls">
      <div>
        <label htmlFor={ids.candidate}>{candidateLabel}</label>
        <select id={ids.candidate} value={candidate} onChange={(e) => onCandidate(e.target.value)}>
          {candidates.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor={ids.pose}>Pose</label>
        <select id={ids.pose} value={pose} onChange={(e) => onPose(Number(e.target.value))}>
          {Array.from({ length: nPoses }, (_, i) => (
            <option key={i} value={i}>
              {i}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
