import { useEffect, useRef, useState } from "react";

type Figure = { data?: unknown[]; layout?: Record<string, unknown> };

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Draws a figure built in Python (smeltery.gui.views) without re-deriving anything. The only change is the theme:
 * transparent backgrounds and the ink colour from the tokens, so the same figure reads in dark mode.
 * Plotly is loaded on first use, so pages without plots never download it.
 */
export function PlotlyFigure({ figure, name, height }: { figure: Figure; name: string; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    const el = ref.current;
    if (!el) return;
    import("plotly.js-cartesian-dist-min")
      .then((mod) => {
        if (cancelled) return;
        const Plotly = mod.default;
        const ink = token("--ink");
        const layout = {
          ...(figure.layout ?? {}),
          paper_bgcolor: "transparent",
          plot_bgcolor: "transparent",
          font: { ...((figure.layout?.font as object) ?? {}), color: ink, family: "IBM Plex Sans, sans-serif" },
          autosize: true,
          ...(height ? { height } : {}),
        };
        return Plotly.react(el, (figure.data ?? []) as Plotly.Data[], layout as Partial<Plotly.Layout>, {
          displayModeBar: false,
          responsive: true,
        });
      })
      .catch((e: unknown) => setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [figure, height]);
  if (error) return <div className="banner" role="alert">The {name} plot could not be drawn: {error}</div>;
  return <div ref={ref} className="figure" data-figure={name} role="img" aria-label={`${name} plot`} />;
}
