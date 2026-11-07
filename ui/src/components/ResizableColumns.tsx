import { Children, useCallback, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

/**
 * Side-by-side columns whose widths the user drags. Widths are relative weights (CSS `fr`), so the columns keep their
 * proportions when the window resizes; `minPx` stops a column being dragged shut. The weights are remembered per
 * `storageKey` in this browser only (a per-viewer convenience: they never reach the pipeline or the server).
 * Each divider is a focusable separator: arrow keys move it, double-click or Home puts the defaults back.
 */
export function ResizableColumns({
  storageKey,
  defaults,
  minPx = 160,
  className,
  children,
}: {
  storageKey: string;
  defaults: number[];
  minPx?: number;
  className?: string;
  children: ReactNode;
}) {
  const cols = Children.toArray(children);
  const key = `smeltery.cols.${storageKey}`;
  const [weights, setWeights] = useState<number[]>(() => load(key, defaults));
  const host = useRef<HTMLDivElement>(null);

  const save = useCallback(
    (w: number[]) => {
      setWeights(w);
      try {
        localStorage.setItem(key, JSON.stringify(w));
      } catch {
        // storage blocked (private window): the widths just are not remembered
      }
    },
    [key],
  );

  /** Move divider `i` (between column i and i+1) by `dx` pixels, keeping both columns at least `minPx` wide. */
  const shift = useCallback(
    (base: number[], i: number, dx: number) => {
      const el = host.current;
      if (!el) return base;
      const total = base.reduce((a, b) => a + b, 0);
      const pxPerWeight = colsWidth(el, cols.length) / total;
      if (!(pxPerWeight > 0)) return base;
      const a = base[i];
      const b = base[i + 1];
      if (a === undefined || b === undefined) return base;
      const pair = a + b;
      const minW = minPx / pxPerWeight;
      const left = Math.min(Math.max(a + dx / pxPerWeight, minW), pair - minW);
      if (pair - 2 * minW <= 0) return base;
      const next = [...base];
      next[i] = left;
      next[i + 1] = pair - left;
      return next;
    },
    [cols.length, minPx],
  );

  const onPointerDown = (i: number) => (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const start = e.clientX;
    const base = weights;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    // One AbortController tears every listener down, so a gesture the browser cancels (touch interrupted, scroll
    // taken over) cannot leave a stale handler that later saves a width from an old starting point.
    const stop = new AbortController();
    const { signal } = stop;
    const move = (ev: globalThis.PointerEvent) => setWeights(shift(base, i, ev.clientX - start));
    const up = (ev: globalThis.PointerEvent) => {
      stop.abort();
      save(shift(base, i, ev.clientX - start));
    };
    target.addEventListener("pointermove", move, { signal });
    target.addEventListener("pointerup", up, { signal });
    target.addEventListener("pointercancel", () => stop.abort(), { signal });
  };

  const onKeyDown = (i: number) => (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 80 : 20;
    if (e.key === "ArrowLeft") save(shift(weights, i, -step));
    else if (e.key === "ArrowRight") save(shift(weights, i, step));
    else if (e.key === "Home") save(defaults);
    else return;
    e.preventDefault();
  };

  const template = weights.map((w) => `minmax(${minPx}px, ${w}fr)`).join(" 7px ");
  const total = weights.reduce((a, b) => a + b, 0);
  return (
    <div ref={host} className={`resizable-cols ${className ?? ""}`} style={{ gridTemplateColumns: template }}>
      {cols.map((c, i) => (
        <ColumnWithDivider
          key={i}
          last={i === cols.length - 1}
          now={Math.round((100 * weights.slice(0, i + 1).reduce((a, b) => a + b, 0)) / total)}
          onPointerDown={onPointerDown(i)}
          onKeyDown={onKeyDown(i)}
          onReset={() => save(defaults)}
        >
          {c}
        </ColumnWithDivider>
      ))}
    </div>
  );
}

function ColumnWithDivider({
  children,
  last,
  now,
  onPointerDown,
  onKeyDown,
  onReset,
}: {
  children: ReactNode;
  last: boolean;
  now: number;
  onPointerDown: (e: PointerEvent<HTMLDivElement>) => void;
  onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => void;
  onReset: () => void;
}) {
  return (
    <>
      {children}
      {last ? null : (
        <div
          className="col-divider"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize columns"
          aria-valuenow={now}
          aria-valuemin={0}
          aria-valuemax={100}
          tabIndex={0}
          title="Drag to resize · double-click to reset"
          onPointerDown={onPointerDown}
          onKeyDown={onKeyDown}
          onDoubleClick={onReset}
        />
      )}
    </>
  );
}

/** The width the columns share: the host's width less the dividers between them. */
function colsWidth(el: HTMLElement, n: number): number {
  return el.getBoundingClientRect().width - 7 * Math.max(n - 1, 0);
}

function load(key: string, defaults: number[]): number[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return defaults;
    const w = JSON.parse(raw);
    if (Array.isArray(w) && w.length === defaults.length && w.every((x) => typeof x === "number" && x > 0)) return w;
  } catch {
    // unreadable or blocked storage: fall back to the defaults
  }
  return defaults;
}
