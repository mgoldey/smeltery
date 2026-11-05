import type { Schemas } from "../../api/client";

type Step = Schemas["StepOut"];

const STATE_TEXT: Record<Step["state"], string> = {
  ready: "ready",
  needs_input: "needs input",
  off: "off · add",
  changed: "changed",
  stale: "result will change",
};

/** What a step's state says on the drawing; a step that needs input says what. */
export function stateText(s: Step): string {
  if (s.state === "needs_input") {
    const first = s.issues.find((i) => i.level === "error");
    return first ? `needs input: ${first.message}` : STATE_TEXT.needs_input;
  }
  return STATE_TEXT[s.state];
}

/** The pipeline drawn as its steps, in the order `smeltery run` executes them, each with its state. */
export function PipelineDrawing({ steps, selected, onSelect }: { steps: Step[]; selected: string; onSelect: (key: string) => void }) {
  return (
    <nav aria-label="Pipeline steps">
      <ol className="drawing">
        {steps.map((s, i) => (
          <li key={s.key}>
            <button
              type="button"
              data-step={s.key}
              data-state={s.state}
              aria-current={s.key === selected ? "step" : undefined}
              onClick={() => onSelect(s.key)}
              title={stateText(s)}
            >
              <span className="n">{i + 1}</span>
              <span className="t">{s.label}</span>
              <span className="s">{STATE_TEXT[s.state]}</span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
