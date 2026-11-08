from smeltery.structure_check import check_pdb


def _atom(rec, serial, name, res, chain, seq, x, alt=" "):
    head = f"{rec:<6}{serial:>5} {name:<4}{alt}{res:>3} {chain}{seq:>4}    "
    return head + f"{x:>8.3f}{0:>8.3f}{0:>8.3f}  1.00  0.00           C"


PDB = "\n".join(
    [
        "REMARK 465",
        "REMARK 465 MISSING RESIDUES",
        "REMARK 465 THE FOLLOWING RESIDUES WERE NOT LOCATED IN THE",
        "REMARK 465   M RES C SSSEQI",
        "REMARK 465     MET A     1",
        "REMARK 465     GLY A     2",
        _atom("ATOM", 1, "CA", "ALA", "A", 3, 1.0),
        _atom("ATOM", 2, "CB", "ALA", "A", 3, 2.0, alt="A"),
        _atom("ATOM", 3, "CA", "ALA", "A", 4, 3.0),
        _atom("ATOM", 4, "CA", "ALA", "A", 8, 4.0),  # 5-7 absent
        _atom("ATOM", 5, "CA", "GLY", "B", 1, 5.0),
        _atom("HETATM", 6, "O", "HOH", "A", 101, 6.0),
        "ATOM      7  CA  ALA A  xx      1.000   0.000   0.000",  # bad residue number
    ]
)


def test_check_pdb_reports_chains_hetatm_gaps_missing_and_warnings():
    c = check_pdb(PDB)
    assert c.chains == {"A": 3, "B": 1}
    assert c.n_hetatm == 1
    assert c.missing_residues == 2
    assert c.possible_gaps == ["A 5-7"]
    assert any("1 ATOM/HETATM line" in w and "failed to parse" in w for w in c.warnings)
    assert any("alternate-location" in w for w in c.warnings)


def test_check_pdb_clean_file_has_no_warnings_and_no_remark_465_is_none():
    c = check_pdb(_atom("ATOM", 1, "CA", "ALA", "A", 1, 1.0) + "\n" + _atom("ATOM", 2, "CA", "ALA", "A", 2, 2.0))
    assert c.warnings == [] and c.possible_gaps == [] and c.missing_residues is None and c.n_hetatm == 0


def test_remark_465_header_without_rows_means_zero_missing():
    c = check_pdb("REMARK 465   M RES C SSSEQI\n" + _atom("ATOM", 1, "CA", "ALA", "A", 1, 1.0))
    assert c.missing_residues == 0


def test_hetatm_residue_inside_a_chain_is_not_a_gap():
    pdb = "\n".join(
        [_atom("ATOM", 1, "CA", "ALA", "A", 44, 1.0), _atom("HETATM", 2, "SE", "MSE", "A", 45, 2.0)]
        + [_atom("ATOM", 3, "CA", "ALA", "A", 46, 3.0)]
    )
    c = check_pdb(pdb)
    assert c.possible_gaps == [] and c.n_hetatm == 1


def test_remark_465_rows_with_insertion_codes_are_counted():
    c = check_pdb("REMARK 465   M RES C SSSEQI\nREMARK 465     SER A    52A\nREMARK 465     GLY A    53\n")
    assert c.missing_residues == 2


def test_only_the_first_model_is_counted():
    model = _atom("ATOM", 1, "CA", "ALA", "A", 1, 1.0, alt="A") + "\n" + _atom("HETATM", 2, "O", "HOH", "A", 9, 2.0)
    c = check_pdb(f"MODEL 1\n{model}\nENDMDL\nMODEL 2\n{model}\nENDMDL")
    assert c.n_hetatm == 1 and c.warnings == [
        "1 atom record(s) carry an alternate-location flag; only one conformer is used"
    ]


def test_a_gap_that_is_only_residue_zero_is_not_reported():
    pdb = _atom("ATOM", 1, "CA", "ALA", "A", -1, 1.0) + "\n" + _atom("ATOM", 2, "CA", "ALA", "A", 1, 2.0)
    assert check_pdb(pdb).possible_gaps == []
