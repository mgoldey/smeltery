import { Navigate, Route, Routes } from "react-router-dom";
import { BenchHome } from "./pages/BenchHome";
import { NotFound } from "./pages/NotFound";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/bench" replace />} />
      <Route path="/bench" element={<BenchHome />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
