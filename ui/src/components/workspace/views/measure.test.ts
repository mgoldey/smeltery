import { describe, expect, it } from "vitest";
import { bindClicks, type PlotDiv, type Sensitivity, sensitivityFailures, sensitivityRows } from "./MeasureView";

/** A model's entry with the response's defaults filled in. */
const ms = (m: Partial<Sensitivity>): Sensitivity => ({
  paired_ddE: {},
  sign_flips: null,
  spearman: null,
  n_dropped: 0,
  dropped_poses: {},
  parent_dropped_poses: [],
  excluded: [],
  failures: {},
  ...m,
});

/** A stand-in for the div Plotly draws into: `.on` records its listeners. */
function plot(): PlotDiv & { fire: (e: unknown) => void } {
  const listeners: ((e: never) => void)[] = [];
  const el = document.createElement("div") as unknown as PlotDiv & { fire: (e: unknown) => void };
  el.setAttribute("data-figure", "per_pose_values");
  el.on = (_name, fn) => listeners.push(fn as (e: never) => void);
  el.fire = (e) => listeners.forEach((f) => f(e as never));
  return el;
}

describe("bindClicks", () => {
  it("binds the plot once, and again to the new element after the plot is replaced", () => {
    const host = document.createElement("div");
    const picks: unknown[] = [];
    const state = { el: null };
    const first = plot();
    host.append(first);
    bindClicks(host, state, (p) => picks.push(p));
    bindClicks(host, state, (p) => picks.push(p)); // the next tick: no second listener
    first.fire({ points: [{ customdata: ["4-F", 3, -1, 2] }] });
    expect(picks).toEqual([{ candidate: "4-F", pose: 3 }]);

    first.remove(); // a theme switch unmounts the plot and draws it on a new div
    const second = plot();
    host.append(second);
    bindClicks(host, state, (p) => picks.push(p));
    second.fire({ points: [{ customdata: ["benzoic", 0, -1, null] }] });
    expect(picks).toEqual([
      { candidate: "4-F", pose: 3 },
      { candidate: "benzoic", pose: 0 },
    ]);
  });
  it("waits while Plotly has not yet attached .on", () => {
    const host = document.createElement("div");
    const bare = document.createElement("div");
    bare.setAttribute("data-figure", "x");
    host.append(bare);
    const state = { el: null };
    bindClicks(host, state, () => {});
    expect(state.el).toBeNull();
  });
});

describe("sensitivityRows", () => {
  it("is one row per charge model and analogue, and nothing when the analysis did not run", () => {
    const models = {
      CHARMM: ms({
        paired_ddE: { "4-F": { primary: -1, model: -1.5, delta: -0.5, n: 3 }, "4-Br": { primary: null, model: null, delta: null, n: 0 } },
        sign_flips: 0,
        spearman: 1,
        n_dropped: 3,
        dropped_poses: { "4-Br": [0, 1, 2] },
      }),
      PARSE: ms({ paired_ddE: { "4-F": { primary: -1, model: 0.5, delta: 1.5, n: 3 } }, sign_flips: null, spearman: null, n_dropped: 0, dropped_poses: {}, note: "1 analogue(s)" }),
    };
    expect(sensitivityRows(models)).toEqual([
      { chargeModel: "CHARMM", candidate: "4-F", primary: -1, model: -1.5, delta: -0.5, n: 3 },
      { chargeModel: "CHARMM", candidate: "4-Br", primary: null, model: null, delta: null, n: 0 },
      { chargeModel: "PARSE", candidate: "4-F", primary: -1, model: 0.5, delta: 1.5, n: 3 },
    ]);
    expect(sensitivityRows(null)).toEqual([]);
    expect(sensitivityRows(undefined)).toEqual([]);
  });
});

describe("sensitivityFailures", () => {
  it("lists every failed pose per model in pose order, and skips a model that failed as a whole", () => {
    const models = {
      CHARMM: ms({
        paired_ddE: {},
        sign_flips: null,
        spearman: null,
        n_dropped: 2,
        dropped_poses: { "4-Cl": [0, 2] },
        failures: { "4-Cl": { "2": "RuntimeError: b", "0": "RuntimeError: a" } },
      }),
      PARSE: ms({ error: "CalledProcessError: x", stage: "field" }),
    };
    expect(sensitivityFailures(models)).toEqual([
      { chargeModel: "CHARMM", candidate: "4-Cl", pose: 0, error: "RuntimeError: a" },
      { chargeModel: "CHARMM", candidate: "4-Cl", pose: 2, error: "RuntimeError: b" },
    ]);
    expect(sensitivityRows(models)).toEqual([]);
  });
});
