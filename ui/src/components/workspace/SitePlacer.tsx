import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, unwrap } from "../../api/client";
import { AtomPicker } from "../AtomPicker";
import { MolstarViewer } from "../MolstarViewer";
import { provisionalScene } from "../scenes";

/**
 * Place a pocket charge by clicking two atoms of the parent: the site atom, then the atom it points away from. The
 * click supplies only the two indices; q and distance come from the form, and the pipeline stays the only state.
 */
export function SitePlacer({
  parent,
  smiles,
  disabled,
  onPlace,
}: {
  parent: string;
  smiles: string;
  disabled: boolean;
  onPlace: (atom: number, fromAtom: number, q: number, distance: number) => Promise<unknown>;
}) {
  const [picked, setPicked] = useState<number[]>([]);
  const [q, setQ] = useState("1.0");
  const [distance, setDistance] = useState("3.0");
  const prov = useQuery({
    queryKey: ["provisional", smiles],
    queryFn: () => unwrap(api.GET("/api/provisional", { params: { query: { smiles } } })),
  });
  const pick = (i: number) =>
    setPicked((p) => (p.includes(i) ? p.filter((x) => x !== i) : p.length < 2 ? [...p, i] : [i]));
  const scene = useMemo(() => (prov.data ? provisionalScene(prov.data.molblock, picked) : undefined), [prov.data, picked]);
  const [atom, fromAtom] = picked;
  const qn = Number(q);
  const dn = Number(distance);
  const valid = atom !== undefined && fromAtom !== undefined && Number.isFinite(qn) && Number.isFinite(dn) && dn > 0;

  return (
    <section aria-label="Place a pocket site">
      <h2>Place a pocket site</h2>
      <p className="lede">
        Click the atom the charge sits beyond, then the atom it points away from. Atoms are numbered as in the run's poses
        (hydrogens included); the 3D view is provisional geometry for choosing, never scored or recorded.
      </p>
      <div className="site-grid">
        <AtomPicker smiles={smiles} picked={picked} onPick={pick} />
        <div>
          {scene ? <MolstarViewer state={scene} label="provisional geometry" height={300} /> : null}
          <p data-testid="site-selection">
            {atom === undefined
              ? `Choose the site atom on ${parent}.`
              : fromAtom === undefined
                ? `Site atom ${atom}. Now choose the atom it points away from.`
                : `Charge beyond atom ${atom}, pointing away from atom ${fromAtom}.`}
          </p>
          {fromAtom !== undefined ? (
            <form
              className="controls"
              onSubmit={(e) => {
                e.preventDefault();
                if (valid) void onPlace(atom!, fromAtom, qn, dn).then(() => setPicked([]));
              }}
            >
              <div>
                <label htmlFor="site-q">Charge q (e)</label>
                <input id="site-q" className="num" value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              <div>
                <label htmlFor="site-distance">Distance (Å)</label>
                <input id="site-distance" className="num" value={distance} onChange={(e) => setDistance(e.target.value)} />
              </div>
              <button className="primary" type="submit" disabled={disabled || !valid}>
                Place site
              </button>
              <button type="button" onClick={() => setPicked([])}>
                Clear
              </button>
            </form>
          ) : null}
        </div>
      </div>
    </section>
  );
}
