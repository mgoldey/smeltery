"""The Methods tab over HTTP: every wired method with its settings explained, workspace defaults, and who uses it."""

import sys
import tomllib
import types
from pathlib import Path

from smeltery.gui.catalog import catalog
from smeltery.methods import registry

BLANK = {"campaign": "cdk2", "parent": "a", "candidates": {"a": "CCO", "b": "CCN"}}


def _methods(c) -> dict:
    r = c.get("/api/methods")
    assert r.status_code == 200, r.text
    return {m["key"]: m for m in r.json()}


def test_one_entry_per_wired_method_with_every_param_explained(client_for):
    c, _ = client_for()
    ms = _methods(c)
    assert set(ms) == {k for k, m in catalog().items() if m.wired}
    for key, m in ms.items():
        assert m["label"] and set(m) >= {"summary", "quantity", "cost_model", "memory_model", "floor", "used_by"}
        assert [p["name"] for p in m["params"]] == [p.name for p in catalog()[key].params]
        for p in m["params"]:
            assert p["help"].strip() and p["workspace_default"] is None
            assert set(p) >= {"name", "kind", "default", "required", "choices"}


def test_the_dft_entry_carries_the_models_the_code_has(client_for):
    c, _ = client_for()
    m = _methods(c)["field_dft_interaction"]
    assert m["quantity"] == "dE_int" and m["floor"] is None
    assert "STO-3G" in m["memory_model"] and "PBE" in m["cost_model"]
    basis = next(p for p in m["params"] if p["name"] == "basis")
    assert basis["default"] == "sto-3g"


def test_a_model_that_cannot_say_says_so(client_for):
    c, _ = client_for()
    assert _methods(c)["docking"]["cost_model"].startswith("cannot say")


def test_a_workspace_default_is_stored_in_methods_toml_and_listed(client_for):
    c, ports = client_for()
    r = c.put("/api/methods/field_dft_interaction", json={"defaults": {"basis": "def2-svp"}})
    assert r.status_code == 200, r.text
    basis = next(p for p in r.json()["params"] if p["name"] == "basis")
    assert basis["workspace_default"] == "def2-svp" and basis["default"] == "sto-3g"
    text = Path(ports["settings"].root, ".smeltery", "bench", "methods.toml").read_text()
    assert tomllib.loads(text) == {"field_dft_interaction": {"basis": "def2-svp"}}
    again = next(p for p in _methods(c)["field_dft_interaction"]["params"] if p["name"] == "basis")
    assert again["workspace_default"] == "def2-svp"


def test_an_empty_default_clears_it(client_for):
    c, _ = client_for()
    c.put("/api/methods/cut", json={"defaults": {"z": 3.0}})
    c.put("/api/methods/cut", json={"defaults": {}})
    z = next(p for p in _methods(c)["cut"]["params"] if p["name"] == "z")
    assert z["workspace_default"] is None


def test_a_default_the_method_would_refuse_is_400_and_stores_nothing(client_for):
    c, ports = client_for()
    for key, body in (
        ("cut", {"nope": 1}),
        ("cut", {"z": "wide"}),
        ("forcefield", {"max_strain_kcal": True}),
        ("structure", {"provider": "ftp"}),
    ):
        r = c.put(f"/api/methods/{key}", json={"defaults": body})
        assert r.status_code == 400, (key, body, r.text)
    assert not Path(ports["settings"].root, ".smeltery", "bench", "methods.toml").exists()


def test_unknown_and_unwired_methods_are_404_and_400(client_for):
    c, _ = client_for()
    assert c.put("/api/methods/nope", json={"defaults": {}}).status_code == 404
    assert c.put("/api/methods/qmmm", json={"defaults": {}}).status_code == 400


def test_used_by_lists_the_workspace_pipelines_that_place_the_method(client_for):
    c, _ = client_for()
    assert all(m["used_by"] == [] for m in _methods(c).values())
    for name in ("alpha", "beta"):
        assert c.post("/api/pipelines", json={"name": name, "blank": BLANK}).status_code == 201
    ms = _methods(c)
    assert ms["cut"]["used_by"] == ["alpha", "beta"] and ms["field_interaction"]["used_by"] == ["alpha", "beta"]
    assert ms["field_dft_interaction"]["used_by"] == [] and ms["docking"]["used_by"] == []


