import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * A write control must not appear before `/api/whoami` answers.
 *
 * `useCapabilities` cannot distinguish "writable" from "not asked yet" with a single boolean, and the first
 * version defaulted to writable: a read-only Bench rendered Run/Edit for one paint, and a viewer who clicked
 * in that window got a 403 from a button that then vanished. These tests hold the query open deliberately --
 * the mock below never resolves until told -- which the other read-only tests cannot do, because their mock
 * resolves immediately.
 */

let resolveWhoami: ((v: { read_only: boolean }) => void) | null = null;

vi.mock("./client", async (orig) => {
  const real = await orig<typeof import("./client")>();
  const GET = (path: string) => {
    if (path !== "/api/whoami") return Promise.resolve({ data: [], response: new Response(null, { status: 200 }) });
    return new Promise((res) => {
      resolveWhoami = (v) => res({ data: { user: "mg", root: "/r", ...v }, response: new Response(null, { status: 200 }) });
    });
  };
  return { ...real, api: { GET } };
});

const { Writable } = await import("../components/ReadOnly");

function renderWritable(child: ReactNode = <button type="button">Run</button>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Writable>{child}</Writable>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  resolveWhoami = null;
});

describe("a write control and the unknown-capability window", () => {
  it("is absent while /api/whoami is still in flight", () => {
    renderWritable();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("appears once the server answers that writes are allowed", async () => {
    renderWritable();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    resolveWhoami?.({ read_only: false });
    expect(await screen.findByRole("button", { name: "Run" })).toBeTruthy();
  });

  it("never appears on a read-only server, so it cannot flash in and out", async () => {
    renderWritable();
    resolveWhoami?.({ read_only: true });
    // Let the query settle, then confirm the control stayed away the whole time.
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });
});
