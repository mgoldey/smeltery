import { useCallback, useEffect, useRef, useState } from "react";
import { api, unwrap, type Schemas } from "../../../api/client";

export type PipelineState = Schemas["PipelineState"];
export type EditOp = Schemas["PipelineEdit"]["op"];
/** Where the latest TOML came from, so the TOML pane is not overwritten while someone is typing in it. */
export type Origin = "load" | "visual" | "toml";

/**
 * The pipeline editor's state is the pipeline's TOML and nothing else. Every edit -- a palette click, a knob, a
 * reorder, an atom click, or typing in the TOML pane -- goes through the API, which applies it with
 * smeltery.gui.pipeline.Pipeline and returns the new TOML with every issue Pipeline.check() finds.
 */
export function usePipeline(runIdx: number) {
  const [state, setState] = useState<PipelineState | null>(null);
  const [origin, setOrigin] = useState<Origin>("load");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const toml = useRef("");
  const chain = useRef<Promise<unknown>>(Promise.resolve());

  const apply = useCallback((from: Origin, request: (current: string) => Promise<PipelineState>) => {
    // Edits are applied one after another, each to the TOML the previous one produced.
    const next = chain.current.then(async () => {
      setPending(true);
      try {
        const s = await request(toml.current);
        toml.current = s.toml;
        setState(s);
        setOrigin(from);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setPending(false);
      }
    });
    chain.current = next;
    return next;
  }, []);

  useEffect(() => {
    void apply("load", () => unwrap(api.POST("/api/pipeline/from_run", { body: { run: runIdx } })));
  }, [apply, runIdx]);

  const edit = useCallback(
    (op: EditOp, key: string, extra: { params?: Record<string, unknown>; at?: number; to?: number } = {}) =>
      apply("visual", (current) => unwrap(api.POST("/api/pipeline/edit", { body: { toml: current, op, key, ...extra } }))),
    [apply],
  );
  const check = useCallback(
    (text: string) => apply("toml", () => unwrap(api.POST("/api/pipeline/check", { body: { toml: text } }))),
    [apply],
  );
  const placeSite = useCallback(
    (atom: number, fromAtom: number, q: number, distance: number) =>
      apply("visual", (current) =>
        unwrap(api.POST("/api/pipeline/site", { body: { toml: current, atom, from_atom: fromAtom, q, distance } })),
      ),
    [apply],
  );
  return { state, origin, error, pending, edit, check, placeSite };
}
