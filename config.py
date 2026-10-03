"""Configuration for the ponssight dashboard.

All chain constants live here. Nothing else in the project hardcodes them.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
# Images this machine pinned, named by cid. Not a second copy for safety -
# the pin is the durable one - but the copy that gets served, so drawing a
# logo we uploaded never waits on a gateway.
PINS_DIR = DATA_DIR / "pins"
DB_PATH = DATA_DIR / "pons.db"

for _d in (DATA_DIR, CACHE_DIR, PINS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- chain
# Two endpoints are known for this chain:
#   * the launchpad's own public node - no key, but it rate limits eth_call
#     hard (429 after a single burst), which makes a full backfill slow;
#   * a Goldsky edge node - keyed, no practical rate limit, ~0.2s/request and
#     fine with parallel calls. It needs an account of your own, so it is only
#     used when the key is given.
# The default is the public node, because it is the one that works with nothing
# configured. Put a keyed endpoint in PONS_RPC_URL to index at full speed.
PUBLIC_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
RPC_URL = os.getenv("PONS_RPC_URL", PUBLIC_RPC_URL)
# Live tail for new launches. The official node is HTTP-only, and the Goldsky
# edge product has no websocket either, so this is the one public wss that
# serves this chain. Falls back to polling if it will not connect.
WSS_URL = os.getenv("PONS_WSS", "wss://robinhood-rpc.publicnode.com")
WSS_ENABLED = os.getenv("PONS_WSS_ENABLED", "1") not in ("0", "false", "")
WSS_RECONNECT = float(os.getenv("PONS_WSS_RECONNECT", 5.0))

PUBLIC_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
CHAIN_ID = 4663
CHAIN_NAME = "Robinhood Chain"

# ------------------------------------------------------------------- x
# The X reading half is an optional extra: a client for X's private web API is
# not part of this project and is not vendored here, because it needs a signed-in
# session of your own either way. Point PONS_XCLIENT_DIR at a directory that has
# an `xclient` package in it and the Handles tab comes alive; leave it empty and
# every X-backed view answers "X is not configured" instead of failing.
XCLIENT_DIR = os.getenv("PONS_XCLIENT_DIR", "").strip()
# The bot's own .env is read for the token when this is empty, which is the
# usual case. The value is never logged and never printed - only its name.
X_TOKEN = os.getenv("PONS_X_TOKEN", "").strip()
X_TOKEN_ENV = os.getenv("PONS_X_TOKEN_ENV", "AUTH_TOKEN")
# Which of the bot's env files to read. Kept as a name so the path can differ
# per machine without touching code.
X_ENV_FILE = os.getenv("PONS_X_ENV_FILE", str(Path(XCLIENT_DIR) / ".env"))
# A profile does not change minute to minute, and every lookup is a request
# against somebody else's rate limit. A day is the documented behaviour.
X_PROFILE_TTL = int(os.getenv("PONS_X_PROFILE_TTL", 86400))
# 200 is what friends/list.json hands out per page, and it is the ceiling X
# documents; asking for more just wastes a round trip.
X_PAGE = int(os.getenv("PONS_X_PAGE", 200))
# First run defaults. 5000 followings is the number asked for, 25 pages at 200.
X_MAX_PAGES = int(os.getenv("PONS_X_MAX_PAGES", 25))
# A page takes ~0.6 s; the whole point of the batched display is that nobody
# waits for the last page. The walk stops at this many seconds rather than
# running until X says no.
X_MAX_SEC = float(os.getenv("PONS_X_MAX_SEC", 120.0))
# On a 429, wait for the reset header, but never longer than this. The walk
# resumes from its saved cursor, so a stop here costs nothing already fetched.
X_MAX_WAIT = float(os.getenv("PONS_X_MAX_WAIT", 30.0))
X_TIMEOUT = float(os.getenv("PONS_X_TIMEOUT", 20.0))

# ---------------------------------------------------------------- bridge
# The second chain the transfer-all route passes through. Robinhood Chain is
# the only chain this dashboard indexes, but moving a balance off it and back
# to a different wallet is a two-leg relay trip, and the middle leg lands here.
# This is a read-only endpoint for balances: nothing on this machine signs for
# Arbitrum, the browser wallet does.
ARB_CHAIN_ID = 42161
ARB_CHAIN_NAME = "Arbitrum One"
ARB_CHAIN_HEX = "0xa4b1"
ARB_RPC_URL = os.getenv("PONS_ARB_RPC", "https://arb1.arbitrum.io/rpc")

# relay.link. A quote is a POST and comes back with the transaction to send,
# already carrying the gas limit and the fee cap - so the node behind this URL
# is trusted for calldata, and the amounts it states are shown on the page
# before anything is signed. No key: the public endpoint answers without one.
RELAY_API = os.getenv("PONS_RELAY_API", "https://api.relay.link")
RELAY_TIMEOUT = float(os.getenv("PONS_RELAY_TIMEOUT", 20.0))
# How many times the max-send quote is redone. The amount is the balance minus
# the gas cost of the very transfer being quoted, so the two depend on each
# other and the first answer is only an estimate. Two rounds is normally the
# last one; the extra rounds are there because the fee can keep climbing.
RELAY_MAX_ROUNDS = int(os.getenv("PONS_RELAY_ROUNDS", 4))
# relay quotes maxFeePerGas at roughly the current base fee, and the base fee
# moves between one quote and the next - the same transfer quoted twice a second
# apart came back as 101819200 and 102320400. Signing with the fee of the quote
# that produced the amount leaves that difference behind as dust when the fee
# falls, and when it rises the node rejects the transaction outright for
# spending more than the balance. So the fee this machine signs with is its own
# number: relay's, plus this margin, frozen before the amount is solved from it.
#
# The margin is cheap. It is charged on the gas limit, not on the transfer, and
# only the unused part of it comes back: at this chain's fee (about 0.102 gwei
# over 32713 gas) 20% is 6.7e11 wei, seven hundred thousandths of a cent.
RELAY_FEE_HEADROOM_BPS = int(os.getenv("PONS_RELAY_FEE_HEADROOM", 2000))

# WalletConnect v2 identifies an app by a project id, which is free and comes
# from cloud.reown.com. Empty is a supported state rather than a broken one:
# the connect modal draws the WalletConnect row disabled and says where the id
# comes from, instead of offering a button that throws when it is pressed.
WALLETCONNECT_PROJECT_ID = os.getenv("PONS_WALLETCONNECT_ID", "")

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"

# Main launchpad factory. Emits TokenLaunched(token, curve, deployer,
# pairToken, launchConfigId, graduationThreshold).
FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"
TOKEN_LAUNCHED_SIG = (
    "TokenLaunched(address,address,address,address,uint256,uint256)"
)

# The block the factory above was deployed in, which is the first block a launch
# could exist in and therefore where this index's history begins.
#
# A measured fact rather than a guess: found by binary searching eth_getCode for
# this address over the chain, 26 calls, and confirmed by both ends - no code at
# block 0, 24177 bytes at the tip. Deployment is block 26841846, 2026-08-03
# 14:41 UTC.
#
# It exists because a retention window is not a beginning. The window says how
# much recent history is worth keeping; this says where the launchpad starts,
# and the two are different numbers. Before this, the history walk stopped at
# the window and the index covered 31 days of a launchpad that had been running
# for 43, so "no launch claims this handle" was a statement about a month rather
# than about the pad.
#
# The chain is young enough for the whole of it to be cheap: walking from the
# window's floor back to this block is 53 spans of HISTORY_CHUNK, measured at 3
# to 26 seconds each.
#
# Zero disables it, which restores the window as the only floor.
FACTORY_DEPLOY_BLOCK = int(os.getenv("PONS_FACTORY_DEPLOY", 26_841_846))

# Known quote assets. Native ETH shows up as the zero address on the curve.
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
NATIVE_QUOTE = "0x0000000000000000000000000000000000000000"

# ---------------------------------------------------------------- indexing
# How far back the launch history goes. This is a depth, not a retention:
# what it covers is indexed and kept, and raising it makes the indexer
# reach back for the difference on the next start.
#
# The number is blocks, and blocks are a bad unit to reason about here.
# This chain mines at roughly ten a second, so a day is about 850,000 of
# them and 900,000 - which reads like months on most chains - was 25
# hours. Everything below is derived from the same rate; if it changes,
# these stop being true.
BLOCKS_PER_DAY = 852_000
INDEX_WINDOW_BLOCKS = int(os.getenv("PONS_INDEX_WINDOW",
                                   30 * BLOCKS_PER_DAY))  # ~30 days
POLL_SECONDS = float(os.getenv("PONS_POLL_SECONDS", 4.0))
# The startup history walk goes backwards in spans of this size, so it
# can be interrupted without losing what it already covered.
HISTORY_CHUNK = int(os.getenv("PONS_HISTORY_CHUNK", 200_000))
# How much of a gap the live tail closes per tick. Small enough that a
# long catch-up does not hold the loop for minutes at a time.
TAIL_CATCHUP_BLOCKS = int(os.getenv("PONS_TAIL_CATCHUP", 250_000))
# Off, and it should stay off. Kept as a switch rather than deleted because
# a machine with a small disk may genuinely want the old behaviour back.
PRUNE_LAUNCHES = os.getenv("PONS_PRUNE_LAUNCHES", "").lower() in (
    "1", "true", "yes", "on")

# On, and it is what makes a fresh clone fill itself: the loops below walk the
# chain and write what they find. Off leaves the dashboard reading the database
# exactly as it stands, which is what a demo, a second screen or a database
# somebody handed you wants - nothing starts, nothing is fetched, no RPC is
# touched, and every tab renders from whatever rows are already there.
INDEX_ENABLED = os.getenv("PONS_INDEX", "1").lower() not in (
    "0", "false", "no", "off")
# Below this many undated tokens, asking for blocks one by one is cheaper than
# sweeping the whole window for the timestamps embedded in the logs.
TS_SWEEP_MIN = int(os.getenv("PONS_TS_SWEEP_MIN", 500))

# Refresh cadence. The head is the newest N tokens: their curve state moves
# on every block. The tail is a rotating window over everything else, so a
# wide span stays reasonably fresh without ever spending the whole RPC budget.
REFRESH_HEAD = int(os.getenv("PONS_REFRESH_HEAD", 100))
REFRESH_TAIL = int(os.getenv("PONS_REFRESH_TAIL", 200))
REFRESH_SECONDS = float(os.getenv("PONS_REFRESH_SECONDS", 5.0))

# How many tokens to enrich / refresh per multicall round, and how long to
# breathe between rounds.
BATCH = int(os.getenv("PONS_BATCH", 200))
ENRICH_PAUSE = float(os.getenv("PONS_ENRICH_PAUSE", 0.0))

# Cooldown after a 429 before touching the RPC again.
RATE_LIMIT_BACKOFF = float(os.getenv("PONS_RATE_BACKOFF", 20.0))

# How many rows one trade/write statement may cover before it is committed.
# A span of ten thousand blocks aggregated and written in a single transaction
# puts hundreds of megabytes into the write-ahead log at once, and a log that
# big cannot be checkpointed while it is still being appended to - the next
# transaction starts half a second later. Committing in chunks keeps every
# transaction small enough that the checkpoint gets a turn, which is what
# keeps the log from growing without bound. The statements are all sums and
# guarded merges, so splitting them changes nothing but the transaction size.
WRITE_CHUNK = int(os.getenv("PONS_WRITE_CHUNK", 2000))

# Once every this many seconds, and only when the trade walk has been idle,
# fold the write-ahead log back into the database and truncate it. A passive
# checkpoint cannot reset the file while anything is reading or writing, so
# this is the one that actually gives the disk space back.
WAL_TRUNCATE_SEC = float(os.getenv("PONS_WAL_TRUNCATE", 900.0))

# A checkpoint that copies this few frames while the log holds more is pinned,
# not idle: something is holding a snapshot at an old frame and the log will
# only grow. The copy phase stops there silently and reports success-shaped
# numbers, which is how a frozen `copied=1001` passed unremarked every fifteen
# minutes for four hours while the file reached fifty gigabytes. It is a
# warning now. 20000 frames is 80 MB.
WAL_STUCK_FRAMES = int(os.getenv("PONS_WAL_STUCK_FRAMES", 20000))

# The size at which a write-ahead log stops being reported and starts being
# repaired. The warning above was correct for four hours and reached nobody,
# because it went to stderr and this app has no handler for stderr; the log it
# was warning about reached a hundred and nineteen gigabytes and would have
# filled the disk within the day. A healthy log on this database is a few
# megabytes - SQLite folds it every thousand pages - so four gigabytes is a
# thousand times the normal size and still nowhere near the disk. Past it,
# `store.wal_checkpoint` rolls back whatever transaction is holding the
# snapshot, and replaces the connections if that was not enough.
WAL_MAX_GIB = float(os.getenv("PONS_WAL_MAX_GIB", 4.0))

# A transaction that has been open this long is not working, it is abandoned.
# The app keeps every transaction short on purpose - `_reprice` cuts a quarter
# of a million rows into bounded chunks precisely so none of them runs long -
# so five minutes is far past anything legitimate and well short of the four
# hours and twenty minutes the log was pinned for. The request path sweeps
# transactions older than this, which is what stops a failed statement from
# pinning the log in the first place rather than waiting for the size cap
# above to notice.
WAL_STALE_TX_SEC = float(os.getenv("PONS_WAL_STALE_TX_SEC", 300.0))

# The sweep runs on the request path, so it is throttled: at most one pass
# every this many seconds, however many requests arrive in between. The pass
# itself is a few dozen attribute reads, but the hot path should not carry
# even that on every image request.
WAL_SWEEP_SEC = float(os.getenv("PONS_WAL_SWEEP_SEC", 5.0))

# A quote's tokens are re-priced only when its rate has moved this far, or when
# it has not been re-priced for REPRICE_MAX_AGE seconds. The pass costs one
# write per token - a native pair is a quarter of a million of them - and
# running it on every ninety-second tick rewrote the whole table for a change no
# reader can see: the grid prints market caps in millions, and a tenth of a
# percent never reaches a digit it draws. Set the epsilon to 0 to re-price on
# every tick.
#
# The age is the floor under the epsilon, not the usual trigger, so it is set
# where a forced pass is cheap rather than where it is fresh: the epsilon
# already holds the column to a tenth of a percent, and the floor exists only
# so that it is rebuilt from scratch now and then. At an hour that is one pass
# per quote per hour - 325 MB for the native pair - instead of one every ten
# minutes, which was two gigabytes an hour for a guarantee the epsilon had
# already given.
RATE_EPSILON = float(os.getenv("PONS_RATE_EPSILON", 1e-3))
REPRICE_MAX_AGE = float(os.getenv("PONS_REPRICE_MAX_AGE", 3600.0))

# How many tokens one tick may re-price. The whole pass is the same number of
# writes however this is set; what it decides is how much of it lands in the
# log at once. The log file keeps the highest count of frames that were ever
# unfolded together, so a quarter of a million rows inside one tick - three
# hundred megabytes of log while a reader is inside it - becomes the size of
# the file for the rest of the run. The native pair is re-priced over about
# thirteen ticks at this value, twenty minutes at the loop's ninety-second
# period, and the column is a tenth of a percent stale at worst in the middle
# of that. 0 removes the cap and goes back to one pass per tick.
REPRICE_PER_TICK = int(os.getenv("PONS_REPRICE_PER_TICK", 20000))

# Every RPC request goes through one gate, because the limit is per IP and not
# per loop: left alone, six loops at once sail straight past it and everything
# 429s. Spacing is global, so this holds the whole process near one rate.
#
# The number is set for the endpoint in use. On the launchpad's own public node
# the limit was 500 requests/minute per IP, about 8/s in total, and 0.2 held the
# process near 300 rpm. The default is now the paid Goldsky edge node, measured
# at 41 calls/s across 16 threads with zero errors, so the spacing is a tenth of
# what it was. What makes that safe to write down is not the measurement but the
# penalty mechanism below: a 429 parks every caller through gate.hit() and the
# rate falls back on its own, so too low a number here degrades into a slower
# walk rather than a dead indexer. If the log shows 429s, raise it back to 0.2.
RPC_MIN_SPACING = float(os.getenv("PONS_RPC_SPACING", 0.025))   # ~2400 rpm
RPC_MAX_CONCURRENCY = int(os.getenv("PONS_RPC_CONCURRENCY", 8))

RPC_PENALTY = float(os.getenv("PONS_RPC_PENALTY", 15.0))
RPC_PENALTY_MAX = float(os.getenv("PONS_RPC_PENALTY_MAX", 120.0))
RPC_PENALTY_DECAY = float(os.getenv("PONS_RPC_PENALTY_DECAY", 120.0))
# Failures closer together than this count as one event, not many.
RPC_PENALTY_WINDOW = float(os.getenv("PONS_RPC_PENALTY_WINDOW", 5.0))

# Sub-calls per multicall batch - the node bills each one.
MULTICALL_CHUNK = int(os.getenv("PONS_MULTICALL_CHUNK", 600))

# getLogs ranges are halved on RPC errors until they succeed.
GETLOGS_MAX_SPAN = int(os.getenv("PONS_GETLOGS_SPAN", 20_000))

# ---------------------------------------------------------------- trades
# Curve buys are indexed with a topic0-only filter (no address list), because
# every trade log already names its own curve in `address`. That one filter
# covers all tokens at once. This node caps that query at ~12k blocks, well
# below the span it allows when a single address is given, so it gets its own
# limit rather than sharing GETLOGS_MAX_SPAN.
TRADES_SPAN = int(os.getenv("PONS_TRADES_SPAN", 10_000))

CURVE_BUY_SIG = ("CurveBuy(address,address,uint256,uint256,uint256,uint256)")
CURVE_SELL_SIG = ("CurveSell(address,address,uint256,uint256,uint256,uint256)")
# FeesSwept(protocol, tokens, creator). No indexed arguments: which curve
# it belongs to is the address that emitted it, the same as a trade.
CURVE_FEES_SWEPT_SIG = ("FeesSwept(uint256,uint256,uint256)")
# The sweep walk is a third topic0-only scan, so it gets the same ceiling.
FEES_SPAN = int(os.getenv("PONS_FEES_SPAN", 10_000))
# Idle poll once the walk has caught up with the tip.
FEES_SECONDS = float(os.getenv("PONS_FEES_SECONDS", 4.0))

# A first buy this many blocks after the launch is a snipe: blocks are ~0.1s,
# so two of them are a fraction of a second - faster than a human can react.
SNIPE_BLOCKS = int(os.getenv("PONS_SNIPE_BLOCKS", 2))
# The window in which we record every buy, because that is where a snipe
# lives. Everything older only contributes to the running totals.
EARLY_BLOCKS = int(os.getenv("PONS_EARLY_BLOCKS", 10))

# Volume is bucketed so the UI can ask for 5m / 1h / 24h without rescanning
# logs. Buckets are pruned once they fall out of the widest window.
TRADE_BUCKET_SEC = int(os.getenv("PONS_TRADE_BUCKET", 300))
TRADE_KEEP_HOURS = float(os.getenv("PONS_TRADE_KEEP_HOURS", 25.0))

# The coin card's chart needs a finer slice than the volume ranking does: an
# average launch here trades for about a quarter of an hour, so five minute
# buckets would draw it as three points. A minute is the coarsest slice that
# still shows the shape of a launch, and it costs about five times the rows
# of the volume buckets. This is a size of its own rather than a change to
# TRADE_BUCKET_SEC because the bucket key is the timestamp divided by it:
# changing that constant would silently reinterpret every row already stored.
CANDLE_BUCKET_SEC = int(os.getenv("PONS_CANDLE_BUCKET", 60))

# A wallet that is the first outside buyer on this many launches is a bot,
# not a lucky person.
BOT_HITS_MIN = int(os.getenv("PONS_BOT_HITS", 5))

# ------------------------------------------------------------- sniper profile
# How long a chain reading of a deployer is worth showing before it is called
# stale. A nonce only grows and a balance moves both ways, so the honest window
# is short - but every refresh is a request through the same gate the indexer
# lives on, and a tab left open overnight must not quietly spend that budget.
# An expired row is *reported* as stale, never refetched by itself.
SW_CHAIN_TTL = int(os.getenv("PONS_SW_CHAIN_TTL", 3600))

# The most deployers one profile will look up or read from cache. A sniper with
# twenty-five hundred distinct deployers costs seven grouped queries over
# `tokens`, which is cheap; the cap is here so that a pathological wallet cannot
# turn a page load into a scan. When it bites, the page says so.
SW_DEPLOYER_LIMIT = int(os.getenv("PONS_SW_DEPLOYERS", 6000))

# How many seconds to wait between X profile lookups in a walk. The client
# has no pacing of its own, and a hundred profiles in one second is a
# guaranteed 429. Half a second is what the followings walk spends between
# pages and has not been limited in months.
SW_X_PACE = float(os.getenv("PONS_SW_X_PACE", 0.5))

# How many of the profile's own RPC calls may be in flight at once. This is a
# cap of its own rather than a share of the indexer's, because the walk is a
# burst: a thousand-address backfill holding every slot the indexer has would
# stall the launch feed for the minute the walk lasts. The gate above is still
# the thing that paces the wire - this only decides how much of the process
# waits on the walk rather than on its own work.
SW_RPC_CONCURRENCY = int(os.getenv("PONS_SW_RPC_CONCURRENCY", 12))

# Reads per second one walk actually sustains, which is what the button's ETA is
# computed from. It is not 1 / RPC_MIN_SPACING: the gate spaces *starts*, and
# the walk is bounded by its own slot count and by how long the node takes to
# answer, so it never reaches the spacing's ceiling. Measured on this node with
# 50 distinct deployers: 101 reads in 3.8s, 26.4/s, no failures - and the rate
# was still climbing with the address count, so a long walk does better than
# this rather than worse. An ETA that is three times too optimistic is worse
# than no ETA, so the number stays at the measured floor.
SW_RPC_PER_SEC = float(os.getenv("PONS_SW_RPC_PER_SEC", 26.0))

# ------------------------------------------------- chains that are not this one
# The chains a deployer's past is worth reading on, as (chain_id, label, url).
# Nonce only - how many transactions the address has ever sent - because that
# is the cheapest honest answer to "is this a wallet with a history or one made
# for this launch", and it is one call per address per chain. The same address
# has a different nonce on each, and the profile prints them side by side
# precisely because they disagree: a wallet that is brand new here and has five
# thousand transactions on Ethereum is a different animal from one that is new
# everywhere.
#
# Both endpoints are keyless and were measured from this machine before being
# made a dependency: Ethereum answered 24 concurrent calls in 0.42s with no
# failures, Arbitrum 12 in 0.86s. The one trap found on the way is worth
# writing down - publicnode refuses a request that carries urllib's default
# User-Agent with a 403 that reads exactly like a rate limit, and answers any
# explicit one. web3.py sends python-requests, so nothing here sets a UA; a
# hand-rolled urllib caller against this URL must.
SW_EXT_CHAINS: tuple[tuple[int, str, str], ...] = (
    (1, "ETH", os.getenv("PONS_ETH_RPC",
                         "https://ethereum-rpc.publicnode.com")),
    (ARB_CHAIN_ID, "ARB", os.getenv("PONS_ARB_RPC", ARB_RPC_URL)),
)
# Pacing is per host and not shared with the indexer's gate. The limit being
# respected belongs to the remote node and each node counts on its own, so
# pacing Ethereum at the rate Robinhood tolerates would be either useless or a
# shutdown. This is the case the single-gate warning on the indexer's gate does
# not cover - that one is about two gates on ONE url, which would pace half the
# process at one rate and half at another.
SW_EXT_SPACING = float(os.getenv("PONS_SW_EXT_SPACING", 0.05))
SW_EXT_CONCURRENCY = int(os.getenv("PONS_SW_EXT_CONCURRENCY", 8))
# A nonce only ever grows, but a wallet that has not moved yet is the whole
# signal, so a reading is re-taken on the same clock as the local one.
SW_EXT_TTL = float(os.getenv("PONS_SW_EXT_TTL", 3600.0))

# One press of the X button walks at most this many handles, and stops at the
# deadline regardless. Repeat presses continue from where the last one stopped,
# so the cap is how much of the account's rate limit one click may spend, not
# how much of the job can ever be done. `xsource` has no pacing of its own, so
# a walk that ran to the end of a twenty-two-hundred-handle list in one request
# would be both a hung page and a guaranteed 429.
#
# The deadline is longer than X_MAX_SEC above, which belongs to the followings
# walk: that one pages through a single account and 120s is a whole account,
# while this walks hundreds of separate profiles and 120s would stop it a third
# of the way through a press that is already capped at SW_X_MAX handles.
SW_X_MAX = int(os.getenv("PONS_SW_X_MAX", 400))
SW_X_MAX_SEC = float(os.getenv("PONS_SW_X_MAX_SEC", 600.0))

# How long a single rate-limit wait inside the profiles walk may be. X hands out
# a `reset` timestamp in its own 429 and the window is around fifteen minutes,
# so the cap is set to cover a whole window: sleeping the exact time is one
# round trip, while a short fixed backoff is a 429 every thirty seconds for the
# length of the window and stalls the walk for the same time it would have
# spent asleep. Deliberately not `X_MAX_WAIT` (30s), which belongs to the
# followings walk where the client does the waiting per page.
SW_X_WAIT = float(os.getenv("PONS_SW_X_WAIT", 960.0))

# How long one press may spend asleep on rate limits before it gives up and
# hands the button back. `SW_X_WAIT` bounds a single wait; without a bound on
# their total, a press that keeps being handed a fresh reset never returns, the
# run stays `running` for hours, and because the starter refuses a second press
# while one is live the button is dead the whole time. Set to two full windows
# so a single capped wait always fits inside it and the walk still gets a second
# chance at the list after one: a press that has slept out two windows and still
# cannot read a profile is not going to, and the handles it did cover are
# committed.
SW_X_WAIT_BUDGET = float(os.getenv("PONS_SW_X_WAIT_BUDGET", 1800.0))

# How many blocks of trades history to walk backwards on first run, and how
# long to rest between spans so the launch indexer keeps its share of the RPC.
# Deliberately not INDEX_WINDOW_BLOCKS any more: that constant just grew
# thirtyfold, and trade history is heavier per block than launch history.
TRADES_BACKFILL_BLOCKS = int(os.getenv("PONS_TRADES_BACKFILL", 900_000))
TRADES_PAUSE = float(os.getenv("PONS_TRADES_PAUSE", 0.5))

# ---------------------------------------------------------------- launching
# The factory exposes launchToken(...) at this selector; the launch fee is a
# flat charge on top of whatever the launcher buys in the same transaction.
LAUNCH_TOKEN_SIG = (
    "launchToken((string,string,string,string,"
    "(string,string,string,string,string),address,uint16,bool,bytes32,bytes32),"
    "uint256,address)"
)
LAUNCH_FEE_WEI = int(os.getenv("PONS_LAUNCH_FEE", 500_000_000_000_000))  # 0.0005
LAUNCH_CONFIG_ID = int(os.getenv("PONS_LAUNCH_CONFIG", 0))

# The router wraps the factory and splits msg.value before calling it, which is
# the only way to buy in the same transaction as the launch: the factory takes
# msg.value == the launch fee exactly and reverts on a wei more, so a buy has to
# arrive through a contract that separates the two. Decoded off the chain, not
# guessed - the type list below reproduces the calldata of all 42 router
# launches in the last 80 exactly, and value == buy + fee holds to the wei.
# The old note here said this signature was launchAndBuy(tuple,uint256,address,
# uint256,address[]); that hashes to 0xa96685fd and is not what anything calls.
ROUTER = "0xe33E9E479dF8802cb0866d5d05258bEc4cF62948"
LAUNCH_AND_BUY_SIG = (
    "launchAndBuy((string,string,string,string,"
    "(string,string,string,string,string),address,uint16,bool,bytes32,bytes32),"
    "uint256,address,uint256,uint256,address,address[])"
)
# How far under the modelled fill a router buy will accept. Real launches on
# this chain set floors at 0.95 and 0.98 of the model, so 5% is the going rate.
DEFAULT_SLIPPAGE_BPS = int(os.getenv("PONS_SLIPPAGE_BPS", 500))
# Refuse to build calldata for a buy larger than this, so a fat finger in the
# copy form cannot silently become a real mainnet transaction.
MAX_INITIAL_BUY_QUOTE = float(os.getenv("PONS_MAX_INITIAL_BUY", 5.0))
# How much gas over the estimate is signed. The estimate reads the curve in one
# block and the transaction is mined in another, and the price of being wrong
# here is one-sided: gas that is not used comes back, and gas that is short
# burns the whole launch fee on a transaction that reverts. Unlike the fee
# margin above this one is charged in full up front, so it is 20% of an estimate
# that is itself already the cost of the launch - the same order as the fee, and
# the same argument: refunded when nothing goes wrong.
GAS_HEADROOM_BPS = int(os.getenv("PONS_GAS_HEADROOM", 12000))

# ---------------------------------------------------------------- pricing
# Public spot prices (no API key). Failures degrade gracefully to
# quote-denominated values.
PRICE_REFRESH_SECONDS = float(os.getenv("PONS_PRICE_REFRESH", 90.0))
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/{pair}/spot"
YAHOO_QUOTE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

# Quote assets that are dollars one for one.
STABLE_SYMBOLS = {"USDG", "USDC", "USDT", "DAI", "USD", "PYUSD", "USDE"}

# Wrapped coins, priced through Coinbase as a pair.
CRYPTO_SYMBOL_MAP = {
    "CBBTC": "BTC-USD", "WBTC": "BTC-USD", "TBTC": "BTC-USD",
    "WETH": "ETH-USD", "CBETH": "ETH-USD", "STETH": "ETH-USD",
    "SOL": "SOL-USD",
}

# Ticker aliases for the quote API. Anything not listed is looked up under
# its own symbol, since the chain's quote assets are mostly equities.
STOCK_SYMBOL_MAP = {
    "SPY": "SPY", "PLTR": "PLTR", "COIN": "COIN", "SHOP": "SHOP",
    "SNDK": "SNDK", "QQQ": "QQQ", "AAPL": "AAPL", "TSLA": "TSLA",
    "NVDA": "NVDA", "MSFT": "MSFT", "AMZN": "AMZN", "GOOGL": "GOOGL",
    "META": "META", "NFLX": "NFLX", "AMD": "AMD", "CRCL": "CRCL",
}

# ---------------------------------------------------------------- server
HOST = os.getenv("PONS_HOST", "127.0.0.1")
PORT = int(os.getenv("PONS_PORT", 8787))

# Where a new logo goes, and the key that puts it there.
#
# A logo ends up in the token contract as a uri that cannot be changed
# afterwards, so the host behind it matters more than it looks: a cid is
# held by every node that has the file, and any gateway can serve it. The
# key is only needed to add a file, never to read one.
#
# Pinata v3, not the older pinning host - that one resets the connection.
# No key means the upload falls back to the anonymous host, so the tab
# keeps working without one.
PINATA_JWT = os.getenv("PONS_PINATA_JWT", "")
PINATA_API = "https://uploads.pinata.cloud/v3/files"
# The pair prefix marks the uri as ipfs, so a reader resolves it through a
# gateway rather than treating the cid as a hostname. It is also what the
# rest of this chain writes, which is the reason to match it.
IPFS_URI_PREFIX = os.getenv("PONS_IPFS_URI", "ipfs://")

# Tried in order for ipfs:// logos. What answers here is a fact about the
# network rather than about the gateway: cloudflare-ipfs no longer resolves
# at all, 4everland does not resolve the cid subdomain, and pinata rate
# limits this address while still being the one holding our own pins. The
# order below is the order they answered in, best first.
IPFS_GATEWAYS = [
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://nftstorage.link/ipfs/",
    "https://w3s.link/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
    "https://4everland.io/ipfs/",
]

# How long one gateway gets to answer before the next is tried. The old value
# was twenty seconds per gateway, six gateways deep - a single logo the
# network does not have could hold a request for two minutes, and every one
# of those seconds is a server thread that is not answering anything else.
IMAGE_TIMEOUT = float(os.getenv("PONS_IMAGE_TIMEOUT", 12.0))
IMAGE_MAX_BYTES = int(os.getenv("PONS_IMAGE_MAX_MB", 8)) * 1024 * 1024

# How long a cached image is worth keeping. There was no eviction here at all,
# so the folder grew by every logo any browser had ever asked for - 93,271
# files and 25.6 GB by 18.09.2026, about 6.6 GB a day, and the disk had
# twenty-seven days left. An entry past this age is a miss rather than a
# deletion on the request path: whatever asks for that logo again fetches it
# and it is cached anew, so the folder settles at one day of traffic instead
# of every day of it. 0 keeps images forever, which is how this behaved before.
IMAGE_TTL_SEC = float(os.getenv("PONS_IMAGE_TTL_HOURS", 24.0)) * 3600.0

# How often the cache folder is walked and the expired entries deleted. The
# ttl is measured in hours, so this is about not letting dead files sit there
# for the rest of the day, not about precision - half an hour of a two
# hundred megabyte surplus is nothing next to the walk over ninety thousand
# directory entries.
IMAGE_PRUNE_EVERY = float(os.getenv("PONS_IMAGE_PRUNE_EVERY", 1800.0))

# The grid opens on the newest tokens, whose logos are exactly the ones no
# browser has ever asked for, so a cold page is a hundred gateway round trips
# the browser waits on six at a time. This walk fetches the logos of the
# newest tokens into the cache ahead of the page, one at a time and slowly,
# so that opening the dashboard is mostly reads off this disk. 0 turns it off.
LOGO_PREFETCH = int(os.getenv("PONS_LOGO_PREFETCH", 200))
LOGO_PREFETCH_PAUSE = float(os.getenv("PONS_LOGO_PAUSE", 0.5))
LOGO_PREFETCH_EVERY = float(os.getenv("PONS_LOGO_EVERY", 60.0))

# /api/stats runs five counts over the tokens table and the page asks for it
# on every polling tick, several times per tick when more than one tab is
# open. The numbers it reads change on the scale of blocks, not milliseconds,
# so a short-lived copy answers almost every call and the count runs once.
STATS_TTL = float(os.getenv("PONS_STATS_TTL", 2.0))

# ---------------------------------------------------------------- keys
# Keys on this machine are stored encrypted, under a passphrase that is typed
# into the page and held in the server's memory. It is deliberately not
# readable from the environment: a variable has to be set somewhere, and that
# somewhere is a script or a shortcut on the disk the encryption exists to
# survive losing. See keysafe.py for what this does and does not protect.
#
# How long an opened vault stays open without being used. Every read of a key
# resets it, so this only fires while nothing is happening. A sweep in flight
# is unaffected either way: the thread that runs it is handed the one key it
# needs before it starts. 0 keeps the vault open until it is locked or the
# server stops, which is the behaviour the tab had before any of this and no
# protection at all.
KEY_IDLE_SEC = float(os.getenv("PONS_KEY_IDLE_SEC", 1800))
# Refused when the passphrase is being set. Long enough that guessing is not
# the cheapest way in, short enough to be typed; a few words is the shape to
# aim for and this only stops the eight-character ones.
KEY_MIN_PHRASE = int(os.getenv("PONS_KEY_MIN_PHRASE", 8))

# How long a pair of balances for a stored wallet is worth showing before the
# Wallets tab asks for fresh ones. Both halves are batched, so a refresh is two
# calls for the whole list rather than one per wallet - but the robinhood half
# goes through the gate the indexer lives on, and a page reloaded ten times in
# a minute must not cost ten of its slots. A minute is short enough that a
# balance moved by hand is visible on the next look and long enough that a
# burst of reloads costs one reading.
KEY_CHAIN_TTL = int(os.getenv("PONS_KEY_CHAIN_TTL", 60))

# ---------------------------------------------------------------- UI
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
