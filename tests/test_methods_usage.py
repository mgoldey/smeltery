"""`smeltery methods`: every registered method as a TOML block, derived from the registry.

The point of the generator is that it cannot drift: a method registered tomorrow documents itself, and a knob
renamed in a tier is renamed here. These tests pin that, not the prose.
"""

from __future__ import annotations

import tomllib

import pytest

from smeltery.cli import main
from smeltery.gui.catalog import method_from_spec
from smeltery.methods import MethodSpec, register, registry, unregister
from smeltery.methods.usage import _EXTRA_KNOBS, all_methods_toml, method_toml, write_methods_toml


def test_the_whole_document_is_valid_toml_with_a_section_per_method():
    """Valid TOML matters: a block is meant to be pasted into a pipeline config."""
    text = all_methods_toml()
    parsed = tomllib.loads(text)
    for spec in registry().values():
        if spec.section:
            assert spec.section in parsed, spec.key


def test_every_knob_of_every_method_appears_with_its_type():
    """A knob the config accepts but the document omits is the failure this prevents. Read each method's own block:
    knob names repeat across methods (several take `file`)."""
    for spec in registry().values():
        block = method_toml(spec)
        for p in method_from_spec(spec).params:
            state = "REQUIRED" if p.required else ("optional" if p.optional or p.default is None else "default")
            lines = [ln for ln in block.splitlines() if ln.lstrip("# ").startswith(f"{p.name} = ")]
            assert len(lines) == 1, f"{spec.key}.{p.name}: {lines}"
            assert p.kind in lines[0] and state in lines[0], lines[0]


def test_a_required_knob_is_uncommented_and_an_unset_one_is_commented():
    """Paste-and-edit: what must be set is already there, what is optional is a comment."""
    block = method_toml(registry()["structure"])
    assert "spec = " in block and not block.split("spec = ")[0].rstrip().endswith("#")
    assert "# chains = " in block  # optional, no default


def test_a_config_only_knob_with_no_gui_widget_is_still_documented():
    """`remedies` has no form widget, so the catalog leaves it out; a usage document must not."""
    block = method_toml(registry()["field_interaction"])
    assert "remedies" in block and "adiis" in block and "level_shift" in block
    assert "refused" in block  # why a rung may not loosen a threshold


def test_facts_come_from_the_registration_not_from_prose():
    block = method_toml(registry()["field_interaction"])
    assert "measures dE_int" in block and "Needs: poses, field" in block
    assert "Refused beside: field_dft_interaction" in block
    assert "Required: every pipeline has this section" in block


def test_a_newly_registered_method_documents_itself():
    """The whole point: no hand-maintained list to update."""
    spec = MethodSpec(
        key="usage_probe",
        label="Usage probe",
        rung="input",
        role="prep",
        step="active_site",
        section="usage_probe",
        summary="a method registered by this test",
        params=(),
        order=999,
    )
    register(spec)
    try:
        text = all_methods_toml()
        assert "[usage_probe]" in text and "a method registered by this test" in text
        assert "# no knobs" in method_toml(spec)
        tomllib.loads(text)
    finally:
        unregister("usage_probe")
    assert "[usage_probe]" not in all_methods_toml()


def test_out_writes_an_index_and_one_file_per_method(tmp_path):
    paths = write_methods_toml(tmp_path)
    assert paths[0].name == "index.toml"
    assert len(paths) == 1 + len(registry())
    for spec in registry().values():
        p = tmp_path / f"{spec.key}.toml"
        assert p.is_file()
        tomllib.loads(p.read_text())


def test_the_cli_prints_one_method_lists_keys_on_a_bad_one_and_writes_a_directory(tmp_path, capsys):
    assert main(["methods", "--only", "field_interaction"]) == 0
    assert "[field_interaction]" in capsys.readouterr().out
    assert main(["methods", "--only", "no_such_method"]) == 2
    err = capsys.readouterr().err
    assert "no method 'no_such_method'" in err and "field_interaction" in err
    assert main(["methods", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "index.toml").is_file()
    assert main(["methods"]) == 0
    assert "[cut]" in capsys.readouterr().out


def test_every_extra_knob_names_a_registered_method_and_a_knob_the_catalog_omits():
    """A stale entry in `_EXTRA_KNOBS` would document a knob that no longer exists."""
    for key, (name, _example, _note) in _EXTRA_KNOBS.items():
        spec = registry().get(key)
        assert spec is not None, key
        assert name not in {p.name for p in method_from_spec(spec).params}, f"{key}.{name} is a catalog param now"


@pytest.mark.parametrize("key", ["bound_pose", "charge_sensitivity"])
def test_the_methods_added_for_the_danuglipron_pipeline_are_documented(key):
    block = method_toml(registry()[key])
    assert f"[{key}]" in block
    tomllib.loads(block)
