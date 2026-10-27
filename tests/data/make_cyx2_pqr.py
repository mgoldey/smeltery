"""Regenerate tests/data/cyx2.pqr: L-cystine (two CYX residues joined by SG-SG) in PDB2PQR's no-chain PQR layout.

Neutral amino-acid form (NH2 / COOH), RDKit ETKDGv3 (seed 7) + MMFF94. Atom names follow PDB/AMBER (N, CA, C, O, OXT,
CB, SG; H's start with H). Charges are RDKit Gasteiger charges, shifted per residue so each residue sums to exactly
0 (a stand-in for AMBER charges; the test reads the residue charge back from them). Radii are a constant 1.5.
Residue 1 and residue 2 are not peptide-bonded: the only bond between them is the disulfide.

Usage: python make_cyx2_pqr.py > cyx2.pqr   (needs rdkit only). Not collected by pytest.
"""

from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.AddHs(Chem.MolFromSmiles("N[C@@H](CSSC[C@H](N)C(=O)O)C(=O)O"))
params = AllChem.ETKDGv3()
params.randomSeed = 7
assert AllChem.EmbedMolecule(mol, params) == 0
AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
AllChem.ComputeGasteigerCharges(mol)
conf = mol.GetConformer()

s_atoms = [a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() == "S"]
names, resid = {}, {}
for r, sg in enumerate(s_atoms, start=1):
    cb = next(n.GetIdx() for n in mol.GetAtomWithIdx(sg).GetNeighbors() if n.GetSymbol() == "C")
    ca = next(n.GetIdx() for n in mol.GetAtomWithIdx(cb).GetNeighbors() if n.GetSymbol() == "C")
    n_ = next(n.GetIdx() for n in mol.GetAtomWithIdx(ca).GetNeighbors() if n.GetSymbol() == "N")
    c = next(n.GetIdx() for n in mol.GetAtomWithIdx(ca).GetNeighbors() if n.GetSymbol() == "C" and n.GetIdx() != cb)
    heavy = {sg: "SG", cb: "CB", ca: "CA", n_: "N", c: "C"}
    for o in mol.GetAtomWithIdx(c).GetNeighbors():
        if o.GetSymbol() == "O":
            double = mol.GetBondBetweenAtoms(c, o.GetIdx()).GetBondTypeAsDouble() == 2.0
            heavy[o.GetIdx()] = "O" if double else "OXT"
    h_names = {"N": ["H1", "H2"], "CA": ["HA"], "CB": ["HB2", "HB3"], "OXT": ["HXT"]}
    for idx, nm in list(heavy.items()):
        names[idx], resid[idx] = nm, r
        for h in mol.GetAtomWithIdx(idx).GetNeighbors():
            if h.GetSymbol() == "H":
                names[h.GetIdx()], resid[h.GetIdx()] = h_names[nm].pop(0), r
assert len(names) == mol.GetNumAtoms()

q = {i: float(mol.GetAtomWithIdx(i).GetProp("_GasteigerCharge")) for i in names}
for r in (1, 2):
    idx = [i for i in names if resid[i] == r]
    shift = sum(q[i] for i in idx) / len(idx)
    for i in idx:
        q[i] = round(q[i] - shift, 4)
    q[idx[0]] = round(q[idx[0]] - sum(q[i] for i in idx), 4)

serial = 0
for r in (1, 2):
    for i in sorted((i for i in names if resid[i] == r), key=lambda i: (names[i][0] == "H", i)):
        serial += 1
        p = conf.GetAtomPosition(i)
        print(f"ATOM  {serial:5d}  {names[i]:<3s} CYX  {r:4d}    {p.x:8.3f}{p.y:8.3f}{p.z:8.3f} {q[i]:7.4f} 1.5000")
print("TER\nEND")
