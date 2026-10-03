"""The two ways a fresh token's buys were being thrown away, checked offline.

The display this file is about is the snipe verdict on a token launched in the
last minute, and both cases below are about the walk reading a block before the
launch indexer has written the token that block trades. Nothing here talks to
the node: the logs are built here and the fetcher is replaced by a stub, so a
failure means the logic is wrong rather than that the chain was quiet.

1. A log whose curve the map does not know yet used to be skipped, and the
   block it sat in was never walked again, so the buy was gone for good. It is
   held now and folded once the token appears - and only once, because the
   tables it lands in are sums.
2. A subscription log does not decode at all: the topics were converted to
   bytes and `data` was not, so the codec refused every one of them and
   `decode_trade` reported the refusal as "not a trade".

Writes go to a database of their own, made here, so the dashboard's is
untouched.
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config as C

# Before the first connection, which is lazy: store.conn() reads this.
C.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pons-test-"), "test.db")

from hexbytes import HexBytes

import chain
import snipe
import store
import trades

store.init()

DEV = "0x" + "11" * 20
RACER = "0x" + "22" * 20
TOKEN = "0x" + "33" * 20
CURVE = "0x" + "44" * 20
OTHER = "0x" + "55" * 20
GHOST = "0x" + "66" * 20
LAUNCH_TX = "0x" + "aa" * 32
LATE_TX = "0x" + "bb" * 32
TS = 1_760_000_000
BLOCK = 5_000_000

bad = 0


def check(name, ok, extra=""):
    global bad
    if not ok:
        bad += 1
    print("%-5s %-46s %s" % ("ok" if ok else "FAIL", name, extra))


def _addr_topic(a):
    return HexBytes(bytes(12) + bytes.fromhex(a[2:]))


def trade_log(curve=OTHER, who=RACER, quote=5 * 10 ** 17, tokens=10 ** 18,
              block=BLOCK, log_index=1, tx=LATE_TX, side="buy", ts=TS):
    """A CurveBuy / CurveSell log in the shape web3 hands one over.

    The two value words mean opposite things on the two events, so the side
    decides their order - the same rule `decode_trade` documents.
    """
    topic0 = chain.BUY_TOPIC if side == "buy" else chain.SELL_TOPIC
    first, second = (quote, tokens) if side == "buy" else (tokens, quote)
    return {
        "address": curve,
        "topics": [HexBytes(bytes.fromhex(topic0[2:])),
                   _addr_topic(who), _addr_topic(who)],
        "data": chain.w3.codec.encode(["uint256"] * 4,
                                      [first, second, 0, 0]),
        "blockNumber": block,
        "logIndex": log_index,
        "transactionHash": tx,
        "blockTimestamp": ts,
    }


def launch_log(block=BLOCK, tx=LAUNCH_TX, token=TOKEN, curve=CURVE):
    """A TokenLaunched log, so the launch row is built the real way."""
    return {
        "address": C.FACTORY,
        "topics": [HexBytes(bytes.fromhex(chain.TOPIC0[2:])),
                   _addr_topic(token), _addr_topic(curve), _addr_topic(DEV)],
        "data": b"",
        "blockNumber": block,
        "logIndex": 0,
        "transactionHash": tx,
        "blockTimestamp": TS,
    }


def serve(logs):
    """Replace the node with a fixed answer for the next span."""
    chain.get_logs_chunked = lambda *a, **k: list(logs)


def clear():
    trades._pending.clear()
    trades._pending_keys.clear()


# --- 1. the log that never decoded --------------------------------------
# What a subscription actually sends: hex text, and not even 0x-prefixed on
# this node. Before the fix the codec raised on every one of these and the
# exception was swallowed as "unreadable log".
def bare(b):
    """Hex text with no 0x, the way this node's subscription sends it."""
    s = b.hex() if isinstance(b, (bytes, bytearray)) else str(b)
    return s[2:] if s.startswith("0x") else s


raw = trade_log()
ws = {"address": raw["address"], "topics": [bare(t) for t in raw["topics"]],
      "data": bare(raw["data"]), "blockNumber": hex(raw["blockNumber"]),
      "logIndex": hex(raw["logIndex"]),
      "transactionHash": raw["transactionHash"],
      "blockTimestamp": raw["blockTimestamp"]}
t = chain.decode_trade(chain.normalize_log(ws))
check("subscription log decodes", t is not None and t["quote"] == 5 * 10 ** 17
      and t["tokens"] == 10 ** 18 and t["block"] == BLOCK,
      "quote=%s block=%s" % (t["quote"] if t else None,
                             t["block"] if t else None))

# --- 2. what aggregate hands back instead of dropping --------------------
empty = trades.aggregate([trade_log()], {})
check("unknown curve is reported, not dropped",
      len(empty["unknown"]) == 1 and not empty["trades"],
      "unknown=%s trades=%s" % (len(empty["unknown"]), len(empty["trades"])))

known = trades.aggregate([trade_log()],
                         {OTHER.lower(): {"address": TOKEN,
                                          "launch_block": BLOCK}})
