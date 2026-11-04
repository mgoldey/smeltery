import { useEffect, useState, type KeyboardEvent } from "react";
import { depict, type Depiction } from "../chem/rdkit";

/**
 * The parent drawn in 2D with every atom, hydrogens included, as a focusable control carrying its index. Indices are
 * RDKit AddHs order, the order `[[pocket.site]]` uses; click (or Enter on) two atoms to choose a site.
 */
export function AtomPicker({ smiles, picked, onPick }: { smiles: string; picked: number[]; onPick: (i: number) => void }) {
  const [d, setD] = useState<Depiction | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    depict(smiles)
      .then((x) => live && setD(x))
      .catch((e: unknown) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [smiles]);
  if (error) return <div className="banner" role="alert">The structure could not be drawn: {error}</div>;
  if (!d) return <div className="picker" aria-busy="true" />;
  // Draw in ~pixel units (40 per Angstrom) so text and strokes are ordinary sizes; sub-unit font sizes inside a
  // scaled viewBox render unreliably.
  const S = 40;
  const xs = d.atoms.map((a) => a.x * S);
  const ys = d.atoms.map((a) => -a.y * S);
  const pad = 0.8 * S;
  const [x0, x1, y0, y1] = [Math.min(...xs) - pad, Math.max(...xs) + pad, Math.min(...ys) - pad, Math.max(...ys) + pad];
  const at = (i: number) => {
    const a = d.atoms[i]!;
    return { x: a.x * S, y: -a.y * S };
  };
  const key = (i: number) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onPick(i);
    }
  };
  return (
    <svg className="picker" viewBox={`${x0} ${y0} ${x1 - x0} ${y1 - y0}`} role="group" aria-label={`Atoms of ${smiles}`}>
      {d.bonds.map((b, i) => {
        const [p, q] = [at(b.a), at(b.b)];
        const lines = [<line key="a" x1={p.x} y1={p.y} x2={q.x} y2={q.y} />];
        if (b.order >= 2) {
          // A second line offset along the normal, drawn shortened, as in a skeletal formula.
          const [dx, dy] = [q.x - p.x, q.y - p.y];
          const len = Math.hypot(dx, dy) || 1;
          const [nx, ny] = [(-dy / len) * 6, (dx / len) * 6];
          const [sx, sy] = [dx * 0.15, dy * 0.15];
          lines.push(<line key="b" x1={p.x + nx + sx} y1={p.y + ny + sy} x2={q.x + nx - sx} y2={q.y + ny - sy} />);
        }
        return (
          <g key={i} className="bond" data-order={b.order}>
            {lines}
          </g>
        );
      })}
      {d.atoms.map((a) => {
        const h = a.symbol === "H";
        const order = picked.indexOf(a.index);
        const { x, y } = at(a.index);
        return (
          <g
            key={a.index}
            className={`atom${h ? " hydrogen" : ""}${order >= 0 ? " picked" : ""}`}
            role="button"
            tabIndex={0}
            aria-pressed={order >= 0}
            aria-label={`atom ${a.index} ${a.symbol}`}
            data-atom-index={a.index}
            data-element={a.symbol}
            onClick={() => onPick(a.index)}
            onKeyDown={key(a.index)}
          >
            <circle cx={x} cy={y} r={h ? 8 : 12} />
            {a.symbol !== "C" && !h ? (
              <text x={x} y={y} dy="4.5">
                {a.symbol}
              </text>
            ) : null}
            {order >= 0 ? (
              <text className="pick-label" x={x + 14} y={y - 12}>
                {order === 0 ? "site" : "from"}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
