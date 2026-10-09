#!/usr/bin/env bash
# Run the test suite where ferric cannot be built (cloud sandboxes).
#
# Uses a venv with only rdkit, numpy, scipy, pytest and sets SMELTERY_NO_FERRIC=1,
# so tests/conftest.py installs a clearly-labelled ferric STUB and skips every
# test marked `needs_ferric`. This is NOT a substitute for CI, which runs the
# whole suite against the real engine. Exits non-zero on any real failure.
set -uo pipefail
cd "$(dirname "$0")/.."

VENV="${SMELTERY_DEV_VENV:-.venv}"
if [ ! -x "$VENV/bin/python" ]; then
  uv venv --python ">=3.11,<3.13" "$VENV" >/dev/null || exit 2
fi
uv pip install --quiet --python "$VENV/bin/python" "rdkit>=2024.3" "numpy>=1.26" scipy pytest || exit 2
# smeltery itself, without its dependencies (so no ferric): record.py reads its package metadata.
uv pip install --quiet --no-deps --python "$VENV/bin/python" -e . || exit 2

REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT
SMELTERY_NO_FERRIC=1 OPENBLAS_NUM_THREADS=1 \
  "$VENV/bin/python" -m pytest -q -rs -p no:cacheprovider "$@" 2>&1 | tee "$REPORT"
rc=${PIPESTATUS[0]}

python3 - "$REPORT" "$rc" <<'PY'
import re, sys
text, rc = open(sys.argv[1]).read(), int(sys.argv[2])
last = text.strip().splitlines()[-1]
n = lambda w: sum(int(c) for c, k in re.findall(r"(\d+) (\w+)", last) if k.startswith(w))
ferric = sum(int(c) for c in re.findall(r"^SKIPPED \[(\d+)\] .*needs the ferric engine", text, re.M))
failed = n("failed") + n("error")
print("\n== dev-check (ferric STUB, not the real engine) ==")
print(f"passed: {n('passed')}  skipped-needs-ferric: {ferric}  "
      f"skipped-other: {n('skipped') - ferric}  failed: {failed}")
sys.exit(1 if failed or rc not in (0,) else 0)
PY
