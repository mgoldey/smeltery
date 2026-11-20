import { useState, type ReactNode } from "react";
import type { Schemas } from "../api/client";
import { MolstarViewer } from "./MolstarViewer";
import { NoPoseOutput, PosePicker, useScene } from "./PosePicker";
import { ScfLog } from "./ScfLog";

/** A candidate's pose in the site, with selectors, the pairing stated, and an optional per-pose footer. */
export function PoseExplorer({
  runId,
  detail,
  label = "pose viewer",
  footer,
}: {
  runId: string;
  detail: Schemas["RunDetail"];
  label?: string;
  footer?: (candidate: string, pose: number) => ReactNode;
}) {
  const [candidate, setCandidate] = useState<string | null>(null);
  const [pose, setPose] = useState(0);
  const [showLog, setShowLog] = useState(false);
  const chosen = candidate ?? detail.analogues[0] ?? null;
  const nPoses = chosen ? (detail.n_poses[chosen] ?? 0) : 0;
  const scene = useScene(runId, chosen ?? "", pose, { enabled: Boolean(detail.has_pose_output && chosen) });
  if (!detail.has_pose_output) return <NoPoseOutput />;
  if (!chosen) return null;
  return (
    <>
      <PosePicker
        candidates={detail.analogues}
        candidate={chosen}
        pose={pose}
        nPoses={nPoses}
        onCandidate={(c) => {
          setCandidate(c);
          setPose(0);
          setShowLog(false);
        }}
        onPose={(p) => {
          setPose(p);
          setShowLog(false);
        }}
        candidateLabel="Candidate"
        ids={{ candidate: `${label}-candidate`, pose: `${label}-pose` }}
      />
      <p className="pairing" data-testid="pairing">
        Pose {pose} of {chosen} is paired to pose {pose} of {detail.parent}, shown muted. Charges are drawn as spheres:
        positive and negative by colour.
      </p>
      {scene.isError ? (
        <div className="banner" role="alert">
          {scene.error.message}
        </div>
      ) : null}
      <MolstarViewer state={scene.data} label={label} />
      <p>
        <button type="button" aria-pressed={showLog} onClick={() => setShowLog((v) => !v)}>
          SCF log
        </button>
      </p>
      {showLog ? <ScfLog runId={runId} candidate={chosen} position={pose} summary /> : null}
      {footer ? footer(chosen, pose) : null}
    </>
  );
}
