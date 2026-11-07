import type { components } from "../api/schema";

export type DroppedPose = components["schemas"]["DroppedPose"];

/** One line per dropped leg: which pose, which leg, why, the rungs tried and whose ddE lost it. */
export function dropLine(d: DroppedPose): string {
  const leg = d.field_model ? `${d.leg} leg, ${d.field_model}` : `${d.leg} leg`;
  const rungs = d.rungs_tried?.length ? `; rungs tried ${d.rungs_tried.join(", ")}` : "";
  const lost = d.dropped_with?.length ? `; dropped from ${d.dropped_with.join(", ")}` : "";
  return `${d.candidate} pose ${d.pose} (${leg}): ${d.reason ?? d.outcome ?? "no value"}${rungs}${lost}`;
}

/** `results.dropped_poses`: poses whose SCF failed every rung, dropped pairwise. Nothing when none was. */
export function DroppedPoses({ dropped }: { dropped: DroppedPose[] | undefined }) {
  if (!dropped?.length) return null;
  return (
    <section aria-label="Dropped poses" data-testid="dropped-poses">
      <h4>Dropped poses</h4>
      <p className="lede">
        These poses gave no value after every SCF remedy. Each is dropped from the ligand and from the poses it is paired with, so
        the ligands below have fewer points; the x axis keeps each pose's original index.
      </p>
      <ul>
        {dropped.map((d) => (
          <li key={`${d.candidate}/${d.pose}/${d.leg}/${d.field_model ?? ""}`}>{dropLine(d)}</li>
        ))}
      </ul>
    </section>
  );
}
