import pytest
from rdkit import Chem

from smeltery.bench.provisional import place_site, provisional_pose
from smeltery.gui.pipeline import Pipeline
from smeltery.gui.results import load_run


def test_atom_order_is_AddHs_order():
    syms, xyz = provisional_pose("OC(=O)c1ccc(F)cc1")
    expect = tuple(a.GetSymbol() for a in Chem.AddHs(Chem.MolFromSmiles("OC(=O)c1ccc(F)cc1")).GetAtoms())
    assert syms == expect and len(xyz) == len(expect)


def test_atom_order_is_independent_of_the_embedding_seed():
    a, _ = provisional_pose("OC(=O)c1ccc(F)cc1", seed=1)
    b, _ = provisional_pose("OC(=O)c1ccc(F)cc1", seed=99)
    assert a == b


def test_provisional_order_equals_the_runs_pose_order(run_dir):

    from rdkit import Chem

    from smeltery.gui.results import pose_sdf

    run = load_run(run_dir)
    for name, smiles in run.candidates.items():
        sdf = pose_sdf(run, name)
        block = sdf.split("$$$$")[0]
        mol = Chem.MolFromMolBlock(block, removeHs=False)
        assert mol is not None, name
        pose_syms = tuple(a.GetSymbol() for a in mol.GetAtoms())
        prov_syms, _ = provisional_pose(smiles)
        assert pose_syms == prov_syms, name


def test_place_site_appends_one_site_to_the_pipeline(pipeline):
    p = place_site(pipeline, atom=3, from_atom=4)
    assert p.stage("pocket").params["site"] == [{"q": 1.0, "atom": 3, "from_atom": 4, "distance": 3.0}]


def test_place_site_keeps_the_pipeline_the_only_state(pipeline):
    p = place_site(pipeline, atom=3, from_atom=4)
    assert Pipeline.from_toml(p.to_toml()).stage("pocket").params["site"] == p.stage("pocket").params["site"]


def test_place_site_refuses_two_identical_indices(pipeline):
    import pytest

    with pytest.raises(ValueError):
        place_site(pipeline, atom=3, from_atom=3)


@pytest.fixture
def run_dir(tmp_path):
    from gui_fixture import make_run_dir

    return make_run_dir(tmp_path)


@pytest.fixture
def pipeline():
    p = Pipeline.minimal("c", "parent", {"parent": "C", "a": "C"})
    p.stage("pocket").params["site"] = []
    p.set("cut", keep=1, floor=0.0)
    return p
