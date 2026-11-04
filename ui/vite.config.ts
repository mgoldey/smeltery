import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The app is served by the Bench API (`smeltery bench`), so it builds into the Python package. In dev, Vite proxies
// /api to a running `smeltery bench` on BENCH_API (default http://127.0.0.1:8765).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/smeltery/bench/web",
    emptyOutDir: true,
    chunkSizeWarningLimit: 8000,
  },
  server: { proxy: { "/api": process.env.BENCH_API ?? "http://127.0.0.1:8765" } },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
