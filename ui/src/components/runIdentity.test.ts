import { describe, expect, it } from "vitest";
import type { RunSummary } from "../api/client";
import {
  inOut,
  latestRunOfPipeline,
  outDir,
  outName,
  pipelineHref,
  pipelineOfRun,
  runDate,
  runLabel,
  runOfTask,
  taskOut,
} from "./runIdentity";

/** A run row with only the fields `/api/runs` actually returns. */
const row = (path: string, extra: Partial<RunSummary> = {}): RunSummary =>
  ({ id: `id-${path}`, path, campaign: "Danuglipron", digest: "abcdef0123456789", ...extra }) as RunSummary;

describe("outDir / outName", () => {
  it("is the record's directory, and the segment that names it", () => {
    expect(outDir("/w/out/dan-20261114-091500/run-abcdef012345.json")).toBe("/w/out/dan-20261114-091500");
    expect(outName("/w/out/dan-20261114-091500/run-abcdef012345.json")).toBe("dan-20261114-091500");
  });
  it("reaches past a directory that names no particular run", () => {
    // Five runs under <root>/<name>/out/ would otherwise all be labelled "out" -- the campaign's own problem,
    // one level down.
    expect(outName("/w/a_resolved/out/run-abc.json")).toBe("a_resolved/out");
    expect(outName("/w/b_unranked/out/run-abc.json")).toBe("b_unranked/out");
    expect(outName("/w/pipelines/p/run/run-abc.json")).toBe("p/run");
  });
  it("gives nothing rather than guessing for a bare filename", () => {
    expect(outDir("run-abc.json")).toBe("");
    expect(outName("run-abc.json")).toBe("");
  });
});

describe("pipelineOfRun", () => {
  it("names the pipeline a run inside a pipeline folder belongs to", () => {
    expect(pipelineOfRun("/w/pipelines/my-benzoic/run/run-abc.json")).toBe("my-benzoic");
    expect(pipelineOfRun("/w/pipelines/my-benzoic/out/round2/run-abc.json")).toBe("my-benzoic");
  });
  it("claims no pipeline for a run launched outside one, rather than guessing from the campaign", () => {
    // A run records no pipeline name by design, so out/ runs have none to show. Guessing would attach another
    // pipeline's steps to this run's numbers.
    expect(pipelineOfRun("/w/out/dan-20261114-091500/run-abc.json")).toBeNull();
    expect(pipelineOfRun("/w/pipelines-archive/thing/run-abc.json")).toBeNull();
  });
});

describe("runDate", () => {
  it("reads the date out of a directory Bench named itself", () => {
    expect(runDate("/w/out/dan-20261114-091500/run-abc.json")).toBe("2026-11-14");
  });
  it("gives none for a directory the user typed", () => {
    expect(runDate("/w/out/round-two/run-abc.json")).toBeNull();
    expect(runDate("/w/pipelines/p/run/run-abc.json")).toBeNull();
  });
});

describe("runLabel", () => {
  it("names the campaign, then which run of it, then its inputs", () => {
    expect(runLabel(row("/w/out/dan-20261114-091500/run-abc.json"))).toBe(
      "Danuglipron · dan-20261114-091500 · abcdef012345",
    );
  });
  it("tells two runs of one campaign with one digest apart", () => {
    // The defect: these two were "Danuglipron abcdef012345" and "Danuglipron abcdef012345".
    const a = runLabel(row("/w/out/dan-20261114-091500/run-abc.json"));
    const b = runLabel(row("/w/out/dan-20261115-174200/run-abc.json"));
    expect(a).not.toBe(b);
  });
});

describe("taskOut / inOut / runOfTask", () => {
  const command = ["smeltery", "run", "/w/out/r1/config.toml", "--out", "/w/out/r1"];
  it("reads the out directory off the command", () => {
    expect(taskOut(command)).toBe("/w/out/r1");
    expect(taskOut(["smeltery", "run", "x.toml"])).toBeUndefined();
  });
  it("matches only a record directly inside the out directory", () => {
    expect(inOut("/w/out/r1/run-abc.json", "/w/out/r1")).toBe(true);
    expect(inOut("/w/out/r1/run-abc.json", "/w/out/r1/")).toBe(true);
    // A run nested under a typed-in parent is not that parent's result.
    expect(inOut("/w/out/r1/deeper/run-abc.json", "/w/out/r1")).toBe(false);
    // A sibling whose name merely starts the same.
    expect(inOut("/w/out/r10/run-abc.json", "/w/out/r1")).toBe(false);
    expect(inOut("/w/out/r1/run-abc.json", undefined)).toBe(false);
  });
  it("finds the run a task produced, so the Work panel can link to it", () => {
    const runs = [row("/w/out/other/run-zzz.json"), row("/w/out/r1/run-abc.json")];
    expect(runOfTask(command, runs)?.path).toBe("/w/out/r1/run-abc.json");
    expect(runOfTask(command, [runs[0]!])).toBeUndefined(); // not written yet: nothing to link
    expect(runOfTask(["smeltery", "preview"], runs)).toBeUndefined();
  });
});

describe("latestRunOfPipeline / pipelineHref", () => {
  const mine = row("/w/pipelines/p/run/run-abc.json", { id: "r-old" });
  const newer = row("/w/pipelines/p/out/p-20261115-100000/run-abc.json", { id: "r-new" });
  const elsewhere = row("/w/out/p-20261115-110000/run-abc.json", { id: "r-other" });

  it("carries the pipeline's own newest run, so its step views have one to read", () => {
    // The defect: /pipelines/p with no ?run= makes Poses, Checks, dE_int and Cut all claim the step has not run.
    expect(latestRunOfPipeline("p", [mine, newer, elsewhere])?.id).toBe("r-new");
    expect(pipelineHref("p", [mine, newer, elsewhere])).toBe("/pipelines/p?run=r-new");
  });
  it("prefers a later round over a copied example's shipped run, whichever way the names sort", () => {
    // "run" sorts above "p-20261115-100000", so comparing the names would pin the pipeline to its oldest run
    // forever. Only a directory Bench dated carries an order to read.
    expect(latestRunOfPipeline("p", [mine, newer])?.id).toBe("r-new");
    const later = row("/w/pipelines/p/out/p-20261116-100000/run-abc.json", { id: "r-newest" });
    expect(latestRunOfPipeline("p", [newer, mine, later])?.id).toBe("r-newest");
  });
  it("links a pipeline with only a shipped run to that run", () => {
    expect(pipelineHref("p", [mine, elsewhere])).toBe("/pipelines/p?run=r-old");
  });
  it("links bare rather than to a run that is not this pipeline's", () => {
    expect(latestRunOfPipeline("p", [elsewhere])).toBeUndefined();
    expect(pipelineHref("p", [elsewhere])).toBe("/pipelines/p");
    expect(pipelineHref("p", undefined)).toBe("/pipelines/p");
  });
  it("skips a run whose record would not parse", () => {
    const dead = row("/w/pipelines/p/out/p-20261116-100000/run-abc.json", { id: "r-dead", error: "bad json" });
    expect(latestRunOfPipeline("p", [mine, dead])?.id).toBe("r-old");
  });
  it("escapes a name that would otherwise change the URL", () => {
    expect(pipelineHref("a b", [])).toBe("/pipelines/a%20b");
  });
});
