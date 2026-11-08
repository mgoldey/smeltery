import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("shows the sentence alone when there is no action", () => {
    render(<EmptyState>Nothing here.</EmptyState>);
    expect(screen.getByTestId("empty-state").textContent).toBe("Nothing here.");
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers a router link when the action has a target", () => {
    render(
      <MemoryRouter>
        <EmptyState action={{ label: "Go home", to: "/bench" }}>
          Nothing here.
        </EmptyState>
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("link", { name: "Go home" }).getAttribute("href"),
    ).toBe("/bench");
  });

  it("offers a button that calls the handler", () => {
    const onClick = vi.fn();
    render(
      <EmptyState action={{ label: "Write one", onClick }}>
        Nothing here.
      </EmptyState>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Write one" }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
