import { useQuery } from "@tanstack/react-query";
import { PoseExplorer } from "../../components/PoseExplorer";
import { useRunId } from "./RunLayout";
import { runQuery } from "../../api/queries";

export function Poses() {
  const runId = useRunId();
  const detail = useQuery(runQuery(runId));
  return (
    <div className="body">
      <main className="work">
        <h1>Poses</h1>
        {detail.isError ? (
          <div className="banner" role="alert">
            {detail.error.message}
          </div>
        ) : null}
        <p>
          <a href={`/api/runs/${runId}/pymol.zip`} download>
            Download PyMOL session
          </a>{" "}
          (receptor, every candidate's poses and a session.pml; unzip, then open session.pml in a fresh PyMOL: <code>pymol session.pml</code>; it replaces any open session)
        </p>
        {/* The whole run directory, which is the unit of sharing. It was `smeltery golden bundle` in a shell
            until now -- no use to someone reading a Bench they did not start. */}
        <p>
          <a href={`/api/runs/${runId}/bundle.zip`} download data-testid="download-bundle">
            Download the whole run
          </a>{" "}
          (the record, poses, receptor, decisions and any report, as one zip for an ELN or a colleague)
        </p>
        {detail.data ? <PoseExplorer runId={runId} detail={detail.data} /> : null}
      </main>
    </div>
  );
}
