import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { PoseExplorer } from "../../components/PoseExplorer";
import { useRunIdx } from "./RunLayout";

export function Poses() {
  const idx = useRunIdx();
  const detail = useQuery({ queryKey: ["run", idx], queryFn: () => unwrap(api.GET("/api/runs/{idx}", { params: { path: { idx } } })) });
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
          <a href={`/api/runs/${idx}/pymol.zip`} download>
            Download PyMOL session
          </a>{" "}
          (receptor, every candidate's poses and a session.pml; unzip, then open session.pml in a fresh PyMOL: <code>pymol session.pml</code>; it replaces any open session)
        </p>
        {detail.data ? <PoseExplorer idx={idx} detail={detail.data} /> : null}
      </main>
    </div>
  );
}
