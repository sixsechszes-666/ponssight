#!/usr/bin/env bash
# Every picture in docs/, from a running dashboard and nothing else.
#
#     python tools/seed_demo.py        # the synthetic database
#     python server.py                 # http://127.0.0.1:8787
#     bash tools/demo_shots.sh         # writes docs/shots/*.png
#
# The stills and the frames of the GIF come out of one pass. Two passes would
# be two chances for the numbers on screen to move between them, and a GIF
# whose tabs disagree with the stills below it is a GIF that reads as staged.
#
# Each state is a URL and nothing else - the tabs route off the hash, so a
# screenshot of the Snipers tab is a URL, not a click. Three are not: Handles
# is a lookup with no default question, Wallet has no address route on purpose
# and is reached by pressing view on a watchlist row, and Copy opens on a
# picker of all 48 tokens rather than on the plan form. Those three carry a
# setup script, and without it the picture is of a tab nobody asked anything.
set -u

BASE="${1:-http://127.0.0.1:8787}"
OUT="${2:-docs/shots}"
W="${W:-1600}"
H="${H:-1400}"
WAIT="${WAIT:-9000}"

# The token card is a fixed address, because the seeder is deterministic: token
# n is always 0x7000...<n>, so #16 is the same token every time this runs. The
# Wallet tab has no address of its own here - it has no route for one, and its
# picture is taken by pressing view on a watchlist row.
COIN="0x7000000000000000000000000000000000000010"

# Refuse to photograph a database that is not the demo one. This is not
# politeness: the indexer is on by default, so a server started without
# PONS_INDEX=0 pulls the real chain in behind the seed, and half an hour later
# the pictures are of real launches, real wallets and real money with nothing
# on screen to say so. That happened once while these pictures were being
# taken, and the only thing that caught it was reading a footer.
stats=$(curl -s --noproxy '*' "$BASE/api/stats" || true)
if [ -z "$stats" ]; then
  echo "no dashboard on $BASE - start it first" >&2
  exit 1
fi
read -r tokens block <<< "$(printf '%s' "$stats" | python -c \
  'import json,sys; d=json.load(sys.stdin); print(d.get("tokens"), d.get("latest_block"))')"
if [ "${tokens:-0}" -gt 300 ] || [ "${block:-null}" != "None" ]; then
  echo "refusing to shoot $BASE: $tokens tokens, latest_block=$block." >&2
  echo "That is a live index, not the seeded demo - run tools/seed_demo.py" >&2
  echo "and start the server with PONS_INDEX=0." >&2
  exit 1
fi
echo "shooting the demo database: $tokens tokens, indexer off"

# The top sniper, asked of the dashboard rather than written down here. The
# seeded wallets are deterministic, but which of them sorts first is a property
# of the sort rather than of the seeder, and an address written down that stops
# matching does not fail - it produces a picture of an empty tab, which reads
# as a tab that does not work.
SNIPER=$(curl -s --noproxy '*' "$BASE/api/snipers?limit=1" | python -c \
  'import json,sys; print(json.load(sys.stdin)["snipers"][0]["address"])' 2>/dev/null || true)
if [ -z "${SNIPER:-}" ]; then
  echo "no snipers to open on $BASE - the seed looks wrong" >&2
  exit 1
fi
echo "top sniper: $SNIPER"

# The list is built here rather than at the top on purpose: the shell expands
# `$SNIPER` and `$COIN` as this literal is read, and under `set -u` a name that
# is not set yet does not become an empty string, it kills the script. Both are
# known by now, and one of them is only knowable after asking the dashboard.
STATES=(
  "launches|#launches|"
  "volume|#volume|"
  "sniped|#sniped|"
  "snipers|#snipers|"
  "handles|#handles|@tools/pre_handles.js"
  "copy|#copy|@tools/pre_copy.js"
  "create|#create|"
  "wallet|#wallet|@tools/pre_wallet.js"
  "wallets|#wallets|@tools/pre_wallets.js"
  "sniper|#sniper/$SNIPER|"
  "coin|#launches/coin/$COIN|"
)

mkdir -p "$OUT"
fail=0
for state in "${STATES[@]}"; do
  IFS="|" read -r name hash pre <<< "$state"
  printf '=== %s\n' "$name"
  # shellcheck disable=SC2086
  node tools/shot.js "$BASE/$hash" "$OUT/$name.png" "$WAIT" "$W" "$H" $pre || fail=1
done

if [ "$fail" -ne 0 ]; then
  echo "one or more captures failed - the dashboard is still serving?" >&2
  exit 1
fi
echo "wrote $(ls -1 "$OUT"/*.png | wc -l) stills into $OUT"
