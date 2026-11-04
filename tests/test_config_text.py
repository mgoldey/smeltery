import tomllib
from pathlib import Path

import pytest

from smeltery.gui.config_text import ConfigTextError, apply, diff_lines, set_value

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.toml"))
DOC = """# top
campaign = "c"  # name
parent = "a"

[candidates]
a = "C"

# about poses
[poses]
n_poses = 3  # keep few
relax_unmapped = true

# about pocket
[pocket]
file = "p.pdb"

# first site
[[pocket.site]]
name = "s1"
r = 4.0

# second site
[[pocket.site]]
name = "s2"
r = 5.0

# about cut
[cut]
keep = 1
"""


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_round_trips_byte_for_byte(path):
    t = path.read_text()
    assert apply(t, tomllib.loads(t)) == t


def test_setting_one_knob_keeps_every_comment():
    t = (EXAMPLES[0].parent / "mwe_docking.toml").read_text()
    cfg = tomllib.loads(t)
    cfg["poses"]["n_poses"] = 4
    cfg["docking"]["n_poses"] = 4
    out = apply(t, cfg)
    assert tomllib.loads(out) == cfg
    for line in t.splitlines():
        if line.lstrip().startswith("#"):
            assert line in out.splitlines()


def test_a_value_keeps_its_trailing_comment_and_order():
    cfg = tomllib.loads(DOC)
    cfg["poses"]["n_poses"] = 9
    out = apply(DOC, cfg)
    assert "n_poses = 9  # keep few" in out
    assert out.replace("9", "3") == DOC


def test_removing_a_table_removes_only_it():
    cfg = tomllib.loads(DOC)
    del cfg["poses"]
    out = apply(DOC, cfg)
    assert tomllib.loads(out) == cfg
    assert "about poses" not in out and "n_poses" not in out
    for keep in ("# top", "# about pocket", "# about cut", "# first site", "# second site"):
        assert keep in out
    assert out == DOC.replace("# about poses\n[poses]\nn_poses = 3  # keep few\nrelax_unmapped = true\n\n", "")


def test_removing_the_last_table_leaves_clean_end():
    cfg = tomllib.loads(DOC)
    del cfg["cut"]
    out = apply(DOC, cfg)
    assert tomllib.loads(out) == cfg
    assert out.endswith("r = 5.0\n") and "about cut" not in out


def test_array_of_tables_add_remove_change():
    cfg = tomllib.loads(DOC)
    cfg["pocket"]["site"][1]["r"] = 6.5
    cfg["pocket"]["site"].append({"name": "s3", "r": 1.0})
    out = apply(DOC, cfg)
    assert tomllib.loads(out) == cfg
    assert "# second site" in out and "# first site" in out
    cfg["pocket"]["site"].pop(0)
    out2 = apply(out, cfg)
    assert tomllib.loads(out2) == cfg
    assert "first site" not in out2 and "# second site" in out2
    cfg["pocket"]["site"] = cfg["pocket"]["site"][:1]
    assert tomllib.loads(apply(out2, cfg)) == cfg


def test_new_table_and_new_key():
    cfg = tomllib.loads(DOC)
    cfg["poses"]["extra"] = [1, 2]
    cfg["gates"] = {"posebusters": True}
    out = apply(DOC, cfg)
    assert tomllib.loads(out) == cfg
    assert "# about pocket" in out


def test_type_change_is_written():
    cfg = tomllib.loads(DOC)
    cfg["cut"]["keep"] = True
    cfg["pocket"]["site"][0]["r"] = 4
    out = apply(DOC, cfg)
    assert tomllib.loads(out) == cfg
    assert isinstance(tomllib.loads(out)["cut"]["keep"], bool)


def test_unparseable_text_raises_with_the_line():
    with pytest.raises(ConfigTextError) as e:
        apply("a = 1\nb = \n", {"a": 1})
    assert e.value.line == 2
    assert isinstance(e.value, ValueError)


def test_set_value():
    out = set_value(DOC, "pocket.file", "inputs/abc.pdb")
    assert tomllib.loads(out)["pocket"]["file"] == "inputs/abc.pdb"
    assert out.replace("inputs/abc.pdb", "p.pdb") == DOC
    assert set_value(DOC, "pocket.file", "p.pdb") == DOC
    made = set_value(DOC, "gates.posebusters", True)
    assert tomllib.loads(made)["gates"] == {"posebusters": True}


def test_diff_lines():
    d = diff_lines("a\nb\nc\n", "a\nB\nc\n")
    assert d == [(" ", "a"), ("-", "b"), ("+", "B"), (" ", "c")]


def test_removing_a_key_keeps_the_rest():
    cfg = tomllib.loads(DOC)
    del cfg["poses"]["relax_unmapped"]
    out = apply(DOC, cfg)
    assert tomllib.loads(out) == cfg
    assert out == DOC.replace("relax_unmapped = true\n", "")
