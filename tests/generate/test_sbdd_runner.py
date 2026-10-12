"""The runner's contract, tested without torch: bad input, failure reporting, helpers, independence from smeltery."""

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "src" / "smeltery" / "generate" / "_sbdd_runner.py"

GOOD_REQUEST = {
    "diffsbdd_dir": "/nonexistent/diffsbdd",
    "checkpoint": "/nonexistent.ckpt",
    "pdb": "/nonexistent.pdb",
    "ref_ligand": "/nonexistent.sdf",
    "fix_atoms": "/nonexistent_scaffold.sdf",
    "n_samples": 2,
    "add_n_nodes": 3,
    "timesteps": 5,
    "resamplings": 1,
    "center": "ligand",
    "seed": 1,
}


def _load_module():
    spec = importlib.util.spec_from_file_location("sbdd_runner_under_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args):
    return subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True, text=True)


def test_the_runner_imports_nothing_from_smeltery():
    tree = ast.parse(RUNNER.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
        if isinstance(node, ast.ImportFrom) and node.level:
            pytest.fail("the runner must not use relative imports: it runs outside the smeltery package")
    assert "smeltery" not in roots


def test_a_missing_argument_is_a_usage_error():
    proc = _run()
    assert proc.returncode == 2 and "usage" in proc.stderr.lower()


def test_a_request_missing_keys_names_them(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"seed": 1}))
    proc = _run(str(request))
    assert proc.returncode == 2
    assert "out_sdf" in proc.stderr and "checkpoint" in proc.stderr


def test_a_failed_run_writes_a_report_with_the_traceback_and_exits_1(tmp_path):
    request = tmp_path / "request.json"
    report = tmp_path / "report.json"
    request.write_text(json.dumps({**GOOD_REQUEST, "out_sdf": str(tmp_path / "out.sdf"), "out_report": str(report)}))
    proc = _run(str(request))
    assert proc.returncode == 1
    written = json.loads(report.read_text())
    assert written["ok"] is False and "Traceback" in written["error"]
    assert written["error"].strip().splitlines()[-1] in proc.stderr


def test_sha256_of_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "blob"
    path.write_bytes(b"x" * 3_000_000)
    assert _load_module().sha256_of(path) == hashlib.sha256(b"x" * 3_000_000).hexdigest()


def test_git_info_reports_the_commit_and_whether_tracked_files_changed(tmp_path):
    def git(*a):
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (tmp_path / "f.txt").write_text("one")
    git("add", "f.txt")
    git("commit", "-q", "-m", "m")
    module = _load_module()
    commit, dirty = module.git_info(tmp_path)
    assert commit and len(commit) == 40 and dirty is False
    (tmp_path / "f.txt").write_text("two")
    assert module.git_info(tmp_path)[1] is True
    (tmp_path / "untracked.txt").write_text("x")  # an untracked file does not make the checkout dirty
    git("checkout", "--", "f.txt")
    assert module.git_info(tmp_path)[1] is False
    assert module.git_info(tmp_path / "missing") == (None, False)
