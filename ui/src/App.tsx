import { Navigate, Route, Routes } from "react-router-dom";
import { BenchHome } from "./pages/BenchHome";
import { NotFound } from "./pages/NotFound";
import { Overview } from "./pages/run/Overview";
import { RunLayout } from "./pages/run/RunLayout";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/bench" replace />} />
      <Route path="/bench" element={<BenchHome />} />
      <Route path="/run/:idx" element={<RunLayout />}>
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="overview" element={<Overview />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
