"""Regenerate tests/data/gly3_ff14sb.json: AMBER ff14SB (OpenMM amber14-all.xml) parameters for the gly3.pqr geometry.

Usage (needs openmm, not a smeltery dependency): python make_gly3_ff14sb.py ../gly3.pqr gly3_ff14sb.json
Writes gly3_pdbfmt.pdb into the current directory. Not collected by pytest.
"""

import json
import sys

import openmm
from openmm import app, unit

pqr, out = sys.argv[1], sys.argv[2]
lines, names = [], []
for ln in open(pqr):
    if ln.startswith("ATOM"):
        f = ln.split()
        name, res, rid, x, y, z = f[2], f[3], f[4], float(f[5]), float(f[6]), float(f[7])
        el = name[0]
        nm = " " + name if len(name) < 4 else name
        lines.append(
            "ATOM  %5d %-4s %3s A%4d    %8.3f%8.3f%8.3f  1.00  0.00          %2s"
            % (len(lines) + 1, nm, res, int(rid), x, y, z, el)
        )
        names.append((name, res, int(rid), el, (x, y, z)))
open("gly3_pdbfmt.pdb", "w").write("\n".join(lines) + "\nEND\n")
pdb = app.PDBFile("gly3_pdbfmt.pdb")
ff = app.ForceField("amber14-all.xml")
s = ff.createSystem(pdb.topology, nonbondedMethod=app.NoCutoff, constraints=None)
data = {
    "openmm_version": openmm.__version__,
    "forcefield": "amber14-all.xml (ff14SB protein)",
    "atoms": [],
    "bonds": [],
    "angles": [],
    "torsions": [],
}
nb = [f for f in s.getForces() if isinstance(f, openmm.NonbondedForce)][0]
for i in range(nb.getNumParticles()):
    q, sg, ep = nb.getParticleParameters(i)
    n = names[i]
    data["atoms"].append(
        {
            "name": n[0],
            "res": n[1],
            "resid": n[2],
            "element": n[3],
            "xyz": n[4],
            "q": q.value_in_unit(unit.elementary_charge),
            "sigma_ang": sg.value_in_unit(unit.angstrom),
            "eps_kcal": ep.value_in_unit(unit.kilocalorie_per_mole),
        }
    )
for f in s.getForces():
    if isinstance(f, openmm.HarmonicBondForce):
        for k in range(f.getNumBonds()):
            i, j, r0, kb = f.getBondParameters(k)
            # OpenMM: E = 1/2 k (r-r0)^2, k in kJ/mol/nm^2 ; AMBER-form k_kcal/A^2 with E = k (r-r0)^2 is k/2
            data["bonds"].append(
                [
                    i,
                    j,
                    0.5 * kb.value_in_unit(unit.kilocalorie_per_mole / unit.angstrom**2),
                    r0.value_in_unit(unit.angstrom),
                ]
            )
    if isinstance(f, openmm.HarmonicAngleForce):
        for k in range(f.getNumAngles()):
            i, j, ln, th, kt = f.getAngleParameters(k)
            data["angles"].append(
                [
                    i,
                    j,
                    ln,
                    0.5 * kt.value_in_unit(unit.kilocalorie_per_mole / unit.radian**2),
                    th.value_in_unit(unit.degree),
                ]
            )
    if isinstance(f, openmm.PeriodicTorsionForce):
        for k in range(f.getNumTorsions()):
            i, j, ln, m, per, ph, kp = f.getTorsionParameters(k)
            data["torsions"].append(
                [i, j, ln, m, per, kp.value_in_unit(unit.kilocalorie_per_mole), ph.value_in_unit(unit.degree)]
            )
print({k: (len(v) if isinstance(v, list) else v) for k, v in data.items()})
print("net charge", sum(a["q"] for a in data["atoms"]))
json.dump(data, open(out, "w"), indent=1)
