import { NeedsWrite } from "./components/ReadOnly";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
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
  const { pathname } = useLocation();
  return (
    <ErrorBoundary resetKey={pathname}>
      <Routes>
        <Route path="/" element={<Navigate to="/bench" replace />} />
        <Route path="/bench" element={<BenchHome />} />
        <Route path="/studies/:module" element={<Study />} />
        <Route path="/report/:runId" element={<Report />} />
        <Route path="/campaigns/:id" element={<Notebook />} />
        <Route path="/compare" element={<Compare />} />
        <Route path="/methods" element={<Methods />} />
        <Route path="/pipelines/:name" element={<NeedsWrite what="Pipeline editor"><Workspace /></NeedsWrite>} />
        <Route path="/run/:runId" element={<RunLayout />}>
          <Route index element={<Navigate to="results" replace />} />
          <Route path="results" element={<Results />} />
          <Route path="poses" element={<Poses />} />
          <Route path="structure" element={<NeedsWrite what="Structure"><Structure /></NeedsWrite>} />
          <Route path="pipeline" element={<RunPipeline />} />
          <Route path="decisions" element={<NeedsWrite what="Decisions"><Decisions /></NeedsWrite>} />
          <Route path="proposals" element={<Proposals />} />
          <Route path="overview" element={<Overview />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </ErrorBoundary>
  );
}
