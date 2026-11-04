import { Navigate, Route, Routes } from "react-router-dom";
import { BenchHome } from "./pages/BenchHome";
import { NotFound } from "./pages/NotFound";
import { Overview } from "./pages/run/Overview";
import { Results } from "./pages/run/Results";
import { RunLayout } from "./pages/run/RunLayout";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/bench" replace />} />
      <Route path="/bench" element={<BenchHome />} />
      <Route path="/run/:idx" element={<RunLayout />}>
        <Route index element={<Navigate to="results" replace />} />
        <Route path="results" element={<Results />} />
        <Route path="overview" element={<Overview />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
