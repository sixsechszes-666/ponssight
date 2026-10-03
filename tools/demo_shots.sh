#!/usr/bin/env bash
# Every picture in docs/, from a running dashboard and nothing else.
#
#     python server.py                 # http://127.0.0.1:8787
#     bash tools/demo_shots.sh         # writes docs/shots/*.png
#
# The stills and the frames of the GIF come out of one pass. Two passes would
# be two chances for the numbers on screen to move between them, and a GIF
# whose tabs disagree with the stills below it is a GIF that reads as staged.
# Against a live index the indexer is worth stopping for the pass
# (`PONS_INDEX=0 python server.py`) for the same reason taken further: the
# header counts launches and the footer counts blocks, and a chain that is
# moving while eleven frames are being taken is a chain that will be in a
# different place in the first one and the last.
#
# Each state is a URL and nothing else - the tabs route off the hash, so a
# screenshot of the Snipers tab is a URL, not a click - and four of them need
# more than the URL. Volume opens on an hour that may hold nothing, the coin
# card opens on a range that may hold nothing, Handles is a lookup with no
# default question, and Wallet has no address route on purpose and is reached by
# pointing the tab at a wallet. Those four carry a setup script that does the
# asking, and without it the picture is of a tab nobody asked anything.
#
# Copy is the fifth and a different case: it does take its subject as a route
# argument (`#copy/<address>`, same as the coin card), so the form fills itself
# from the chain. What the URL cannot do is wait, and two panels on that tab
# arrive late - the source line and the factory's fee table. Its script is a
# wait, not a click.
#
# ------------------------------------------------------------ which base
#
# The script asks the dashboard what is behind it and shoots accordingly,
# because the same eleven pictures have to work against two very different
# databases:
#
#   the seeded demo (tools/seed_demo.py) is 48 tokens with a watchlist and a
#   vault under `demo-passphrase`, and everything in it is invented;
#
#   a live index is whatever the chain has, in this repository's own case six
#   hundred thousand launches, and the wallets in it are the operator's.
#
# The first is what the tests and the CI run against. The second is what the
# README shows, because a demo of a chain index that is not indexing a chain is
# a screenshot of a fixture. So the choice is made by looking, not by a flag
# somebody has to remember: more than three hundred tokens, or a non-null
# `latest_block`, and the pictures come out of the real index with the real
# numbers on them.
#
# The addresses that cannot be written down in advance - which token to open,
# which wallet to view, which handle to look up - are asked of the API in that
# mode, because a hardcoded address that stops matching does not fail, it
# produces a picture of an empty tab, which reads as a tab that does not work.
set -u

BASE="${1:-http://127.0.0.1:8787}"
OUT="${2:-docs/shots}"
W="${W:-1600}"
H="${H:-1400}"
WAIT="${WAIT:-9000}"
PAUSE="${PAUSE:-2}"          # between frames, so the last one's requests drain

stats=$(curl -s --noproxy '*' "$BASE/api/stats" || true)
if [ -z "$stats" ]; then
  echo "no dashboard on $BASE - start it first" >&2
  exit 1
fi
read -r tokens block <<< "$(printf '%s' "$stats" | python -c \
  'import json,sys; d=json.load(sys.stdin); print(d.get("tokens"), d.get("latest_block"))')"

if [ "${tokens:-0}" -gt 300 ] || [ "${block:-null}" != "None" ]; then
  MODE=live
else
  MODE=demo
fi

# The top sniper, asked of the dashboard rather than written down here, in both
# modes: the seeded wallets are deterministic but which of them sorts first is
# a property of the sort rather than of the seeder.
SNIPER=$(curl -s --noproxy '*' "$BASE/api/snipers?limit=1" | python -c \
  'import json,sys; print(json.load(sys.stdin)["snipers"][0]["address"])' 2>/dev/null || true)
if [ -z "${SNIPER:-}" ]; then
  echo "no snipers to open on $BASE - the index looks wrong" >&2
  exit 1
fi

