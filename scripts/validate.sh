#!/usr/bin/env bash
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project"
PYTHONPATH=src python3 -m unittest discover -s tests
(cd examples/ledger && python3 -m unittest)
python3 -m compileall -q src
for script in scripts/*.sh config/completion.bash; do bash -n "$script"; done
if [[ -x .venv/bin/ruff ]]; then .venv/bin/ruff check src tests --select F; fi