check("known curve is folded and nothing waits",
      not known["unknown"] and len(known["trades"]) == 1
      and known["trades"][0][2] == 1,
      "unknown=%s buys=%s" % (len(known["unknown"]),
                              known["trades"][0][2] if known["trades"] else None))

# An early buy is one inside the launch window; the whole point of the table.
near = trades.aggregate([trade_log(block=BLOCK + 1)],
                        {OTHER.lower(): {"address": TOKEN,
                                         "launch_block": BLOCK}})
far = trades.aggregate([trade_log(block=BLOCK + C.EARLY_BLOCKS + 1)],
                       {OTHER.lower(): {"address": TOKEN,
                                        "launch_block": BLOCK}})
check("only the launch window counts as early",
      len(near["early"]) == 1 and not far["early"],
      "near=%s far=%s" % (len(near["early"]), len(far["early"])))

# --- 3. the buy that raced the launch, end to end ------------------------
# The race: the walk folds the block, and the token row for it does not exist
# yet, because the launch indexer writes it a second or two later.
clear()
serve([trade_log(curve=CURVE, block=BLOCK, log_index=1, tx=LAUNCH_TX)])
n, e = trades.index_spans(BLOCK, BLOCK + C.EARLY_BLOCKS)
check("a race is deferred, not lost",
      e == 0 and len(trades._pending) == 1 and n == 0,
      "early=%s pending=%s" % (e, len(trades._pending)))

# The launch indexer catches up.
store.upsert_launches([chain.parse_launch(launch_log())])

# The next span is empty - which is exactly when the retry has to run.
serve([])
n, e = trades.index_spans(BLOCK + 100, BLOCK + 200)
check("an empty span still folds what waited",
      e == 1 and not trades._pending, "early=%s pending=%s" % (e, len(trades._pending)))

tot = store.trades_for([TOKEN]).get(TOKEN) or {}
check("the launch-tx buy is the token's first buy",
      tot.get("buys") == 1 and tot.get("first_buy_block") == BLOCK
      and (tot.get("first_buy_tx") or "").lower() == LAUNCH_TX.lower(),
      "buys=%s first=%s tx=%s" % (tot.get("buys"), tot.get("first_buy_block"),
                                  (tot.get("first_buy_tx") or "")[:10]))

early = store.early_buys_for([TOKEN]).get(TOKEN) or []
check("and it is in the early window", len(early) == 1,
      "rows=%s" % len(early))

# This is the display the report was about: `bundled` is a fact about the
# trades row, so a missing first buy read as an ordinary late buy.
v = snipe.verdict({"launch_block": BLOCK, "deployer": DEV,
                   "launch_tx": LAUNCH_TX, "graduation_threshold": None},
                  tot, early, None, None, indexed=True)
check("the verdict says it was bundled",
      v["bundled"] and v["label"] == "sniped" and v["delta"] == 0,
      "label=%s delta=%s bundled=%s" % (v["label"], v["delta"], v["bundled"]))

# --- 4. folded once, never twice ----------------------------------------
# The tables it lands in are sums, so a second fold would double the volume
# rather than be ignored.
serve([])
trades.index_spans(BLOCK + 300, BLOCK + 400)
again = store.trades_for([TOKEN]).get(TOKEN) or {}
check("a later span does not fold it again", again.get("buys") == 1,
      "buys=%s" % again.get("buys"))

# --- 5. the buffer's own rules ------------------------------------------
clear()
entry = (CURVE.lower(), BLOCK, 1, trade_log(curve=CURVE))
trades._defer([entry, entry])
check("the same log is held once", len(trades._pending) == 1,
      "pending=%s" % len(trades._pending))

# A cards_only pass replays blocks the walk has already been through, so its
# unknowns must not be held for a fold that would count them a second time.
# The entry held here is one no fold can resolve, so the only way the count
# can rise is the pass deferring its own log.
clear()
ghost = (GHOST.lower(), BLOCK, 2, trade_log(curve=GHOST))
trades._defer([ghost])
serve([trade_log(curve=OTHER, block=BLOCK + 700, log_index=3)])
trades.index_spans(BLOCK + 700, BLOCK + 710, cards_only=True)
check("a cards_only pass defers nothing",
      len(trades._pending) == 1 and trades._pending[0][0] == GHOST.lower(),
      "pending=%s" % [e[0][:8] for e in trades._pending])

# The buffer is bounded, and what falls out of it is reported rather than lost
# quietly. The bound is lowered here so the case does not need eight thousand
# logs to reach it.
clear()
keep = trades._PENDING_MAX
trades._PENDING_MAX = 3
try:
    for i in range(5):
        trades._defer([(OTHER.lower(), BLOCK + i, i, trade_log(block=BLOCK + i))])
finally:
    trades._PENDING_MAX = keep
check("the buffer stays bounded and says so",
      len(trades._pending) == 3 and trades._pending_dropped == 2
      and trades._pending[0][1] == BLOCK + 2,
      "pending=%s dropped=%s" % (len(trades._pending), trades._pending_dropped))