if [ "$MODE" = live ]; then
  echo "shooting a live index: $tokens tokens, latest_block=$block"
  # The card is opened on the first token of the volume board that has a price
  # history to draw. A graduated token sounds like the better subject and is
  # not: the ones that graduated did it before the candle walk had reached
  # them, so their chart is a single point and the card's price panel reads "no
  # trades in this window", which is the one thing on it that looks broken.
  # Which token that is cannot be written down here - it is a property of the
  # index - so it is asked for, and the two fallbacks below are for the day
  # nothing has a history yet.
  #
  # The proxy is switched off inside the python rather than in the shell: the
  # curl calls above take `--noproxy '*'` for the same reason, and a local
  # proxy that rewrites loopback would otherwise answer for the dashboard.
  COIN=$(python -c '
import json, sys, urllib.request
base = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def get(path):
    with opener.open(base + path, timeout=120) as r:
        return json.load(r)

def leader(query):
    rows = get("/api/volume?window=all&" + query)["tokens"]
    return rows[0]["address"] if rows else ""

coin = ""
try:
    for t in get("/api/volume?window=all&limit=25")["tokens"]:
        chart = get("/api/token/%s/card?hours=0" % t["address"]).get("chart") or {}
        if len(chart.get("points") or []) > 50:
            coin = t["address"]
            break
except Exception:
    coin = ""
print(coin or leader("graduated=true&limit=1") or leader("limit=1"))
' "$BASE" 2>/dev/null || true)
  # The trade walk fills the time buckets behind the tip, so the short windows
  # can be legitimately empty for hours after a restart. `all` reads the running
  # totals, which are there as soon as the first trade is indexed, and it is
  # also the range the coin card is opened in, for the same reason.
  WINDOW="${WINDOW:-all}"
  # An account with a real launch trail in this index. Overridable because the
  # next index will not have this one.
  HANDLE="${HANDLE:-robinhoodapp}"
  # The wallet the tour opens is the sniper it just showed: a real address, with
  # a real balance and a real trail of early buys, and nobody's private list.
  WADDR="$SNIPER"
  WALLETS_PRE=""
else
  echo "shooting the seeded demo: $tokens tokens, indexer off"
  # Token n of the seed is always 0x7000...<n>, so #16 is the same token every
  # time this runs.
  COIN="0x7000000000000000000000000000000000000010"
  WINDOW=""
  HANDLE=""
  WADDR=""
  WALLETS_PRE="@tools/pre_wallets.js"
fi

if [ -z "${COIN:-}" ]; then
  echo "no token to open on $BASE - the index looks wrong" >&2
  exit 1
fi
echo "mode $MODE / coin $COIN / sniper $SNIPER"

# The three parameterised URLs, empty parameters and all: a query has to sit
# before the hash for the page to see it at all, and pre_volume.js,
# pre_handles.js and pre_wallet.js each read their own key off it.
VOL_URL="#volume"
HAN_URL="#handles"
WAL_URL="#wallet"
if [ -n "$WINDOW" ]; then VOL_URL="?w=$WINDOW#volume"; fi
if [ -n "$HANDLE" ]; then HAN_URL="?h=$HANDLE#handles"; fi
if [ -n "$WADDR" ]; then WAL_URL="?addr=$WADDR#wallet"; fi

# The list is built here rather than at the top on purpose: the shell expands
# `$SNIPER` and `$COIN` as this literal is read, and under `set -u` a name that
# is not set yet does not become an empty string, it kills the script. Both are
# known by now, and one of them is only knowable after asking the dashboard.
#
# The fourth field is how long that frame gets, and only three need more than
# the default: the two snipe tabs fold twenty thousand early buys per wallet,
# which is the heaviest read the dashboard has - twenty seconds for the tokens
# and nearly forty for the wallets, on the same machine that is indexing - and
# the copy form waits on a factory read before its fee table appears. The
# default wait photographs the snipe tabs mid-request, and a table that has not
# been drawn yet looks exactly like a table with nothing in it.
#
# The Copy frame is opened on the same token as the coin card, from the same
# variable: the tour shows that card, and the copy plan over it is the same
# launch seen from the other end - what the factory charges to repeat it.
STATES=(
  "launches|#launches||"
  "volume|$VOL_URL|@tools/pre_volume.js|"
  "sniped|#sniped||35"
  "snipers|#snipers||55"
  "handles|$HAN_URL|@tools/pre_handles.js|"
  "copy|#copy/$COIN|@tools/pre_copy.js|25"
  "create|#create||"
  "wallet|$WAL_URL|@tools/pre_wallet.js|"
  "wallets|#wallets|$WALLETS_PRE|"
  "sniper|#sniper/$SNIPER||"
  "coin|#launches/coin/$COIN|@tools/pre_coin.js|"
)

mkdir -p "$OUT"
fail=0
first=1
for state in "${STATES[@]}"; do
  IFS="|" read -r name url pre wait <<< "$state"
  [ "$first" = 1 ] || sleep "$PAUSE"
  first=0
  printf '=== %s\n' "$name"

  # Two attempts per frame, because a frame can come back as a picture of a
  # page that never booted: the tab strip drawn, no panel chosen, every counter
  # a dash. It is not the dashboard failing - it is the previous frame's
  # abandoned requests still being served. Killing the browser mid-load leaves
  # the server generating logo after logo for a socket nobody is reading, and
  # the next page's own scripts queue behind them. Which frame it lands on is
  # luck, so the retry is not for a specific one.
  #
  # The signal is `"tab":` in the page state shot.js prints: a booted page
  # always names the tab it is on, including the tabs that legitimately have no
  # rows to show. A page that never booted has no tab and no counters, and no
  # picture of it is worth keeping.
  attempt=0
  while :; do
    # shellcheck disable=SC2086
    out=$(node tools/shot.js "$BASE/$url" "$OUT/$name.png" "${wait:-$WAIT}" "$W" "$H" $pre 2>&1)
    printf '%s\n' "$out"
    case "$out" in *'"tab":'*) break ;; esac
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 2 ]; then
      echo "  $name never booted, twice" >&2
      fail=1
      break
    fi
    echo "  $name never booted - the server is still busy with the last frame, retrying"
    sleep 5
  done
done

if [ "$fail" -ne 0 ]; then
  echo "one or more captures failed - the dashboard is still serving?" >&2
  exit 1
fi
echo "wrote $(ls -1 "$OUT"/*.png | wc -l) stills into $OUT"
