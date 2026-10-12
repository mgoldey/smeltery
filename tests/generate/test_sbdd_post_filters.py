"""Filtering, deduplication and candidate building over the real fixture, with its measured counts pinned."""

from collections import Counter

import pytest
from rdkit import Chem

from smeltery.generate.sbdd_post import FilterConfig, first_forbidden, process_samples

SURVIVOR = 12


def _reasons(rejected):
    return dict(Counter(r.reason for r in rejected))


def test_default_filters_on_the_fixture_match_the_measurement(raw_samples, scaffold_spec):
    candidates, rejected = process_samples(raw_samples, scaffold_spec, FilterConfig())
    assert len(candidates) == 1
    assert _reasons(rejected) == {"unsanitizable": 12, "scaffold_lost": 3}
    assert len(candidates) + len(rejected) == len(raw_samples)


def test_topology_matching_admits_the_three_that_changed_the_scaffolds_bond_orders(raw_samples, scaffold_spec):
    candidates, rejected = process_samples(raw_samples, scaffold_spec, FilterConfig(scaffold_match="topology"))
    assert len(candidates) == 4
    assert _reasons(rejected) == {"unsanitizable": 12}


def test_with_every_optional_filter_off_only_sanitization_removes_samples(raw_samples, scaffold_spec):
    config = FilterConfig(require_connected=False, scaffold_match="off", forbidden_smarts={}, deduplicate=False)
    candidates, rejected = process_samples(raw_samples, scaffold_spec, config)
    assert len(candidates) == 4
    assert _reasons(rejected) == {"unsanitizable": 12}


def test_every_returned_candidate_is_connected_sanitized_and_contains_the_scaffold(raw_samples, scaffold_spec):
    candidates, _ = process_samples(raw_samples, scaffold_spec, FilterConfig())
    for cand in candidates:
        mol = Chem.MolFromSmiles(cand.smiles)
        assert mol is not None and len(Chem.GetMolFrags(mol)) == 1
        assert mol.HasSubstructMatch(scaffold_spec.scaffold)


def test_a_forbidden_substructure_rejects_with_its_label(raw_samples, scaffold_spec):
    config = FilterConfig(forbidden_smarts={"phosphorus": "[P]"})  # the survivor carries a phosphonate
    candidates, rejected = process_samples(raw_samples, scaffold_spec, config)
    assert candidates == []
    hit = [r for r in rejected if r.reason == "forbidden_substructure"]
    assert len(hit) == 1 and hit[0].detail == "phosphorus" and hit[0].index == SURVIVOR and hit[0].smiles


def test_the_default_forbidden_list_catches_a_peroxide_and_not_an_ether():
    from smeltery.generate.sbdd_post import DEFAULT_FORBIDDEN

    assert first_forbidden(Chem.MolFromSmiles("COOC"), DEFAULT_FORBIDDEN) == "peroxide"
    assert first_forbidden(Chem.MolFromSmiles("COC"), DEFAULT_FORBIDDEN) is None
    assert first_forbidden(Chem.MolFromSmiles("COOC"), {}) is None


def test_an_unreadable_record_is_one_counted_rejection(raw_samples, scaffold_spec):
    """Review focus 3."""
    candidates, rejected = process_samples([None, raw_samples[SURVIVOR]], scaffold_spec, FilterConfig())
    assert len(candidates) == 1
    assert [(r.index, r.reason) for r in rejected] == [(0, "unsanitizable")]
    assert "unreadable" in rejected[0].detail


def test_an_empty_input_is_an_empty_result(scaffold_spec):
    assert process_samples([], scaffold_spec, FilterConfig()) == ([], [])


def test_the_same_molecule_twice_is_a_duplicate(raw_samples, scaffold_spec):
    """Review focus 5, within a batch."""
    twice = [raw_samples[SURVIVOR], raw_samples[SURVIVOR]]
    candidates, rejected = process_samples(twice, scaffold_spec, FilterConfig())
    assert len(candidates) == 1 and _reasons(rejected) == {"duplicate": 1}
    candidates, rejected = process_samples(twice, scaffold_spec, FilterConfig(deduplicate=False))
    assert len(candidates) == 2 and rejected == []


def test_duplicates_are_found_across_calls_through_the_shared_seen_set(raw_samples, scaffold_spec):
    """Review focus 5, across batches; indices and names continue where the caller says."""
    seen: set[str] = set()
    first, _ = process_samples([raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(), seen=seen)
    second, rejected = process_samples(
        [raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(), seen=seen, start_index=10, first_number=len(first)
    )
    assert second == [] and [(r.index, r.reason) for r in rejected] == [(10, "duplicate")]
    assert len(seen) == 1


def test_a_candidate_that_fails_the_size_filter_does_not_poison_the_seen_set(raw_samples, scaffold_spec):
    seen: set[str] = set()
    _, rejected = process_samples([raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(max_heavy_atoms=40), seen=seen)
    assert _reasons(rejected) == {"size": 1} and seen == set()
    _, rejected = process_samples([raw_samples[SURVIVOR]], scaffold_spec, FilterConfig(min_heavy_atoms=42))
    assert _reasons(rejected) == {"size": 1}


def test_candidates_are_named_in_order_from_the_given_number(raw_samples, scaffold_spec):
    candidates, _ = process_samples(
        raw_samples, scaffold_spec, FilterConfig(scaffold_match="topology"), name_prefix="g", first_number=7
    )
    assert [c.name for c in candidates] == ["g-0007", "g-0008", "g-0009", "g-0010"]


def test_embed_mode_returns_smiles_only_and_model_mode_adds_a_pose_in_smiles_order(raw_samples, scaffold_spec):
    embed, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="embed")
    assert embed[0].poses == []
    model, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="model")
    cand = model[0]
    assert cand.smiles == embed[0].smiles and len(cand.poses) == 1
    reference = Chem.AddHs(Chem.MolFromSmiles(cand.smiles))
    assert cand.poses[0].symbols == tuple(a.GetSymbol() for a in reference.GetAtoms())


def test_an_unknown_pose_source_is_refused(raw_samples, scaffold_spec):
    with pytest.raises(ValueError, match="pose_source"):
        process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="docked")


def test_filter_config_refuses_nonsense():
    with pytest.raises(ValueError, match="scaffold_match"):
        FilterConfig(scaffold_match="roughly")
    with pytest.raises(ValueError, match="not valid SMARTS"):
        FilterConfig(forbidden_smarts={"bad": "[C"})
    assert FilterConfig().forbidden_smarts == {"peroxide": "[OX2][OX2]"}
    assert FilterConfig().forbidden_smarts is not FilterConfig().forbidden_smarts  # not a shared mutable default
