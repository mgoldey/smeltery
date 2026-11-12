import type { ReactElement } from "react";
import type { Schemas } from "../../../api/client";
import { ActiveSiteView } from "./ActiveSiteView";
import { ChecksView } from "./ChecksView";
import { CutView } from "./CutView";
import { LigandsView } from "./LigandsView";
import { MeasureView } from "./MeasureView";
import { PosesView } from "./PosesView";

type Step = Schemas["StepOut"];

/** What every step view receives: the pipeline name, its current draft TOML, the step, the run it was made from, and `onToml` to replace the workspace draft. */
export type StepViewProps = { pipeline: string; toml: string; step: Step; sourceRun: string | null; onToml: (text: string) => void };

/** One component per step key, each in its own file, so the tasks that build them never edit the same file. */
export const STEP_VIEWS: Partial<Record<string, (props: StepViewProps) => ReactElement>> = {
  active_site: ActiveSiteView,
  ligands: LigandsView,
  poses: PosesView,
  checks: ChecksView,
  measure: MeasureView,
  cut: CutView,
};
