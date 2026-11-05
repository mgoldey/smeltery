"""Today's methods as registrations: the stages `smeltery run` executes, and the library tiers it does not yet.

Registration order is the catalog's hierarchy order (by rung, then execution); `order` is where `smeltery run`
executes each one. The builtins carry no `hook`: their code paths stay in `smeltery.cli` (docking, the pocket, the
two field-interaction tiers), and their registration is what the catalog, the steps, the CLI's validation and
input staging read. Their config rules beyond "a table of these keys" are the `smeltery.cli` validators named in
`validate`, imported only when a config is validated.
"""

from __future__ import annotations

from collections.abc import Callable

from . import MethodSpec, register


def _cli_check(name: str) -> Callable[[dict], None]:
    """A config validator from `smeltery.cli`, looked up when called (the CLI imports RDKit; the GUI need not)."""

    def check(cfg: dict) -> None:
        from .. import cli

        getattr(cli, name)(cfg)

    check.__name__ = name
    return check


#: What the two SCF-convergence knobs and the iteration cap mean, shared by the RHF and KS-DFT tiers.
_SCF_HELP = {
    "energy_conv": "SCF energy convergence threshold given to ferric for both SCFs of every pose",
    "density_conv": "SCF density convergence threshold given to ferric for both SCFs of every pose",
    "max_iter": "SCF iteration cap; unset keeps ferric's own limit, and an SCF that does not converge is an error",
}


# Each cost text reads its numbers from the code that owns them (the calibration in `smeltery.cost`, the tier's
# own estimator), so it cannot drift from the model; only the surrounding wording lives here.


def _cost_dft() -> str:
    from .. import cost
    from ..tiers import FieldDftInteraction

    t = FieldDftInteraction()
    ref = cost.PBE_REFERENCE
    return (
        f"measured {t.functional} reference ({ref.n_atoms} atoms, {ref.n_basis_functions} bf: "
        f"{cost.PBE_REFERENCE_VACUUM_SECONDS:g} s vacuum + {cost.PBE_REFERENCE_FIELD_SECONDS:g} s in field) scaled by "
        f"nbf^2 x XC grid points; {t.functional} at {t.basis.upper()} only, otherwise cannot say; "
        "a LOWER BOUND for slower-converging chemistry"
    )


def _cost_rhf() -> str:
    from .. import cost

    cal = cost.rhf_calibration()
    return (
        f"measured {cal.reference_name} reference ({cal.reference_nbf} bf, {cal.reference_seconds:.3g} s per pose, "
        f"vacuum plus field RHF) scaled by (basis functions / {cal.reference_nbf})^{cal.exponent:.2f}; "
        f"{cal.basis.upper()} only, otherwise cannot say; assumes the reference's SCF iteration count"
    )


def _cost_gfn2() -> str:
    from ..tiers import Gfn2

    return f"{Gfn2().estimate_cost([])['kind']}; estimate_cost counts the runs, not seconds"


def _cost_forcefield() -> str:
    from ..tiers import ForceField

    return f"cannot say: {ForceField().estimate_cost([])['basis']}"


def _memory_dft() -> str:
    from ..tiers import FieldDftInteraction

    t = FieldDftInteraction()
    return (
        f"peak resident GB of the largest pose (AO cache + packed RI tensor), checked against memory_budget_gb "
        f"(default {t.memory_budget_gb:g}) before the first SCF; basis functions are counted for "
        f"{t.basis.upper()} only, otherwise cannot say"
    )


