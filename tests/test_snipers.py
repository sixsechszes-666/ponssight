"""The sniper profile: what a snipe is, and what must never drift.

Every case here runs on a fixture in its own database rather than on the live
one, for the reason the live table makes obvious: "1169 snipes" is a number that
moves every time the indexer credits another wallet, so a test asserting it
would be a test of when it was run. The fixture is small and pinned, and what it
pins is the arithmetic and the definitions - the two things that were got wrong
while this feature was being measured.

The three findings this file exists to keep wrong:

  1. The launch transaction's `early_buys` row is NOT excluded. It is the
     bundle's router buying at `launch_block` with a low log_index, and it beats
     everyone who came later. A wallet it overtook is not a sniper, and the one
     tempting "fix" - adding `e2.tx <> t.launch_tx` to the NOT EXISTS - files
     those wallets as snipers. On the live table that mistake turns 1169 into
     1483. Case 1 fails if anyone makes it.

  2. `ROW_NUMBER() OVER (PARTITION BY e.address ...)` over a CTE already
     filtered to one buyer does not mean "first into the token". It means "first
     among this wallet's own rows", which is true of nearly every row and
     measured 2469 against a true 1479. Case 3 pins the answer to
     `first_racers`, which is the implementation that is actually correct.

  3. A nonce nobody has read is not a nonce of zero. Case 9 pins that unknown
     sort values go last in BOTH directions, because a zero would sort to the
     top of an ascending list and state a fact we do not have.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config as C

# Point the whole storage layer at a throwaway file before it opens anything.
# conn() reads C.DB_PATH on first use per thread, so this has to happen before
# the first store.conn() call anywhere.
_TMP = tempfile.mktemp(prefix="pons_snipers_", suffix=".db")
C.DB_PATH = _TMP

import store  # noqa: E402  (must follow the DB_PATH swap)
import xsource  # noqa: E402

store.init()

# The server as well as the store: some of what is pinned below is the server's
# half of a decision - which rows a walk is allowed to write, what a failed
# reading means. It imports cleanly and starts nothing; the app object is built
# but its lifespan, and with it the indexer, only runs under uvicorn.
import server as srv  # noqa: E402

ok_all = True


def case(name, got, want):
    global ok_all
    ok = got == want
    ok_all &= ok
    print("%-4s %-52s got=%r want=%r" % ("ok" if ok else "FAIL", name, got, want))
    return ok


def note(name, text):
    print("     %-52s %s" % (name, text))


# --------------------------------------------------------------- the fixture
#
# DEV is the deployer, ROUTER is what the launch transaction records as the
# buyer of its own bundle buy, SNIP is the wallet under test.
DEV = "0xDeployer00000000000000000000000000000001"
DEV2 = "0xDeployer00000000000000000000000000000002"
ROUTER = "0xRouter0000000000000000000000000000000001"
SNIP = "0xSniper0000000000000000000000000000000001"
OTHER = "0xOther0000000000000000000000000000000001"
CONTRACT = C.MULTICALL3

TOKENS = [
    # A: the headline case. The router's row on the launch transaction sits at
    # log_index 5 of block 1000; SNIP arrives five blocks later and is not first.
    dict(address="0xTokA", deployer=DEV, launch_block=1000, launch_tx="0xLA",
         symbol="AAA", twitter="https://x.com/aaa", quote_symbol="ETH",
         launch_ts=1000, quote_decimals=18, graduation_threshold=4.2e18),
    # B: nobody bundled; SNIP is first, two blocks behind the launch.
    dict(address="0xTokB", deployer=DEV, launch_block=2000, launch_tx="0xLB",
         symbol="BBB", twitter="@bbb", quote_symbol="ETH",
         launch_ts=2000, quote_decimals=18, graduation_threshold=4.2e18),
    # C: SNIP buys inside the launch block itself.
    dict(address="0xTokC", deployer=DEV2, launch_block=3000, launch_tx="0xLC",
         symbol="CCC", twitter="x.com/ccc", quote_symbol="ETH",
         launch_ts=3000, quote_decimals=18, graduation_threshold=4.2e18),
    # D..G: four launches SNIP bought into all in block 5000, for the paging
    # stability case - equal sort keys are the thing that loses rows. Their
    # launch blocks differ so the lag histogram has something in every bucket.
    dict(address="0xTokD", deployer=CONTRACT, launch_block=4999, launch_tx="0xLD",
         symbol="DDD", twitter="", quote_symbol="ETH", launch_ts=4000,
         quote_decimals=18, graduation_threshold=4.2e18),
    dict(address="0xTokE", deployer=DEV2, launch_block=4997, launch_tx="0xLE",
         symbol="EEE", twitter="ddd", quote_symbol="ETH", launch_ts=4000,
         quote_decimals=18, graduation_threshold=4.2e18),
    dict(address="0xTokF", deployer=DEV2, launch_block=4995, launch_tx="0xLF",
         symbol="FFF", twitter="", quote_symbol="ETH", launch_ts=4000,
         quote_decimals=18, graduation_threshold=4.2e18),
    dict(address="0xTokG", deployer=DEV2, launch_block=4994, launch_tx="0xLG",
         symbol="GGG", twitter="", quote_symbol="ETH", launch_ts=4000,
         quote_decimals=18, graduation_threshold=4.2e18),
    # H: quoted in USDG, so the median must not be pooled with the ETH rows.
    dict(address="0xTokH", deployer=CONTRACT, launch_block=6000, launch_tx="0xLH",
         symbol="HHH", twitter="hhh", quote_symbol="USDG", launch_ts=6000,
         quote_decimals=6, graduation_threshold=4.2e18),
]

# (token, block, log_index, buyer, quote, tx)
BUYS = [
    ("0xTokA", 1000, 5, ROUTER, 5.0e16, "0xLA"),   # the launch transaction
    ("0xTokA", 1005, 3, SNIP, 2.0e16, "0xb1"),     # beaten by the router
    ("0xTokA", 1006, 1, OTHER, 1.0e16, "0xb2"),
    ("0xTokB", 2002, 4, SNIP, 3.0e16, "0xb3"),     # first, delta 2
    ("0xTokB", 2003, 1, OTHER, 1.0e16, "0xb4"),
    ("0xTokC", 3000, 1, SNIP, 1.0e16, "0xb5"),     # first, delta 0
    ("0xTokD", 5000, 7, SNIP, 4.0e16, "0xb6"),
    ("0xTokE", 5000, 3, SNIP, 5.0e16, "0xb7"),
    ("0xTokF", 5000, 9, SNIP, 6.0e16, "0xb8"),
    ("0xTokG", 5000, 1, SNIP, 7.0e16, "0xb9"),
    ("0xTokH", 6000, 2, SNIP, 12_500_000, "0xba"),  # 12.5 USDG at 6 decimals
]

c = store.conn()
cols = ("address", "deployer", "launch_block", "launch_tx", "symbol", "twitter",
        "quote_symbol", "launch_ts", "quote_decimals", "graduation_threshold")
for t in TOKENS:
    vals = dict(t)
    vals.setdefault("curve", "0xC" + t["address"][4:])
    vals.setdefault("log_index", 0)
    vals.setdefault("graduated", 0)
    vals.setdefault("progress_pct", 10.0)
    vals.setdefault("mcap_usd", 1000.0)
    keys = list(vals.keys())
    c.execute("INSERT INTO tokens (%s) VALUES (%s)"
              % (",".join(keys), ",".join("?" * len(keys))),
              [vals[k] for k in keys])
c.executemany(
    "INSERT INTO early_buys(address,block,log_index,ts,buyer,quote,tokens,tx) "
    "VALUES(?,?,?,?,?,?,?,?)",
    [(a, b, li, b, who, q, 1.0, tx) for (a, b, li, who, q, tx) in BUYS])
# The snipe counter is fed from `unfinalized_snipes`, which needs a trades row
# per launch; one row per token with snipe_counted at its default of 0.
c.executemany("INSERT INTO trades(address, curve, snipe_counted) VALUES(?,?,0)",
              [(t["address"], "0xC" + t["address"][4:]) for t in TOKENS])
c.commit()

ALL_TOKENS = [t["address"] for t in TOKENS]
SNIPE_ADDRS = {r["address"] for r in store.sniper_snipes(SNIP) if r["first"]}
ALL_ADDRS = {r["address"] for r in store.sniper_snipes(SNIP)}

# ------------------------------------------------- 1. the launch tx is kept
rows = store.sniper_snipes(SNIP)
by_tok = {r["address"]: r for r in rows}
case("1 the launch tx row is not excluded", by_tok["0xTokA"]["first"], 0)
case("1 the router, not the sniper, was first into 0xTokA",
     store.first_racers(["0xTokA"])["0xTokA"]["buyer"], ROUTER)
note("1a snipes for this wallet", "%.0f" % len(SNIPE_ADDRS))
note("1b buys in a launch window", "%.0f" % len(ALL_ADDRS))

# The definition the project already had, against the window-function mistake.
_win = """WITH racer AS (
  SELECT e.address, ROW_NUMBER() OVER (PARTITION BY e.address
           ORDER BY e.block, e.log_index) rn
  FROM early_buys e JOIN tokens t ON t.address = e.address
  WHERE e.buyer = ? AND e.buyer <> t.deployer)
