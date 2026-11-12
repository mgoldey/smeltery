import { Suspense, lazy } from "react";
import { NeedsWrite } from "./components/ReadOnly";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { BenchHome } from "./pages/BenchHome";
import { NotFound } from "./pages/NotFound";
import { RunLayout } from "./pages/run/RunLayout";

// Each page loads its own chunk when its route opens, so the first page pays for the shell and the page it shows.
const Compare = lazy(() =>
  import("./pages/Compare").then((m) => ({ default: m.Compare })),
);
const Methods = lazy(() =>
  import("./pages/Methods").then((m) => ({ default: m.Methods })),
);
const Notebook = lazy(() =>
  import("./pages/Notebook").then((m) => ({ default: m.Notebook })),
);
const Report = lazy(() =>
  import("./pages/Report").then((m) => ({ default: m.Report })),
);
const Decisions = lazy(() =>
  import("./pages/run/Decisions").then((m) => ({ default: m.Decisions })),
);
const Proposals = lazy(() =>
  import("./pages/run/Proposals").then((m) => ({ default: m.Proposals })),
);
const Study = lazy(() =>
  import("./pages/Study").then((m) => ({ default: m.Study })),
);
const Overview = lazy(() =>
  import("./pages/run/Overview").then((m) => ({ default: m.Overview })),
);
const Poses = lazy(() =>
  import("./pages/run/Poses").then((m) => ({ default: m.Poses })),
);
const Results = lazy(() =>
  import("./pages/run/Results").then((m) => ({ default: m.Results })),
);
const Structure = lazy(() =>
  import("./pages/run/Structure").then((m) => ({ default: m.Structure })),
);
const Workspace = lazy(() =>
  import("./pages/run/Workspace").then((m) => ({ default: m.Workspace })),
);
const RunPipeline = lazy(() =>
  import("./pages/run/Workspace").then((m) => ({ default: m.RunPipeline })),
);

export function App() {
  const { pathname } = useLocation();
  return (
    <ErrorBoundary resetKey={pathname}>
      <Suspense fallback={<p className="lede">Loading the page.</p>}>
        <Routes>
          <Route path="/" element={<Navigate to="/bench" replace />} />
          <Route path="/bench" element={<BenchHome />} />
          <Route path="/studies/:module" element={<Study />} />
          <Route path="/report/:runId" element={<Report />} />
          <Route path="/campaigns/:id" element={<Notebook />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/methods" element={<Methods />} />
          <Route
            path="/pipelines/:name"
            element={
              <NeedsWrite what="Pipeline editor">
                <Workspace />
              </NeedsWrite>
            }
          />
          <Route path="/run/:runId" element={<RunLayout />}>
            <Route index element={<Navigate to="results" replace />} />
            <Route path="results" element={<Results />} />
            <Route path="poses" element={<Poses />} />
            <Route
              path="structure"
              element={
                <NeedsWrite what="Structure">
                  <Structure />
                </NeedsWrite>
              }
            />
            <Route path="pipeline" element={<RunPipeline />} />
            <Route
              path="decisions"
              element={
                <NeedsWrite what="Decisions">
                  <Decisions />
                </NeedsWrite>
              }
            />
            <Route path="proposals" element={<Proposals />} />
            <Route path="overview" element={<Overview />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