def builtin_specs() -> list[MethodSpec]:
    from ..gui.catalog import Param, params_from_dataclass
    from ..tiers import FieldDftInteraction, FieldInteraction, PairedPoses

    return [
        MethodSpec(
            "structure",
            "Target structure",
            "input",
            "prep",
            step="active_site",
            section="structure",
            file_knobs=("spec",),
            order=10,
            provides=("receptor", "field"),
            exclusive=("pocket",),
            extras=("docking",),
            validate=_cli_check("_validate_structure"),
            summary="A .pdb, PDB id or AFDB accession, prepared into a receptor and a point-charge field.",
            params=(
                Param("spec", "str", required=True, help="a .pdb path, a PDB id or (afdb) a UniProt accession"),
                Param("provider", "choice", "pdb", choices=("pdb", "afdb"), help="afdb is PREDICTED: recorded"),
                Param("allow_network", "bool", False, help="fetch an id or accession; a local file never is"),
                Param("center", "vec3", optional=True, help="box centre, Angstrom; or reference_ligand"),
                Param("reference_ligand", "str", optional=True, help="HETATM RES[:CHAIN[:RESNUM]]; or center"),
                Param("pocket_cutoff", "float", required=True, help="Angstrom; no default: truncation is not monotone"),
                Param("chains", "str_list", optional=True, help="protein chains to keep (default all)"),
            ),
        ),
        MethodSpec(
            "pocket",
            "Pocket field",
            "input",
            "field",
            step="active_site",
            slot="field_source",
            section="pocket",
            file_knobs=("file",),
            order=40,
            needs=("poses",),
            provides=("field",),
            exclusive=("structure",),
            validate=_cli_check("_validate_pocket"),
            summary="Point charges: a .pdb/.pqr file in the receptor's frame, or sites placed on the parent's pose 0.",
            params=(
                Param("file", "path", optional=True, help="a .pdb (via pdb2pqr30) or .pqr; or site"),
                Param("site", "sites", optional=True, help="charges `distance` A beyond parent atom `atom`"),
            ),
        ),
        MethodSpec(
            "docking",
            "Dock the parent (Vina)",
            "geometry",
            "search",
            step="active_site",
            slot="pose_source",
            section="docking",
            file_knobs=("receptor",),
            tier="docking",
            order=20,
            needs=("receptor",),
            provides=("parent_poses",),
            extras=("docking",),
            quantity="docking_score",
            validate=_cli_check("_validate_docking"),
            cost_model=lambda: (
                "cannot say: no Vina timing is recorded in smeltery, so estimate_cost gives no prediction"
            ),
            summary="Vina search for the PARENT only; analogues are paired to its poses, never docked.",
            params=(
                Param("receptor", "path", optional=True, help="prepared .pdbqt; comes from [structure] if present"),
                Param("box_center", "vec3", optional=True, help="A, receptor frame; from [structure] if present"),
                Param("box_size", "vec3", (24.0, 24.0, 24.0), help="Angstrom"),
                Param("seeds", "int_list", help="independent ETKDG embeddings; the budget lever", optional=True),
                Param(
                    "exhaustiveness",
                    "int",
                    optional=True,
                    help="Vina effort per seed (default 4); more moved redock RMSD less than seed noise: add seeds",
                ),
                Param(
                    "n_poses",
                    "int",
                    optional=True,
                    help="docked poses kept per seed; the seeds are pooled and analogues are paired to them",
                ),
            ),
        ),
        MethodSpec(
            "paired_poses",
            "Paired poses",
            "geometry",
            "pair",
            step="poses",
            section="poses",
            knobs=PairedPoses,
            tier="paired_poses",
            required=True,
            order=30,
            provides=("poses",),
            cost_model=lambda: (
                "cannot say: estimate_cost gives no prediction (no recorded timing for its RDKit embeddings "
                "and common-core search)"
            ),
            summary="Analogue pose i copies parent pose i's common core exactly, so pose noise cancels in ddE.",
            params=params_from_dataclass(
                PairedPoses,
                {
                    "n_poses": "ETKDG poses of the parent; not used when the parent is docked",
                    "relax_unmapped": "MMFF-relax only the substituent; needed to pass PoseBusters",
                    "seed": "seed for the parent's ETKDG embeddings and their rigid jitter; a new seed draws new poses",
                    "jitter_deg": "largest random rotation (deg) per parent pose, as docked spread; unused if docked",
                    "jitter_ang": "largest random shift (A) per parent pose, mimicking docked spread; unused if docked",
                    "min_core_heavy": "fewest heavy atoms the common core may have; a smaller core refuses the pair",
                    "mcs_timeout_s": "seconds the core search may take; timing out refuses (10 is short for drugs)",
                    "first_pose_index": "index of pose 0 in the ensemble it was cut from; offsets the analogue seeds",
                },
            ),
        ),
        MethodSpec(
            "parity",
            "Electron parity",
            "check",
            "gate",
            order=50,
            needs=("poses",),
            implicit=True,
            summary="Refuses an odd electron count: the field-interaction tier is closed-shell RHF.",
        ),
        MethodSpec(
            "posebusters",
            "PoseBusters",
            "check",
            "gate",
            step="checks",
            order=60,
            needs=("poses",),
            gate_key="posebusters",
            extras=("posebusters",),
            quantity="pose_validity",
            summary="Every pose must pass; a failure stops the run, it never drops a candidate silently.",
        ),
        MethodSpec(
            "forcefield",
            "MMFF94 energy and strain",
            "empirical",
            "recorded",
            step="checks",
            tier="forcefield",
            order=70,
            needs=("poses",),
            gate_key="forcefield",
            quantity="dE_int",
            cost_model=_cost_forcefield,
            summary="Recorded per pose; a gate only when max_strain_kcal is set.",
            params=(Param("max_strain_kcal", "float", optional=True, help="kcal/mol; reject any pose above it"),),
        ),
        MethodSpec(
            "rescoring",
            "Rescoring",
            "empirical",
            "measure",
            tier="rescoring",
            wired=False,
            needs=("poses",),
            quantity="docking_score",
            why_unwired="rank-only scores (is_delta_g=False) cannot be differenced; smeltery run has no stage for it",
        ),
        MethodSpec(
            "gfn2",
            "GFN2-xTB in the field",
            "semiempirical",
            "recorded",
            step="checks",
            tier="gfn2",
            order=80,
            needs=("poses", "field"),
            gate_key="xtb",
            quantity="dE_int",
            cost_model=_cost_gfn2,
            summary="A gate on ionization state and gross failure, never a ranker (xtb-vs-DFT Spearman 0.011).",
        ),
        MethodSpec(
            "field_interaction",
            "Field interaction (ferric RHF)",
            "ab_initio",
            "measure",
            step="measure",
            slot="measure",
            section="field_interaction",
            knobs=FieldInteraction,
            tier="field_interaction",
            required=True,
            order=90,
            needs=("poses", "field"),
            provides=("dE_int",),
            quantity="dE_int",
            validate=_cli_check("_validate_measurement"),
            cost_model=_cost_rhf,
            summary="dE_int = E(in field) - E(vacuum) per pose: a difference, so it compares across formulas.",
            params=params_from_dataclass(FieldInteraction, {"basis": "ferric basis set name", **_SCF_HELP}),
            # Required, but satisfied by its alternative: a pipeline measures dE_int with RHF or with KS-DFT.
            exclusive=("field_dft_interaction",),
        ),
        MethodSpec(
            "field_dft_interaction",
            "Field interaction (ferric PBE + D3)",
            "ab_initio",
            "measure",
            step="measure",
            slot="measure",
            section="field_dft_interaction",
            knobs=FieldDftInteraction,
            tier="field_dft_interaction",
            order=100,
            needs=("poses", "field"),
            provides=("dE_int",),
            quantity="dE_int",
            validate=_cli_check("_validate_measurement"),
            cost_model=_cost_dft,
            memory_model=_memory_dft,
            params=params_from_dataclass(
                FieldDftInteraction,
                {
                    "basis": "ferric basis set name; the memory check counts basis functions for STO-3G only",
                    "functional": "libxc name ferric accepts; D3(BJ) is added separately, not by a -D3 suffix",
                    "memory_budget_gb": "refuse before the first SCF if the largest pose is predicted not to fit",
                    "require_free_gb": "refuse unless this much memory is actually free (unset: no check)",
                    **_SCF_HELP,
                    "check_memory": "false skips the pre-SCF memory check, so memory_budget_gb is not enforced",
                },
            ),
            summary=(
                "The same dE_int as field_interaction, from KS-DFT instead of RHF. D3(BJ) is "
                "reported separately and cancels in dE_int at fixed geometry."
            ),
            exclusive=("field_interaction",),
        ),
        MethodSpec(
            "surface_esp",
            "Surface ESP",
            "ab_initio",
            "measure",
            tier="surface_esp",
            wired=False,
            needs=("poses",),
            quantity="esp",
            why_unwired="a library tier; smeltery run has no stage for it",
        ),
        MethodSpec(
            "qmmm",
            "QM/MM single point",
            "embedded_qm",
            "measure",
            tier="qmmm",
            wired=False,
            needs=("poses",),
            quantity="dE_int",
            why_unwired="needs MM parameters and a QM partition of a whole-structure pose; not in smeltery run",
        ),
        MethodSpec(
            "designability",
            "Designability (property drift, SA)",
            "designable",
            "measure",
            # Descriptors of the SMILES, so it needs no pose and no field: the parent comes from the run itself.
            tier="designability",
            wired=False,
            quantity="designability",
            why_unwired="a library tier; smeltery run has no stage for it",
            summary=(
                "Drift from the parent in MW, logP, TPSA, QED and synthetic accessibility. "
                "A counterweight to dE_int, which rewards added atoms; it gates nothing."
            ),
        ),
        MethodSpec(
            "cut",
            "Paired cut",
            "decision",
            "rank",
            step="cut",
            section="cut",
            required=True,
            order=200,
            needs=("dE_int",),
            provides=("survivors",),
            validate=_cli_check("_validate_cut"),
            summary="Ranks paired ddE against the parent; never orders what the uncertainty cannot separate.",
            params=(
                Param("keep", "int", required=True, help="analogues to keep (1..number of analogues)"),
                Param("z", "float", 2.0, help="resolution in standard errors"),
                Param("floor", "float", optional=True, help="kcal/mol; required while the tier's floor is unmeasured"),
            ),
        ),
    ]


def register_builtins() -> None:
    for spec in builtin_specs():
        register(spec)