def test_a_pipeline_that_does_not_parse_is_skipped_not_fatal(client_for):
    c, ports = client_for()
    made = c.post("/api/pipelines", json={"name": "ok", "blank": BLANK}).json()
    broken = c.post("/api/pipelines", json={"name": "broken", "blank": BLANK}).json()
    Path(broken["folder"], "config.toml").write_text("not = = toml")
    assert made["name"] == "ok"
    assert _methods(c)["cut"]["used_by"] == ["ok"]


def test_a_hand_broken_methods_file_is_a_409_naming_it_not_a_500(client_for):
    c, ports = client_for()
    f = Path(ports["settings"].root, ".smeltery", "bench", "methods.toml")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("[cut]\nz = = 1\n")
    for r in (c.get("/api/methods"), c.put("/api/methods/cut", json={"defaults": {"z": 3.0}})):
        assert r.status_code == 409 and "methods.toml" in r.json()["detail"] and "line 2" in r.json()["detail"]


def _edit(c, toml, op, key, **kw):
    r = c.post("/api/pipeline/edit", json={"toml": toml, "op": op, "key": key, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_workspace_default_is_copied_into_a_method_added_through_the_edit_route(client_for):
    from smeltery.gui._toml import dumps
    from smeltery.gui.pipeline import Pipeline

    c, _ = client_for()
    assert c.put("/api/methods/field_dft_interaction", json={"defaults": {"basis": "def2-svp"}}).status_code == 200
    toml = dumps(Pipeline.minimal("cdk2", "a", {"a": "CCO", "b": "CCN"}).to_config())
    toml = _edit(c, toml, "remove", "field_interaction")["toml"]
    added = _edit(c, toml, "add", "field_dft_interaction")
    assert tomllib.loads(added["toml"])["field_dft_interaction"] == {"basis": "def2-svp"}
    # an explicit value in the request beats the default
    explicit = _edit(c, toml, "add", "field_dft_interaction", params={"basis": "sto-3g"})
    assert tomllib.loads(explicit["toml"])["field_dft_interaction"] == {"basis": "sto-3g"}
    # copied, not referenced: clearing the default leaves the already-added stage as it was
    assert c.put("/api/methods/field_dft_interaction", json={"defaults": {}}).status_code == 200
    assert tomllib.loads(added["toml"])["field_dft_interaction"] == {"basis": "def2-svp"}
    bare = _edit(c, toml, "add", "field_dft_interaction")
    assert tomllib.loads(bare["toml"])["field_dft_interaction"] == {}


def test_floor_provenance_and_tier_doc_are_reported(client_for):
    c, _ = client_for()
    m = _methods(c)["field_dft_interaction"]
    assert m["floor_status"] == "UNMEASURED"
    assert m["tier_doc"] == "docs/tiers/field_dft_interaction.md"


def _doc_example(monkeypatch) -> dict:
    """Run the 'Registering from outside the package' code block of docs/methods.md; return its namespace."""
    import re

    docs = Path(__file__).resolve().parents[2] / "docs" / "methods.md"
    section = docs.read_text().split("### Registering from outside the package", 1)[1]
    code = re.search(r"```python\n(.*?)```", section, re.S).group(1)
    mod = types.ModuleType("doc_echo_method")  # dataclasses resolve annotations through sys.modules
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    exec(compile(code, "docs/methods.md", "exec"), mod.__dict__)
    return mod.__dict__


def test_a_method_registered_from_outside_the_package_reaches_the_methods_api(client_for, request, monkeypatch):
    ns = _doc_example(monkeypatch)
    ns["install"]()
    request.addfinalizer(lambda: "echo" in registry() and ns["uninstall"]())
    c, ports = client_for()

    echo = _methods(c)["echo"]
    assert echo["label"] == "Echo" and echo["cost_model"] == "negligible: no SCF"
    params = {p["name"]: p for p in echo["params"]}
    assert params["label"]["default"] == "hello" and params["repeat"]["default"] == 2
    assert all(p["workspace_default"] is None for p in params.values())

    r = c.put("/api/methods/echo", json={"defaults": {"repeat": 5}})
    assert r.status_code == 200, r.text
    assert {p["name"]: p["workspace_default"] for p in r.json()["params"]} == {"label": None, "repeat": 5}
    text = Path(ports["settings"].root, ".smeltery", "bench", "methods.toml").read_text()
    assert tomllib.loads(text) == {"echo": {"repeat": 5}}
    bad = c.put("/api/methods/echo", json={"defaults": {"nope": 1}})
    assert bad.status_code == 400

    ns["uninstall"]()
    assert "echo" not in _methods(c)
    assert c.put("/api/methods/echo", json={"defaults": {}}).status_code == 404
