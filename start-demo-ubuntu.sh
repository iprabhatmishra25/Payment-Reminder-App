#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python || ! -f apps/desktop/node_modules/electron/cli.js ]]; then
  python3 scripts/setup.py --demo
fi
exec python3 scripts/run_local.py --demo
