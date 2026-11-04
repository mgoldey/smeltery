import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api, unwrap, type Schemas } from "../api/client";
import { MolstarViewer } from "./MolstarViewer";

/** A candidate's pose in the site, with selectors, the pairing stated, and an optional per-pose footer. */
export function PoseExplorer({
  idx,
  detail,
  label = "pose viewer",
  footer,
}: {
  idx: number;
  detail: Schemas["RunDetail"];
  label?: string;
  footer?: (candidate: string, pose: number) => ReactNode;
}) {
  const [candidate, setCandidate] = useState<string | null>(null);
  const [pose, setPose] = useState(0);
  const chosen = candidate ?? detail.analogues[0] ?? null;
  const nPoses = chosen ? (detail.n_poses[chosen] ?? 0) : 0;
  const scene = useQuery({
    queryKey: ["scene", idx, chosen, pose],
    queryFn: () => unwrap(api.GET("/api/runs/{idx}/scene", { params: { path: { idx }, query: { candidate: chosen ?? "", pose } } })),
    enabled: Boolean(detail.has_pose_output && chosen),
  });
  if (!detail.has_pose_output) {
    return (
      <div className="banner" role="alert">
        This run record predates pose output, so it has no poses to show. Re-run it with this version of smeltery to see
        them.
      </div>
    );
  }
  if (!chosen) return null;
  return (
    <>
      <div className="controls">
        <div>
          <label htmlFor={`${label}-candidate`}>Candidate</label>
          <select
            id={`${label}-candidate`}
            value={chosen}
            onChange={(e) => {
              setCandidate(e.target.value);
              setPose(0);
            }}
          >
            {detail.analogues.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${label}-pose`}>Pose</label>
          <select id={`${label}-pose`} value={pose} onChange={(e) => setPose(Number(e.target.value))}>
            {Array.from({ length: nPoses }, (_, i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
        </div>
      </div>
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
      {footer ? footer(chosen, pose) : null}
    </>
  );
}