SELECT COUNT(*) FROM racer WHERE rn = 1"""
win_count = c.execute(_win, (SNIP,)).fetchone()[0]
note("1c the window-function version would say", "%d" % win_count)
case("1d ... which is every token he touched, not his snipes",
     win_count, len(ALL_ADDRS))
case("1e ... and is strictly wrong", win_count > len(SNIPE_ADDRS), True)

# ------------------------------------- 2. hits and sniper_tokens agree
_rows = store.unfinalized_snipes(10 ** 9)
store.count_snipes(_rows, store.first_racers([r["address"] for r in _rows]))
hits = c.execute("SELECT hits FROM sniper_wallets WHERE address=?",
                 (SNIP,)).fetchone()
hits = int(hits[0]) if hits else 0
listed = len(store.sniper_tokens([SNIP], per_wallet=0).get(SNIP, []))
case("2 hits equals the sniper_tokens count", hits, listed)
note("2a hits / sniper_tokens / snipes", "%d / %d / %d"
     % (hits, listed, len(SNIPE_ADDRS)))
case("2b and both equal the firsts from sniper_snipes", hits, len(SNIPE_ADDRS))

# --------------------------- 3. first_racers and sniper_snipes agree
racers = store.first_racers(ALL_TOKENS)
racers_of_snip = {a for a, r in racers.items() if r["buyer"] == SNIP}
case("3 first_racers finds the same tokens as first=1",
     racers_of_snip, SNIPE_ADDRS)

# --------------------------- 4. deployer_profiles and deployer_counts agree
deps = store.deployer_profiles([t["deployer"] for t in TOKENS])
counts = store.deployer_counts([t["deployer"] for t in TOKENS])
case("4 deployer_profiles(x).launches == deployer_counts(x)",
     {d: p["launches"] for d, p in deps.items()}, counts)
case("4a the contract is a deployer too", deps[CONTRACT]["launches"], 2)
case("4b its first launch block", deps[CONTRACT]["first_block"], 4999)
case("4c an unread deployer has state None, not a zero nonce",
     deps[DEV]["state"], None)

# --------------------------- 5. handles_for_tokens normalises both spellings
ht = store.handles_for_tokens(ALL_TOKENS)
case("5 a url and an @name normalise the same",
     (ht.get("0xTokA"), ht.get("0xTokB"), ht.get("0xTokC")),
     ("aaa", "bbb", "ccc"))
case("5a prose and empty claim nothing", ht.get("0xTokD"), None)

# --------------------------- 7. the denominators are named and add up
crit = store.sniper_criteria(rows, deps)
case("7 denominator is named", crit["denominator"], "first")
case("7a snipes is the count of firsts", crit["snipes"], len(SNIPE_ADDRS))
case("7b firsts + lost_races == every buy in a window",
     crit["contested"]["firsts"] + crit["contested"]["lost_races"],
     crit["contested"]["early_buys"])
case("7c win_rate is over every buy in a window",
     round(crit["contested"]["win_rate"], 6),
     round(len(SNIPE_ADDRS) / len(ALL_ADDRS), 6))
case("7d deltas over the firsts only",
     (crit["median_delta"], crit["delta_hist"]),
     (2, {"0": 2, "1": 1, "2": 1, "3-5": 2, "6+": 1}))
case("7e deployers counted", crit["deployers"], 3)
case("7f the top deployer is the one with four firsts",
     (crit["top_deployer"]["address"], crit["top_deployer"]["snipes"]),
     (DEV2, 4))

# --------------------------- 10. money is never pooled across quote assets
# The medians are in the units a person reads, which means the decimals of each
# quote asset have been divided out. `early_buys.quote` is the raw amount in the
# smallest unit, so the ETH median of 4.5e16 wei is 0.045 ETH and the USDG one
# of 12_500_000 is 12.5 USDG. Pinned here because printing the raw numbers is
# what the tab did at first: every figure was a billion times too large and the
# median buy read "221889252.58 ETH".
mix = {m["symbol"]: (m["n"], m["median"]) for m in crit["quote_mix"]}
case("10 ETH and USDG get their own median", mix,
     {"ETH": (6, 0.045), "USDG": (1, 12.5)})
case("10a no field pools the two",
     [k for k in crit if "spent" in k or "volume" in k], [])

# --------------------------- 8. paging over equal sort keys is stable
ordered, applied = store.sniper_order(list(rows), "block_desc")
case("8 the default order is block_desc", applied, ["block_desc"])
_pages = [ordered[i:i + 3] for i in range(0, len(ordered), 3)]
_seen = [r["address"] for p in _pages for r in p]
case("8a three per page loses and repeats nothing",
     (len(_seen), len(set(_seen))), (len(rows), len(rows)))
case("8b the same request twice gives the same order",
     [r["address"] for r in ordered],
     [r["address"] for r in store.sniper_order(list(rows), "block_desc")[0]])
case("8c log_index breaks the tie downwards for a desc key",
     [r["log_index"] for r in ordered if r["block"] == 5000], [9, 7, 3, 1])
case("8d and upwards for an ascending one",
     [r["log_index"] for r in
      store.sniper_order(list(rows), "block_asc")[0] if r["block"] == 5000],
     [1, 3, 7, 9])

# --------------------------- 9. sort keys that depend on the cache
case("9 an unknown key falls back to the default",
     store.sniper_order(list(rows), "nonsense")[1], [store.SNIPE_SORT_DEFAULT])
case("9a sort2 alone does not apply",
     store.sniper_order(list(rows), "", "followers_desc")[1],
     [store.SNIPE_SORT_DEFAULT])
case("9b two keys are echoed in order",
     store.sniper_order(list(rows), "delta_asc", "quote_desc")[1],
     ["delta_asc", "quote_desc"])

# Only some rows have a nonce, exactly as on the live table.
for r in rows:
    r["d_nonce"] = 7 if r["address"] in ("0xTokD", "0xTokE") else None
_d, _ = store.sniper_order(list(rows), "deployer_nonce_desc")
case("9c a known nonce sorts above the unknown ones", _d[0]["address"], "0xTokD")
case("9d unknowns go last in a descending order",
     {r["address"] for r in _d[-len(rows) + 2:]},
     {r["address"] for r in rows} - {"0xTokD", "0xTokE"})
_u, _ = store.sniper_order(list(rows), "followers_asc")
_rows_missing = [r for r in _u if r.get("followers") is None]
case("9e unknowns go last in an ascending order too",
     _rows_missing[-1] is _u[-1], True)

# --------------------------- 11. x_lookups round trip (Phase 3 groundwork)
store.x_lookups_put([{"handle": "@AAA", "state": "missing", "error": "no such"}])
got = store.x_lookups_get(["aaa", "never-asked"])
case("11 a lookup is remembered under a normalised handle",
     (got["aaa"]["state"], got["aaa"]["error"]), ("missing", "no such"))
case("11a a handle never asked for is absent, not missing",
     "never-asked" in got, False)
case("11b x_users_by_handle on nothing is empty",
     store.x_users_by_handle(["aaa"]), {})

# --------------------------- 11c. a walk that failed does not leave facts
# The other half of `test_xwalk.py` case 5. `xsource.profiles` reports a failed
# reading to its caller and refuses to answer for that handle; this is the
# caller, and what it must not do is write the failure down. `x_lookups` is only
# ever asked once per handle, so a network error recorded as "missing" would be
# a permanent claim that an account does not exist, made from a dropped socket.
OLD_PROFILES = xsource.profiles


def _fake_profiles(handles, pace=0, on_result=None, should_stop=None):
    """One handle answers, one does not exist, one dies on the wire."""
    for h in handles:
        if h == "ok-one":
            on_result(h, {"id": "1", "handle": h, "followers": 5}, None)
        elif h == "ghost-one":
            on_result(h, None, None)
        else:
            on_result(h, None, xsource.XUnavailable("connection reset"))
    return {}


#
# `src` is bound to the run the server keeps in `_X_RUNS`: the walker returns
# immediately for a run id it does not know, so a case that called it without
# registering one would pass by doing nothing at all.
xsource.profiles = _fake_profiles
srv_run = srv._X_RUNS.setdefault("test-run-11c", {})
srv_run.update({"id": "test-run-11c", "kind": "profiles", "address": "0xNobody",
                "state": "running", "walked": 0, "total": 0, "error": "",
                "started_at": int(time.time()), "updated_at": int(time.time()),
                "finished_at": None, "_stop": False})
srv_handles = ["ok-one", "ghost-one", "flaky-one"]
try:
    srv._run_x_profiles("test-run-11c", "0xNobody", srv_handles, 10)
finally:
    xsource.profiles = OLD_PROFILES
case("11c the walk ran rather than returning on an unknown run",
     (srv_run.get("walked"), srv_run.get("state")), (3, "ok"))
rows11 = store.x_lookups_get(srv_handles)
case("11c a handle that answered is written ok",
     (rows11.get("ok-one") or {}).get("state"), "ok")
case("11d a handle that does not exist is written missing",
     (rows11.get("ghost-one") or {}).get("state"), "missing")
case("11e a handle that died on the wire is written nowhere",
     "flaky-one" in rows11, False)

# --------------------------- the schema is where it is supposed to be
tables = {r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
case("12 both new tables exist",
     {"deployer_chain", "x_lookups"} <= tables, True)

# --------------------------- 5. a deployer that is a contract
# Multicall3 is the deployer of 4727 tokens on the live index and carries nonce
# 1 and a zero balance. Without the bytecode reading it is indistinguishable
# from a wallet that launched once and is now empty - and that wallet is exactly
# what a sniper's criteria are built to find. So the contract is pinned here in
# both directions: it must be marked a contract, its nonce must be kept out of
# the median, and a wallet with the same nonce must not be marked at all.
# what a row renders as, and the two other things waiting on the same modules
M3 = C.MULTICALL3
W_ZERO = "0xWa11et0000000000000000000000000000000001"   # nonce 0, never sent
W_HOLE = "0xWa11et0000000000000000000000000000000002"   # count did not arrive
W_NOC = "0xWa11et0000000000000000000000000000000003"    # bytecode did not
BIG = 9_200_000_000_000_000_000                         # 9.2 ETH, over int64

rows5 = srv._sw_rows(
    [M3, W_ZERO, W_HOLE, W_NOC],
    {M3: 1, W_ZERO: 0, W_NOC: 77},
    {M3: 3808, W_ZERO: 0, W_HOLE: 0},
    {M3: 0, W_ZERO: BIG},
    64_000_000)
by = {r["address"]: r for r in rows5}
case("5a a deployer with bytecode is a contract",
     (by[M3]["state"], by[M3]["code"], by[M3]["nonce"]), ("contract", 1, 1))
case("5b a wallet with no code and a real zero stays a wallet",
     (by[W_ZERO]["state"], by[W_ZERO]["code"], by[W_ZERO]["nonce"]),
     ("ok", 0, 0))
case("5c a wallet whose count did not arrive is an error, not a hole in an ok",
     (by[W_HOLE]["state"], by[W_HOLE].get("nonce")), ("error", None))
case("5d no bytecode at all is an error even with a count in hand",
     (by[W_NOC]["state"], by[W_NOC].get("code")), ("error", None))
case("5e a failed balance does not spoil a row", by[W_ZERO]["balance_wei"], BIG)

# the round trip, because balance_wei is TEXT on purpose: 9.2 ETH is the signed
# 64-bit ceiling in wei, and the value above it is the one that would break
store.deployer_chain_put(rows5)
back = store.deployer_chain_get([M3, W_ZERO, W_HOLE, W_NOC])
case("5f 9.2 ETH survives the round trip as a string",
     int(back[W_ZERO]["balance_wei"]), BIG)
case("5g a zero balance is stored as zero and not as absent",
     back[M3]["balance_wei"], "0")

# the aggregate: the contract is counted, and its nonce is not in the median
now5 = int(time.time())
deps5 = store.deployer_chain_get([M3, W_ZERO])
store.deployer_chain_put([{"address": M3, "state": "contract", "code": 1,
                           "nonce": 1, "balance_wei": 0},
                          {"address": W_ZERO, "state": "ok", "code": 0,
                           "nonce": 400, "balance_wei": BIG}])
sum5 = srv._snipe_chain_summary([M3, W_ZERO, W_HOLE], store.deployer_chain_get(
    [M3, W_ZERO, W_HOLE]))
case("5h the contract is counted as a contract", sum5["contracts"], 1)
case("5i the median nonce is the wallet's and not the contract's",
     (sum5["median_nonce"], sum5["nonce_known"]), (400, 1))
case("5j an error row is retryable and so is not coverage",
     (sum5["read"], sum5["missing"]), (2, 1))

# a failed reading must be asked again; a good one inside the TTL must not
case("5k an error state is missing, not read",
     srv._chain_state({"state": "error"}, now5), "missing")
case("5l a fresh ok row is read",
     srv._chain_state({"state": "ok", "fetched_at": now5}, now5), "read")
case("5m a fresh contract row is read",
     srv._chain_state({"state": "contract", "fetched_at": now5}, now5), "read")
case("5n a row past the TTL is stale",
     srv._chain_state({"state": "ok", "fetched_at": now5 - C.SW_CHAIN_TTL - 1},
                      now5), "stale")
case("5o a row that was never read is missing",
     srv._chain_state(None, now5), "missing")

# and what the row renders as: the page prints `contract`, not a number
dep5 = srv._snipe_deployer(store.deployer_chain_get([M3])[M3])
case("5p the deployer payload marks it a contract", dep5["is_contract"], True)
case("5q and carries the balance as a float of ether", dep5["balance"], 0.0)

# --------------------------- 13. the other chains, one state each
# A deployer's transaction count on the chains this indexer does not read. Three
# things here are easy to get wrong and each one turns a missing reading into a
# stated fact:
#
#   * a chain that refused the call is not the same as a chain nobody asked, and
#     neither of them is a count of zero - the whole table rests on the
#     difference between "0 sent" and "we did not find out";
#   * the state is per chain and not per address, because Ethereum answering
#     while Arbitrum refuses is an ordinary outcome and drawing both the same way
#     would have to call one of them a lie;
#   * the key is (address, chain) and not (address), because the same wallet has
#     a different count on each, and a row that lost its chain is a number with
#     nothing left to say what it counts.
ETH, ARB = 1, 42161
XA = "0xDep10yer00000000000000000000000000000001"
XB = "0xDep10yer00000000000000000000000000000002"

# this case runs before the walk below writes anything, so at this point the
# table is empty and every chain is unasked rather than zero
case("13a a chain nobody has asked about is missing, not zero",
     srv._ext_state(None, ETH, now5), "missing")
case("13b an address the table has never held has no rows at all",
     store.deployer_chain_ext_get([XA]), {})

store.deployer_chain_ext_put([
    {"address": XA, "chain_id": ETH, "nonce": 0, "state": "ok",
     "fetched_at": now5, "error": ""},
    {"address": XA, "chain_id": ARB, "nonce": None, "state": "error",
     "fetched_at": now5, "error": "the node did not answer"},
    {"address": XB, "chain_id": ETH, "nonce": 412, "state": "ok",
     "fetched_at": now5, "error": ""},
])
ext13 = store.deployer_chain_ext_get([XA, XB])
case("13c the two chains of one address keep separate states",
     (srv._ext_state(ext13[XA], ETH, now5),
      srv._ext_state(ext13[XA], ARB, now5)), ("read", "missing"))
case("13d a real zero is a reading and survives as one",
     (ext13[XA][ETH]["nonce"], ext13[XA][ETH]["state"]), (0, "ok"))
case("13e the address with no row on a chain simply has no entry for it",
     ARB in ext13[XB], False)
# `per_chain` is keyed by chain, so a row handed in bare answers for no chain at
# all - which is the same result as never having asked, and is why the two
# shapes are worth keeping apart.
case("13f a bare row is not a reading for any chain",
     srv._ext_state({"state": "ok", "fetched_at": now5}, ETH, now5), "missing")
# The TTL is deliberately its own constant and not SW_CHAIN_TTL: a nonce on
# Ethereum moves at Ethereum's pace, which has nothing to do with how fresh this
# indexer's own block is. Pinning the binding rather than the value, because two
# independent constants are allowed to hold the same number today - what must
# not happen is one of them being read for the other.
_ttl_e, _ttl_c = C.SW_EXT_TTL, C.SW_CHAIN_TTL
try:
    C.SW_EXT_TTL, C.SW_CHAIN_TTL = 60.0, 10000.0
    old_row = {"state": "ok", "fetched_at": now5 - 120}
    case("13g a chain past its own TTL is stale",
         srv._ext_state({ETH: old_row}, ETH, now5), "stale")
    case("13h the external reader uses the external TTL, not the local one",
         srv._chain_state(old_row, now5), "read")
finally:
    C.SW_EXT_TTL, C.SW_CHAIN_TTL = _ttl_e, _ttl_c

case("13i ext is empty rather than absent when nothing has been read",
     srv._snipe_deployer({"address": XA})["ext"], {})

# deployer_profiles has to hand back `ext` too, and keyed by chain - the page
# prints these three side by side and there is no second call that fills them in
prof13 = store.deployer_profiles([XA])
case("13j deployer_profiles carries the external readings",
     sorted(prof13[XA]["ext"]), [ETH, ARB])
case("13k and keeps each chain's state apart inside one address",
     (prof13[XA]["ext"][ETH]["state"], prof13[XA]["ext"][ARB]["state"]),
     ("ok", "error"))

# what the row itself renders from
dep13 = srv._snipe_deployer(prof13[XA])
case("13l the deployer payload carries the chains it was read on",
     (dep13["ext"][ETH]["nonce"], dep13["ext"][ARB]["state"]), (0, "error"))
case("13m a deployer nobody has read has an empty ext and not a null one",
     srv._snipe_deployer(None)["ext"], {})

sum13 = srv._snipe_chain_summary([XA, XB], prof13)
# Keyed by chain_id here as well as in the rows, because a chain read for every
# address must not print like a chain that was never configured - which is what
# a lookup that misses quietly does.
case("13n the summary reports coverage one chain at a time",
     (sum13["ext"][ETH]["read"], sum13["ext"][ETH]["missing"]), (1, 1))
case("13o and does not credit the chain that refused",
     (sum13["ext"][ARB]["read"], sum13["ext"][ARB]["missing"]), (0, 2))
case("13p the coverage is keyed the same way the rows are",
     sorted(sum13["ext"]), sorted(prof13[XA]["ext"]))
# Zero readings and no questions are different answers, and the page says so in
# different words. A chain that refused every address has a row for each of them
# and nothing to show, and reporting that as a chain nobody has tried would draw
# a node that is down as a node we have not got to yet.
case("13q the chain that answered counts one asked and one read",
     (sum13["ext"][ETH]["asked"], sum13["ext"][ETH]["read"]), (1, 1))
case("13r the chain that refused every address was still asked",
     (sum13["ext"][ARB]["asked"], sum13["ext"][ARB]["read"]), (1, 0))

# --------------------------- 14. the walk writes a refusal, it does not skip it
# The one thing the external walk must not do is leave a failed read unwritten.
# No row means "never asked" and the page draws it that way, so a chain that was
# asked and refused would look like a chain nobody has tried - and every press
# would ask it again forever without ever saying why. A fake reader is used so
# the case pins the walk's bookkeeping rather than the network's mood.
ADDRS14 = ["0xWa11et0000000000000000000000000000000011",
           "0xWa11et0000000000000000000000000000000012"]
RUN14 = "test-run-14"
srv._SW_RUNS[RUN14] = {
    "address": RUN14, "state": "running", "error": "", "total": 2, "done": 0,
    "read": 0, "contracts": 0, "errors": 0, "ext_errors": 0, "block": None,
    "calls": 0, "per_sec": 0, "eta_sec": 0, "started_at": now5,
    "updated_at": now5, "finished_at": None, "_stop": False,
}
OLD_TC = srv.chain.transaction_counts
OLD_TCX = srv.chain.transaction_counts_ext
OLD_CS = srv.chain.code_sizes
OLD_BAL = srv.chain.balances
OLD_BN = srv.chain.block_number
try:
    srv.chain.block_number = lambda: 12345
    srv.chain.transaction_counts = lambda a, workers=0: {x: 3 for x in a}
    srv.chain.code_sizes = lambda a, workers=0: {x: 0 for x in a}
    srv.chain.balances = lambda a: {x: 0 for x in a}
    # Ethereum answers and one of the two has sent nothing; Arbitrum refuses.
    srv.chain.transaction_counts_ext = lambda cid, a, workers=0: (
        {ADDRS14[0]: 0, ADDRS14[1]: 9} if cid == ETH else {})
    srv._run_chain_walk(RUN14, list(ADDRS14))
finally:
    srv.chain.transaction_counts = OLD_TC
    srv.chain.transaction_counts_ext = OLD_TCX
    srv.chain.code_sizes = OLD_CS
    srv.chain.balances = OLD_BAL
    srv.chain.block_number = OLD_BN

ext14 = store.deployer_chain_ext_get(ADDRS14)
case("14a the chain that answered is written ok, zero included",
     [(ext14[a][ETH]["state"], ext14[a][ETH]["nonce"]) for a in ADDRS14],
     [("ok", 0), ("ok", 9)])
case("14b the chain that refused is written as an error and not skipped",
     [(a, ARB in ext14[a]) for a in ADDRS14],
     [(ADDRS14[0], True), (ADDRS14[1], True)])
case("14c and the refusal carries why it failed",
     all(ext14[a][ARB]["state"] == "error" and ext14[a][ARB]["error"]
         for a in ADDRS14), True)
case("14d the run counts the refusals it hit",
     srv._SW_RUNS[RUN14]["ext_errors"], 2)
case("14e the local chain still walked the same addresses",
     srv._SW_RUNS[RUN14]["read"], 2)
# A refused chain is retryable, so it is not coverage - the same rule the local
# chain follows, one level down.
case("14f a refusal does not count as a reading for the page",
     srv._ext_state(store.deployer_chain_ext_get([ADDRS14[0]])[ADDRS14[0]],
                    ARB, int(time.time())), "missing")

# --------------------------- the ext table is where it is supposed to be
case("15 the external chain table exists",
     "deployer_chain_ext" in {r[0] for r in c.execute(
         "SELECT name FROM sqlite_master WHERE type='table'")}, True)
case("15a its key is (address, chain) so one address can hold three",
     [r[0] for r in c.execute(
         "SELECT name FROM pragma_table_info('deployer_chain_ext') "
         "WHERE pk > 0 ORDER BY pk")], ["address", "chain_id"])

print()
print("ALL CASES PASS:", bool(ok_all))
c.close()
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_TMP + suffix)
    except OSError:
        pass
sys.exit(0 if ok_all else 1)
