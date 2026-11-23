import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ResizableColumns } from "./ResizableColumns";
import { stepColumnDefaults } from "./workspace/StepPanel";

/**
 * The step panel gave its View column (the step's plot, table or 3D pose) a quarter of the width even with a run
 * attached. With a run the defaults now give View the largest share; without one Method keeps it; and a width the
 * user dragged to still wins over either.
 */

const KEY = "smeltery.cols.t";
const template = (c: HTMLElement) => (c.querySelector(".resizable-cols") as HTMLElement).style.gridTemplateColumns;

function Cols({ defaults }: { defaults: number[] }) {
  return (
    <ResizableColumns storageKey="t" defaults={defaults} minPx={100}>
      <section>a</section>
      <section>b</section>
      <section>c</section>
    </ResizableColumns>
  );
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("step column defaults", () => {
  it("give View the largest share with a run, and Method the largest without one", () => {
    const [, method, view] = stepColumnDefaults("run-1") as [number, number, number];
    expect(view).toBeGreaterThan(method);
    expect(Math.max(...stepColumnDefaults("run-1"))).toBe(view);
    const [, m0, v0] = stepColumnDefaults(null) as [number, number, number];
    expect(stepColumnDefaults(null)).toEqual([0.75, 1.5, 0.75]);
    expect(m0).toBeGreaterThan(v0);
  });
});

describe("ResizableColumns", () => {
  it("follows its defaults as they change while the user has chosen nothing", () => {
    const { container, rerender } = render(<Cols defaults={[1, 2, 1]} />);
    expect(template(container)).toContain("1fr");
    expect(template(container)).toContain("2fr");
    rerender(<Cols defaults={[1, 1, 3]} />);
    expect(template(container)).toContain("3fr");
  });

  it("lets a stored user choice win over the defaults", () => {
    localStorage.setItem(KEY, JSON.stringify([5, 1, 1]));
    const { container, rerender } = render(<Cols defaults={[1, 2, 1]} />);
    expect(template(container)).toContain("5fr");
    rerender(<Cols defaults={[1, 1, 3]} />);
    expect(template(container)).toContain("5fr");
    expect(template(container)).not.toContain("3fr");
  });

  it("forgets the choice on reset, so the current defaults apply again", () => {
    localStorage.setItem(KEY, JSON.stringify([5, 1, 1]));
    const { container } = render(<Cols defaults={[1, 1, 3]} />);
    fireEvent.doubleClick(screen.getAllByRole("separator", { name: "Resize columns" })[0]!);
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(template(container)).toContain("3fr");
  });
});
