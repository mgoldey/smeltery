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
        {detail.data ? <PoseExplorer idx={idx} detail={detail.data} /> : null}
      </main>
    </div>
  );
}
