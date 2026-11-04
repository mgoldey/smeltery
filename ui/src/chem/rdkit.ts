import initRDKitModule, { type MainModule } from "@rdkit/rdkit";
import wasmUrl from "@rdkit/rdkit/RDKit_minimal.wasm?url";

let loading: Promise<MainModule> | null = null;

/** RDKit.js, loaded once and only when a page needs it. The wasm is bundled; nothing is fetched from a CDN. */
export function rdkit(): Promise<MainModule> {
  loading ??= initRDKitModule({ locateFile: () => wasmUrl });
  return loading;
}

export interface Depiction {
  atoms: { index: number; symbol: string; x: number; y: number }[];
  bonds: { a: number; b: number; order: number }[];
}

/**
 * 2D coordinates for `smiles` WITH explicit hydrogens, atoms in RDKit's AddHs order -- the order the run's poses
 * are written in and `[[pocket.site]]` indexes. Hydrogens matter: without them the indices of everything after the
 * heavy atoms would not exist, and a click could not name a hydrogen.
 */
export async function depict(smiles: string, withHydrogens = true): Promise<Depiction> {
  const RDKit = await rdkit();
  const heavy = RDKit.get_mol(smiles);
  if (!heavy) throw new Error(`RDKit cannot read ${smiles}`);
  const molblock = withHydrogens ? heavy.add_hs() : heavy.get_molblock();
  heavy.delete();
  const mol = RDKit.get_mol(molblock, JSON.stringify({ removeHs: false }));
  if (!mol) throw new Error(`RDKit cannot rebuild ${smiles} with hydrogens`);
  mol.set_new_coords();
  const lines = mol.get_molblock().split("\n");
  mol.delete();
  const counts = lines[3] ?? "";
  const nAtoms = Number(counts.slice(0, 3));
  const nBonds = Number(counts.slice(3, 6));
  const atoms = Array.from({ length: nAtoms }, (_, i) => {
    const l = lines[4 + i] ?? "";
    return { index: i, x: Number(l.slice(0, 10)), y: Number(l.slice(10, 20)), symbol: l.slice(31, 34).trim() };
  });
  const bonds = Array.from({ length: nBonds }, (_, i) => {
    const l = lines[4 + nAtoms + i] ?? "";
    return { a: Number(l.slice(0, 3)) - 1, b: Number(l.slice(3, 6)) - 1, order: Number(l.slice(6, 9)) };
  });
  return { atoms, bonds };
}