# --- 6. the repair pass ------------------------------------------------
# Everything above is about not losing the buy in the first place. This is the
# other half: a buy that was lost before the fix existed is still lost, because
# the walk never revisits a block, and the repair pass is what re-reads the
# blocks and puts the first-buy facts back. It has to do that without touching
# the sums, which are the columns a replay would double.
clear()
RERUN = "0x" + "77" * 20
RTOKEN = "0x" + "88" * 20
store.upsert_launches([chain.parse_launch(
    launch_log(block=BLOCK + 20_000, tx=LAUNCH_TX,
               token=RTOKEN, curve=RERUN))])
# The state the bug left behind: the launch-window buy was dropped, a later buy
# was folded, so the token has a first buy far too late and no early window.
late = trade_log(curve=RERUN, who=RACER, block=BLOCK + 20_050, log_index=5,
                 tx=LATE_TX)
serve([])
trades.index_spans(BLOCK + 20_000, BLOCK + 20_100)
store.apply_trades(trades.aggregate([late], store.curve_map()))
addr = store.curve_map()[RERUN.lower()]["address"]
before = store.trades_for([addr]).get(addr) or {}
check("the damage is in the fixture: a late first buy, no early window",
      before.get("first_buy_block") == BLOCK + 20_050
      and not store.early_buys_for([addr]).get(addr),
      "first=%s early=%s" % (before.get("first_buy_block"),
                             len(store.early_buys_for([addr]).get(addr) or [])))

# The repair reads the same blocks again, this time with the token in the map.
first = trade_log(curve=RERUN, who=RACER, block=BLOCK + 20_000, log_index=1,
                  tx=LAUNCH_TX)
serve([first, late])
dry = trades.repair_spans(BLOCK + 20_000, BLOCK + 20_100, dry_run=True)
after_dry = store.trades_for([addr]).get(addr) or {}
check("a dry run reports the correction and writes nothing",
      dry["moved"] == 1 and dry["early"] == 1
      and after_dry.get("first_buy_block") == BLOCK + 20_050,
      "moved=%s early=%s first=%s" % (dry["moved"], dry["early"],
                                      after_dry.get("first_buy_block")))

real = trades.repair_spans(BLOCK + 20_000, BLOCK + 20_100)
after = store.trades_for([addr]).get(addr) or {}
check("the repair moves the first buy back into the launch window",
      real["moved"] == 1 and after.get("first_buy_block") == BLOCK + 20_000
      and (after.get("first_buy_tx") or "").lower() == LAUNCH_TX.lower(),
      "moved=%s first=%s tx=%s" % (real["moved"],
                                   after.get("first_buy_block"),
                                   (after.get("first_buy_tx") or "")[:10]))
check("and it does not touch the sums",
      after.get("buys") == before.get("buys")
      and after.get("buy_volume") == before.get("buy_volume")
      and after.get("last_trade_block") == before.get("last_trade_block"),
      "buys=%s/%s volume=%s/%s" % (after.get("buys"), before.get("buys"),
                                   after.get("buy_volume"),
                                   before.get("buy_volume")))
check("and the early window it was missing is there",
      len(store.early_buys_for([addr]).get(addr) or []) == 1,
      "rows=%s" % len(store.early_buys_for([addr]).get(addr) or []))

# The verdict is the point of all of it: the same token the fixture read as a
# late ordinary buy now reads as one bought in the launch transaction.
v2 = snipe.verdict({"launch_block": BLOCK + 20_000, "deployer": DEV,
                    "launch_tx": LAUNCH_TX, "graduation_threshold": None},
                   after, store.early_buys_for([addr]).get(addr), None, None,
                   indexed=True)
check("the repaired token is bundled now",
      v2["bundled"] and v2["delta"] == 0,
      "label=%s delta=%s bundled=%s" % (v2["label"], v2["delta"],
                                        v2["bundled"]))

# Idempotent, because the cursor cannot promise a run finished: the tool is
# meant to be started again.
again = trades.repair_spans(BLOCK + 20_000, BLOCK + 20_100)
check("running the repair again changes nothing",
      again["moved"] == 0 and again["early"] == 0,
      "moved=%s early=%s" % (again["moved"], again["early"]))

# A log for a token that is still not indexed is the running indexer's
# business, not the repair's: it is counted and skipped, and nothing is left
# waiting in the buffer.
clear()
serve([trade_log(curve=GHOST, block=BLOCK + 20_200, log_index=9)])
skipped = trades.repair_spans(BLOCK + 20_200, BLOCK + 20_300)
check("an unindexed curve is skipped, not held",
      skipped["unknown"] == 1 and skipped["moved"] == 0
      and not trades._pending,
      "unknown=%s pending=%s" % (skipped["unknown"], len(trades._pending)))

clear()
print("\nALL UNIT CASES PASS:", not bad)
sys.exit(1 if bad else 0)
