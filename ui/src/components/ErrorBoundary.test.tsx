import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): never {
  throw new Error("molstar exploded");
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders its children when nothing throws", () => {
    render(
      <MemoryRouter>
        <ErrorBoundary>
          <p>fine</p>
        </ErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("fine")).toBeTruthy();
    expect(screen.queryByTestId("error-boundary")).toBeNull();
  });

  it("shows the message, Reload and Back to runs when a child throws", () => {
    render(
      <MemoryRouter>
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert").textContent).toContain("molstar exploded");
    expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to runs" }).getAttribute("href")).toBe("/bench");
  });

  it("recovers when the reset key changes", () => {
    const ui = (key: string, boom: boolean) => (
      <MemoryRouter>
        <ErrorBoundary resetKey={key}>{boom ? <Boom /> : <p>recovered</p>}</ErrorBoundary>
      </MemoryRouter>
    );
    const { rerender } = render(ui("/a", true));
    expect(screen.getByTestId("error-boundary")).toBeTruthy();
    rerender(ui("/b", false));
    expect(screen.getByText("recovered")).toBeTruthy();
  });

  it("Reload reloads the page", () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(
      <MemoryRouter>
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
