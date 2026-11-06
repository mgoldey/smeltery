import { Navigate, Route, Routes } from "react-router-dom";
import { BenchHome } from "./pages/BenchHome";
import { Compare } from "./pages/Compare";
import { Methods } from "./pages/Methods";
import { NotFound } from "./pages/NotFound";
import { Notebook } from "./pages/Notebook";
import { Report } from "./pages/Report";
import { Decisions } from "./pages/run/Decisions";
import { Proposals } from "./pages/run/Proposals";
import { Study } from "./pages/Study";
import { Overview } from "./pages/run/Overview";
import { RunPipeline, Workspace } from "./pages/run/Workspace";
import { Poses } from "./pages/run/Poses";
import { Results } from "./pages/run/Results";
import { Structure } from "./pages/run/Structure";
import { RunLayout } from "./pages/run/RunLayout";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/bench" replace />} />
      <Route path="/bench" element={<BenchHome />} />
      <Route path="/studies/:module" element={<Study />} />
      <Route path="/report/:idx" element={<Report />} />
      <Route path="/campaigns/:id" element={<Notebook />} />
      <Route path="/compare" element={<Compare />} />
      <Route path="/methods" element={<Methods />} />
      <Route path="/pipelines/:name" element={<Workspace />} />
      <Route path="/run/:idx" element={<RunLayout />}>
        <Route index element={<Navigate to="results" replace />} />
        <Route path="results" element={<Results />} />
        <Route path="poses" element={<Poses />} />
        <Route path="structure" element={<Structure />} />
        <Route path="pipeline" element={<RunPipeline />} />
        <Route path="decisions" element={<Decisions />} />
        <Route path="proposals" element={<Proposals />} />
        <Route path="overview" element={<Overview />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
