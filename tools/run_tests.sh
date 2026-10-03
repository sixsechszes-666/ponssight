#!/usr/bin/env bash
# Every test file, from the repository root.
#
#     bash tools/run_tests.sh            # all of them
#     bash tools/run_tests.sh snipe      # only tests/test_snipe.py
#
# These are not pytest modules and are not meant to be. Each file is one script
# that runs its cases top to bottom, prints a line per case and exits non-zero
# on the first failure it records - a shape you can run on its own without a
# runner, which matters on a project whose tests are read as much as run. The
# cost is that `pytest tests/` collects nothing and dies on the `sys.exit` at
# the bottom of the first file, so this is the runner.
#
# They import `config`, `store` and friends by plain name and put "." on the
# path themselves, which means they expect the working directory to be the
# repository root. That is why this script resolves its own directory instead
# of trusting wherever it was called from.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FILTER="${1:-}"
pass=0
fail=0

for f in tests/test_*.py; do
  name="$(basename "$f" .py)"
  if [ -n "$FILTER" ] && [[ "$name" != *"$FILTER"* ]]; then
    continue
  fi
  printf '\n=== %s\n' "$name"
  if python "$f"; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    printf '^ %s FAILED\n' "$name"
  fi
done

printf '\n%d files passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
