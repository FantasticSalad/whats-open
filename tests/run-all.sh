#!/usr/bin/env bash
# Runs every test suite. Exit 0 only when all pass.
#
#   bash tests/run-all.sh
#
# pytest comes from, in order: WHATS_OPEN_TEST_PYTHON (a Python that has it), a .venv in the
# repo (CI makes one), uv with a pinned pytest, or the Python on PATH if it already has pytest.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
PYTEST_VERSION=8.3.5
export PYTHONDONTWRITEBYTECODE=1
status=0

# Git Bash stops converting /c/... paths for Windows programs when a path has an apostrophe in
# it, so hand Python a Windows-form path itself.
TESTS=$HERE
if command -v cygpath >/dev/null 2>&1; then TESTS=$(cygpath -m "$HERE"); fi

printf '\n### test_whats_open.py\n'
args=(-m pytest -p no:cacheprovider -q "$TESTS")
if [ -n "${WHATS_OPEN_TEST_PYTHON:-}" ]; then "$WHATS_OPEN_TEST_PYTHON" "${args[@]}" || status=1
elif [ -x "$ROOT/.venv/bin/python" ]; then "$ROOT/.venv/bin/python" "${args[@]}" || status=1
elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then "$ROOT/.venv/Scripts/python.exe" "${args[@]}" || status=1
elif command -v uv >/dev/null 2>&1; then uv run --no-project --with "pytest==$PYTEST_VERSION" python "${args[@]}" || status=1
else
  py=$(command -v python3 || command -v python) || { echo "No Python found."; exit 1; }
  "$py" "${args[@]}" || status=1
fi

printf '\n### public-scan.sh\n'
bash "$HERE/public-scan.sh" || status=1

if [ "$status" = 0 ]; then printf '\nAll suites passed.\n'; else printf '\nA suite failed.\n'; fi
exit "$status"
