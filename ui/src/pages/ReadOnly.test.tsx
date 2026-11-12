import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NeedsWrite } from "../components/ReadOnly";
import { BenchHome } from "./BenchHome";
import { Methods } from "./Methods";

let readOnly = false;

/** Every GET the pages make is answered here; the real hooks, `unwrap` and components run unchanged. */
vi.mock("../api/client", async (orig) => {
  const real = await orig<typeof import("../api/client")>();
  const GET = async (path: string) => {
    const data =
      path === "/api/whoami"
        ? { user: "mg", root: "/r", read_only: readOnly }
        : path === "/api/methods"
          ? [
              {
                key: "rhf", label: "RHF", summary: "", quantity: null, cost_model: "", memory_model: "", floor: null,
                floor_status: "", tier_doc: null, used_by: [],
                params: [{ name: "basis", kind: "text", default: "sto-3g", workspace_default: null, required: false, help: "", choices: [] }],
              },
            ]
          : [];
    return { data, response: new Response(null, { status: 200 }) };
  };
  return { ...real, api: { GET } };
});

function serve(ro: boolean) {
  readOnly = ro;
}

function renderAt(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Routes>
          <Route path="*" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
});

describe("a read-only server", () => {
  it("shows the badge and none of the controls that start, import or write", async () => {
    serve(true);
    renderAt(<BenchHome />);
    expect(await screen.findByTestId("read-only-badge")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Runs" })).toBeTruthy(); // read views stay
    expect(screen.queryByRole("heading", { name: "Start here" })).toBeNull();
    expect(screen.queryByRole("button", { name: /new pipeline|start notebook|run pipeline/i })).toBeNull();
    expect(screen.queryByLabelText(/import/i)).toBeNull();
  });

  it("a writable server shows them, and no badge", async () => {
    serve(false);
    renderAt(<BenchHome />);
    expect(await screen.findByRole("heading", { name: "Start here" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start notebook" })).toBeTruthy();
    expect(screen.queryByTestId("read-only-badge")).toBeNull();
  });

  it("replaces a page that only edits with a sentence, and keeps the editor on a writable server", async () => {
    serve(true);
    renderAt(
      <NeedsWrite what="Pipeline editor">
        <button>Run pipeline</button>
      </NeedsWrite>,
    );
    expect(await screen.findByTestId("read-only-page")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run pipeline" })).toBeNull();
    cleanup();
    serve(false);
    renderAt(
      <NeedsWrite what="Pipeline editor">
        <button>Run pipeline</button>
      </NeedsWrite>,
    );
    expect(screen.getByRole("button", { name: "Run pipeline" })).toBeTruthy();
  });

  it("makes method defaults read-only: inputs disabled, no Save", async () => {
    serve(true);
    renderAt(<Methods />);
    const input = await screen.findByLabelText("rhf basis workspace default");
    expect((input as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /save defaults/i })).toBeNull();
  });
});
