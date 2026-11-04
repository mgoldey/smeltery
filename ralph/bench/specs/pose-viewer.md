# Pose viewer

**A candidate's pose is shown in the site beside its paired parent pose.**

## Requirements

1. `GET /api/runs/{idx}/scene?candidate=&pose=` returns `smeltery.gui.scenes.pose_scene(run,
   candidate, pose)` unchanged, as JSON.
2. The page states that analogue pose *i* is paired to parent pose *i*.
3. A record without pose output is 409 with the reason (the `has_pose_output` convention), never a
   blank viewer.
4. A pose index out of range is 400.

## In the app (Run → Poses)
5. A Mol* viewer renders the scene from the API: receptor, candidate pose, the muted parent pose,
   and the charge spheres.
6. Candidate and pose selectors re-request the scene. The page states "pose i of <candidate> is
   paired to pose i of <parent>".
7. Without pose output, the panel shows the API's reason instead of an empty canvas.
