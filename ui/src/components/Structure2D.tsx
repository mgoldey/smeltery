import { useEffect, useState } from "react";
import { rdkit } from "../chem/rdkit";

/** A token colour as RDKit wants it: [r, g, b] in 0..1, read from the page's own CSS variable (so night mode follows). */
function token(name: string): [number, number, number] {
  const hex = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return [0.2, 0.6, 0.2];
  const n = parseInt(m[1]!, 16);
  return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/**
 * A 2D structure drawn by RDKit.js (bundled wasm). Its SVG is RDKit's own output, built from a SMILES string.
 *
 * `highlightAtoms`, when given, marks those atoms (and the bonds between them) in the accent colour. The indices are
 * of the SMILES with explicit hydrogens; AddHs appends hydrogens after the heavy atoms, so the heavy atoms keep their
 * index and the hydrogens (not drawn) are simply not highlightable. The set is also exposed as `data-core`.
 */
export function Structure2D({
  smiles,
  width = 160,
  height = 110,
  highlightAtoms,
}: {
  smiles: string;
  width?: number;
  height?: number;
  highlightAtoms?: number[];
}) {
  const [svg, setSvg] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  const key = highlightAtoms?.join(",");
  useEffect(() => {
    let live = true;
    void rdkit().then((RDKit) => {
      const mol = RDKit.get_mol(smiles);
      if (!mol) {
        if (live) setBad(true);
        return;
      }
      const opts: Record<string, unknown> = { width, height, backgroundColour: [1, 1, 1, 0], bondLineWidth: 1.4 };
      if (key !== undefined) {
        const n = mol.get_num_atoms();
        const atoms = (key ? key.split(",").map(Number) : []).filter((i) => i < n);
        const inCore = new Set(atoms);
        const bonds: number[] = [];
        const json = JSON.parse(mol.get_json()) as { molecules: { bonds?: { atoms: [number, number] }[] }[] };
        (json.molecules[0]?.bonds ?? []).forEach((b, i) => {
          if (inCore.has(b.atoms[0]) && inCore.has(b.atoms[1])) bonds.push(i);
        });
        Object.assign(opts, { atoms, bonds, highlightColour: token("--accent"), highlightRadius: 0.3 });
      }
      const out = mol.get_svg_with_highlights(JSON.stringify(opts));
      mol.delete();
      if (live) {
        setSvg(out);
        setBad(false);
      }
    });
    return () => {
      live = false;
    };
  }, [smiles, width, height, key]);
  if (bad) return <span className="field-error">cannot draw {smiles}</span>;
  return (
    <span
      className="structure2d"
      role="img"
      aria-label={`structure ${smiles}`}
      data-smiles={smiles}
      data-core={key}
      style={{ width, height }}
      // RDKit's SVG, generated locally from the SMILES; no user-supplied markup reaches this.
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    />
  );
}
