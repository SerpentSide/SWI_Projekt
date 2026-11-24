#!/usr/bin/env bash
# One command for the whole automated check (C09): dependencies, backend tests incl. the real SQLite
# integration tests, and the frontend type check + production build. Used by CI (.github/workflows/ci.yml)
# and runnable locally from a clean checkout:
#
#   ./scripts/check.sh            # everything
#   SKIP_FRONTEND=1 ./scripts/check.sh   # backend only (no Node.js needed)
#
# Needs: Python >= 3.11 (3.13 used in CI), and for the frontend Node.js >= 20 with npm.
# Writes test reports to build/ (junit XML + plain log). Exit code != 0 when any step fails.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-.venv}"
mkdir -p build

echo "== commit:  $(git rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout')"
echo "== python:  $("$PYTHON" --version)"

if [ ! -x "$VENV/bin/python" ]; then
  echo "== creating $VENV"
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q -r requirements.txt -r requirements-dev.txt
echo "== sqlite:  $("$VENV/bin/python" -c 'import sqlite3; print(sqlite3.sqlite_version)')"
"$VENV/bin/python" -m pip freeze > build/pip-freeze.txt   # the resolved dependency versions of this run

echo "== backend tests (unit + API/SQLite integration), report: build/pytest-junit.xml"
"$VENV/bin/python" -m pytest -v -p no:cacheprovider --junitxml=build/pytest-junit.xml 2>&1 | tee build/pytest.log
# tee hides pytest's exit code; pipefail keeps it.

if [ "${SKIP_FRONTEND:-0}" != "1" ]; then
  echo "== frontend: npm ci, type check + build (src/frontend)"
  echo "== node:    $(node --version)"
  npm --prefix src/frontend ci --no-audit --no-fund
  npm --prefix src/frontend run build
fi

echo "== ALL CHECKS PASSED"
