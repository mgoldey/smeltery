"""The reusable workflows smeltery calls from mgoldey/attestation (roadmap 39) are pinned, and pinned together.

A called workflow runs with this repository's token and checkout, so a moving ref (a branch or a tag) would let a
change in another repository alter what smeltery's CI and release run. Every `uses:` of an attestation reusable
workflow must therefore name a full 40-hex commit SHA, and all of them the SAME one: the three templates are one
release of attestation's CI, and a half-bumped set mixes interfaces. Read as text (not yaml) so the test needs no
dependency the `test` job may lack. Spec: attestation's docs/superpowers/specs/2026-11-20-shared-ci-templates-design.md.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
REF = re.compile(r"^\s*uses:\s*mgoldey/attestation/\.github/workflows/(reusable-[\w-]+\.yml)@(\S+)", re.MULTILINE)


def _refs() -> list[tuple[str, str, str]]:
    return [(p.name, m[1], m[2]) for p in sorted(WORKFLOWS.glob("*.yml")) for m in REF.finditer(p.read_text())]


def test_attestation_reusable_workflows_are_full_sha_pinned_to_one_commit():
    refs = _refs()
    assert refs, "no workflow calls attestation's reusable workflows"
    for wf, template, ref in refs:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{wf}: {template}@{ref} is not a full commit SHA"
    assert len({ref for *_, ref in refs}) == 1, f"attestation reusable workflows pinned to different SHAs: {refs}"


def test_the_converted_jobs_call_the_templates():
    calls = {(wf, template) for wf, template, _ in _refs()}
    assert calls == {
        ("test.yml", "reusable-pre-commit.yml"),
        ("test.yml", "reusable-python-gates.yml"),
        ("test.yml", "reusable-wheel-smoke.yml"),
        ("release.yml", "reusable-wheel-smoke.yml"),
    }


def test_publishing_stays_top_level_in_release_yml():
    """PyPI trusted publishing does not work from a reusable workflow, and the PyPI publisher names release.yml."""
    text = (WORKFLOWS / "release.yml").read_text()
    publish = text[text.index("\n  publish:") :]
    assert "needs: build" in publish and "id-token: write" in publish and "name: pypi" in publish
    assert re.search(r"uses:\s*pypa/gh-action-pypi-publish@[0-9a-f]{40}", publish)


def test_called_jobs_set_openblas_num_threads_themselves():
    """Workflow-level env does not reach a called workflow: the jobs that need OPENBLAS_NUM_THREADS set it."""
    text = (WORKFLOWS / "test.yml").read_text()
    assert 'echo "OPENBLAS_NUM_THREADS=1" >> "$GITHUB_ENV"' in text  # the `test` job's setup
    assert text.count("export OPENBLAS_NUM_THREADS=1") == 1  # the `wheel` job's smoke command
    assert (WORKFLOWS / "release.yml").read_text().count("export OPENBLAS_NUM_THREADS=1") == 1
