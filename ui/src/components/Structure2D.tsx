import { useEffect, useState } from "react";
import { rdkit } from "../chem/rdkit";

/** A 2D structure drawn by RDKit.js (bundled wasm). Its SVG is RDKit's own output, built from a SMILES string. */
export function Structure2D({ smiles, width = 160, height = 110 }: { smiles: string; width?: number; height?: number }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  useEffect(() => {
    let live = true;
    void rdkit().then((RDKit) => {
      const mol = RDKit.get_mol(smiles);
      if (!mol) {
        if (live) setBad(true);
        return;
      }
      const out = mol.get_svg_with_highlights(JSON.stringify({ width, height, backgroundColour: [1, 1, 1, 0], bondLineWidth: 1.4 }));
      mol.delete();
      if (live) {
        setSvg(out);
        setBad(false);
      }
    });
    return () => {
      live = false;
    };
  }, [smiles, width, height]);
  if (bad) return <span className="field-error">cannot draw {smiles}</span>;
  return (
    <span
      className="structure2d"
      role="img"
      aria-label={`structure ${smiles}`}
      data-smiles={smiles}
      style={{ width, height }}
      // RDKit's SVG, generated locally from the SMILES; no user-supplied markup reaches this.
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    />
  );
}
