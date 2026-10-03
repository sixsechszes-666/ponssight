"""The Wallets tab: the rows must not wait on the chain.

Two things this file exists to keep true, and both of them were the bug.

  1. Drawing the table is a SQLite read and nothing else. `_keywallet_list` used
     to make one live RPC call per stored wallet plus one batched call to the
     other chain, inside the request that draws the rows, through the same gate
     and the same eight slots the indexer holds. Measured on the live dashboard,
     /api/keywallets took 638, 942, 1139, 1652, 3701 and 10152 ms while
     /api/wallets and /api/stats answered in 3 to 30 ms in the same seconds, and
     in two of eight page loads it never answered at all - the page gives up at
     15 s, `kw.wallets` stays empty, and the tab prints "no wallets saved yet"
     over a wallet that is sitting in the database. Case 1 fails if any live
     reading comes back into that call.

  2. `key_wallet_chain` is where those numbers live between readings, and its
     value columns are TEXT on purpose: 9.2 ETH is the signed 64-bit ceiling in
     wei, so a balance above it would break in an INTEGER column silently, and
     the whole point of this tab is a balance spent to the last wei. Case 7 pins
     the schema itself.

Nothing here touches the network. The storage layer is pointed at a throwaway
file before it opens anything, for the reason test_snipers.py gives: a test
asserting a live number is a test of when it was run.
"""
import json
import os
import sys
import tempfile
import time

from eth_account import Account
from fastapi import HTTPException

sys.path.insert(0, ".")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config as C

# Point the whole storage layer at a throwaway file before it opens anything.
# conn() reads C.DB_PATH on first use per thread, so this has to happen before
# the first store.conn() call anywhere.
_TMP = tempfile.mktemp(prefix="pons_keywallets_", suffix=".db")
C.DB_PATH = _TMP

import store  # noqa: E402  (must follow the DB_PATH swap)

store.init()

# The server as well as the store: what is pinned here is the server's half of
# the decision - which request is allowed to read a balance, and what a failed
# reading looks like afterwards. It imports cleanly and starts nothing; the app
# object is built but its lifespan, and with it the indexer, only runs under
# uvicorn.
import server as srv  # noqa: E402

ok_all = True


def case(name, got, want):
    global ok_all
    ok = got == want
    ok_all &= ok
    print("%-4s %-56s got=%r want=%r" % ("ok" if ok else "FAIL", name, got, want))
    return ok


def note(name, text):
    print("     %-56s %s" % (name, text))


# --------------------------------------------------------------- the fixture
#
# Two stored wallets, written straight into the table rather than through
# `store.keywallet_add`. That function encrypts under the vault, and the vault
# is not what any of this is about: the list draws from the stored mask and
# never opens a keystore, which is the property that lets the table draw at all
# while the vault is shut. A fake blob is therefore the honest fixture - and it
# doubles as a check that nothing in the drawing path tries to unwrap it.
#
# The addresses look like addresses (and not like `0xWa11et...`) because two of
# the routes under test parse theirs: `_addr` checksums what it is given and
# answers 400 to anything else, and a fixture that could not survive that would
# quietly move the test off the path the page takes.
W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"
BIG = 9_200_000_000_000_000_000                       # 9.2 ETH, over int64

c = store.conn()
c.executemany(
    "INSERT INTO key_wallets(address,label,secret,mask,added_at) VALUES(?,?,?,?,?)",
    [(W1, "main", "not-a-real-keystore", "0xaa...aaaa", 1000),
     (W2, None, "not-a-real-keystore", "0xbb...bbbb", 2000)])
c.commit()

# ------------------------------------------------- 1. the list does not read
# Every reading the old code made, replaced with one that refuses to be called.
# The point is not that they are slow - it is that they were reachable at all
# from a request whose whole job is to print rows that are already in the
# database, and that the page treats a slow answer as no answer.
def _boom(*a, **k):
    raise AssertionError("the list went to the chain")


_old = (srv.chain.balance, srv.chain.balances, srv.bridge.arb_balances)
err = ""
try:
    srv.chain.balance = _boom
    srv.chain.balances = _boom
    srv.bridge.arb_balances = _boom
    d = srv._keywallet_list()
except AssertionError as e:
    d, err = None, str(e)
finally:
    srv.chain.balance, srv.chain.balances, srv.bridge.arb_balances = _old

case("1 drawing the list does not go to the chain", err, "")
rows = (d or {}).get("wallets") or []
case("1a both stored wallets are in the answer", len(rows), 2)
case("1b and the newest one is first",
     [r["address"] for r in rows], [W2, W1])
case("1c the mask is drawn and no keystore is opened",
     [r["secret_mask"] for r in rows], ["0xbb...bbbb", "0xaa...aaaa"])
# Nothing has been read yet, so the numbers are not known - and "not known" is
# not zero. This is the distinction the whole table rests on, and it is drawn
# differently (a dim dash, not a 0) for exactly this reason.
case("1d a wallet nobody has read is null on both chains",
     [(r["rh_wei"], r["arb_wei"]) for r in rows], [(None, None), (None, None)])

# ---------------- 2. a zero is a reading, a missing row is not
# These two have to be told apart *in the same answer*, because the page draws
# them differently - a dim dash for "nobody has asked" and a "0" for "this
# wallet holds nothing" - and a table that rendered both as 0 would make an
# unread wallet look like an empty one. So the fixture for this case is the two
# of them standing side by side.
store.keywallet_chain_put([W1], {W1: 0}, {})
d2 = srv._keywallet_list()
rows2 = d2["wallets"]
case("2 the row order is unchanged by any of this",
     [r["address"] for r in rows2], [W2, W1])
case("2a the wallet with a cached zero shows a zero, the other a null",
     [(r["rh_wei"], r["arb_wei"]) for r in rows2], [(None, None), (0, None)])
case("2b and the zero is not stale either", d2["chain"]["rh"]["age"], 0)
# A zero came back for robinhood and nothing came back for Arbitrum: the
# caption must not blame the chain that answered.
case("2c the caption speaks for the last attempt, not for every row",
     (d2["chain"]["rh"]["error"], bool(d2["chain"]["arb"]["error"])), ("", True))

# ---------------- 3. the second read inside the TTL is not made
# Counting stubs and no network: the question is how many times the read is
# asked for, and the answer has to be once per TTL and not once per look. The
# numbers are per address, so a batch that returned one number for the whole
# list would fail the assertion just as loudly as a second read would.
reads = {"rh": 0, "arb": 0}


def _rh_count(addrs):
    reads["rh"] += 1
    return {a: 1000 + int(a[-1]) for a in addrs}


def _arb_count(addrs):
    reads["arb"] += 1
    return {a: 2000 + int(a[-1]) for a in addrs}


def _settle(what):
    """Wait for the read the route started to finish, and say how it ended.

    The route hands the work to a thread, so the test has to wait for it - but
    only for the thread, never for the chain: the stubs above answer instantly,
    so this is a wait on a local flag and not on a node.
    """
    deadline = time.time() + 10
    while time.time() < deadline:
        run = srv._kw_run()
        if run and run["state"] != "running":
            return run["state"]
        time.sleep(0.05)
    return "never finished (" + what + ")"


_ttl_before = C.KEY_CHAIN_TTL
_old3 = (srv.chain.balances, srv.bridge.arb_balances)
try:
    srv.chain.balances, srv.bridge.arb_balances = _rh_count, _arb_count
    # Nothing has been tried yet, which is the state a page that has just been
    # opened is in - and the one where `next_at` is None and the press has to be
    # due, because a table with no readings has no clock to wait on.
    store.keywallet_chain_drop([W1, W2])
    first = srv.api_keywallet_chain()
    case("3 the first press starts the read", first["started"], True)
    case("3a and says it is running", first["chain"]["running"], True)
    note("3b the read finished", _settle("first"))
    case("3c both halves were read exactly once",
         (reads["rh"], reads["arb"]), (1, 1))
    case("3d and each address got its own number",
         [(r["rh_wei"], r["arb_wei"]) for r in srv._keywallet_list()["wallets"]],
         [(1002, 2002), (1001, 2001)])
    second = srv.api_keywallet_chain()
    case("3e a press inside the TTL starts nothing", second["started"], False)
    case("3f and does not read the chain again",
         (reads["rh"], reads["arb"]), (1, 1))
    case("3g because it is not due and says when it will be",
         (second["chain"]["due"], second["chain"]["next_at"] >
          second["chain"]["now"]), (False, True))
    # The knob, turned the other way: at a TTL of zero every press is due, so
    # the refusal above is the TTL talking and not a flag that never clears.
    C.KEY_CHAIN_TTL = 0
    third = srv.api_keywallet_chain()
    case("3h at a TTL of zero the same press is due", third["started"], True)
    note("3i the second read finished", _settle("third"))
    case("3j and the counters moved", (reads["rh"], reads["arb"]), (2, 2))
finally:
    C.KEY_CHAIN_TTL = _ttl_before
    srv.chain.balances, srv.bridge.arb_balances = _old3

# ---------------- 4. one half's refusal does not erase the other's number
# The two readings come from two different hosts and they fail independently,
# which is the ordinary case rather than the exotic one. The rule is that a
# column this reading gave no number for keeps the number and the stamp it
# already had - a failed read has no right to destroy a good number - while the
# refusal is written unconditionally, because a refusal is the last word about
# that column and not a value.
store.keywallet_chain_put([W1], {W1: 1000}, {W1: 2000})
# Stamped by hand so the two halves can be told apart: both puts below happen
# inside one second, and a test that could not tell the stamps apart could not
# tell a restamped value from an untouched one.
c.execute("UPDATE key_wallet_chain SET rh_at=1111, arb_at=2222 WHERE address=?",
          (W1,))
c.commit()
r4 = store.keywallet_chain_get([W1])[W1]
case("4 the first reading stores both halves",
     (r4["rh_wei"], r4["arb_wei"]), ("1000", "2000"))
case("4a and neither half carries a refusal",
     (r4["rh_err"], r4["arb_err"]), ("", ""))

# robinhood gives nothing back; arbitrum answers
store.keywallet_chain_put([W1], {W1: None}, {W1: 3000})
r4 = store.keywallet_chain_get([W1])[W1]
case("4b the half that answered is replaced", r4["arb_wei"], "3000")
case("4c and restamped", r4["arb_at"] != 2222, True)
case("4d the half that did not is still there", r4["rh_wei"], "1000")
case("4e and keeps its own stamp", r4["rh_at"], 1111)
case("4f and says why it is not newer", bool(r4["rh_err"]), True)
case("4g while the half that answered has no refusal left", r4["arb_err"], "")

# the other direction, because a rule that only works one way is a coincidence
store.keywallet_chain_put([W1], {W1: 4000}, {})
r4 = store.keywallet_chain_get([W1])[W1]
case("4h the other half is the one that moves now", r4["rh_wei"], "4000")
case("4i arbitrum keeps its number and its stamp",
     (r4["arb_wei"], r4["arb_at"] == 2222), ("3000", False))
case("4j and carries the refusal instead", bool(r4["arb_err"]), True)
case("4k while robinhood's refusal is cleared", r4["rh_err"], "")

# A whole half that threw carries one sentence for every address it covered,
# rather than each row inventing its own reason.
store.keywallet_chain_put([W1], {}, {}, rh_error="the node is down")
r4 = store.keywallet_chain_get([W1])[W1]
case("4l a half that threw says so on the row", r4["rh_err"], "the node is down")
case("4m and writes no number over the one it has", r4["rh_wei"], "4000")

# ---------------- 8. forgetting a wallet forgets what was known about it
store.keywallet_chain_put([W2], {W2: 77}, {W2: 88})
case("8 the second wallet has a reading", bool(store.keywallet_chain_get([W2])), True)
case("8a forgetting the key drops the reading",
     (store.keywallet_remove(W2), store.keywallet_chain_get([W2])), (True, {}))
case("8b and it takes nothing else with it",
     store.keywallet_chain_get([W1])[W1]["rh_wei"], "4000")

# ---------------- 5. the money path still reads the chain, not the cache
# The cache exists to make the Wallets tab cheap, and the way it would do real
# damage is by spreading into the endpoints that move funds: a sweep or a plan
# built on a minute-old balance is a transaction built on a guess. So this pins
# the other direction - with a fresh reading already cached for W1, the bridge's
# balance endpoint still goes to the node and still reports what the node said.
stale = {"rh": 0, "arb": 0}


def _bal_stub(chain_id, addr):
    stale["rh" if chain_id == C.CHAIN_ID else "arb"] += 1
    return 900 if chain_id == C.CHAIN_ID else 901


_old5 = srv.bridge.balance_of
try:
    srv.bridge.balance_of = _bal_stub
    b5 = srv.api_bridge_balances(W1)
finally:
    srv.bridge.balance_of = _old5
case("5 a fresh cache does not stop the bridge reading the node",
     (stale["rh"], stale["arb"]), (1, 1))
case("5a and the answer is the node's, not the cached number",
     (b5["rh_wei"], b5["arb_wei"]), (900, 901))

# ---------------- 6. asking for a reading does not need the vault
# Nothing in this route opens a key, and that is the whole reason the table can
# price itself with the vault shut - the same reason the mask is stored rather
# than computed. The route is called with the vault locked and must answer, not
# raise 423. It is called last of the reads because it locks the vault.
srv.keysafe.lock()
_old6 = (srv.chain.balances, srv.bridge.arb_balances)
try:
    srv.chain.balances, srv.bridge.arb_balances = _rh_count, _arb_count
    six, err6 = None, ""
    try:
        six = srv.api_keywallet_chain()
    except Exception as e:
        err6 = "%s: %s" % (type(e).__name__, str(e)[:80])
    note("6 the read finished", _settle("locked"))
finally:
    srv.chain.balances, srv.bridge.arb_balances = _old6
case("6 a locked vault does not refuse the reading", err6, "")
case("6a and the answer still carries the rows",
     len((srv._keywallet_list() or {}).get("wallets") or []), 1)
case("6b while the answer says the vault is shut",
     srv._keywallet_list()["locked"], True)

# ------------------------------- 7. the schema is where it is supposed to be
INFO = "SELECT * FROM pragma_table_info('key_wallet_chain')"
cols7 = {r[1]: r for r in c.execute(INFO)}
case("7 the per-wallet reading table exists", bool(cols7), True)
case("7a its key is the address, so one row holds one wallet",
     [r[1] for r in c.execute(INFO + " WHERE pk > 0 ORDER BY pk")], ["address"])
case("7b the two values are TEXT, because 9.2 ETH is the int64 ceiling",
     ((cols7.get("rh_wei") or (0, 0, None))[2],
      (cols7.get("arb_wei") or (0, 0, None))[2]), ("TEXT", "TEXT"))
# Each column fails on its own, so each one carries its own clock and its own
# refusal. A single fetched_at would have to be one of two lies: a row whose
# robinhood read failed a second ago would claim its three-day-old Arbitrum
# number was read a second ago, and a read that failed would leave the row
# looking like one nobody has ever asked about.
case("7c each half carries its own time and its own refusal",
     all(k in cols7 for k in ("rh_at", "rh_err", "arb_at", "arb_err")), True)
case("7d and one clock for when to ask again",
     "tried_at" in cols7, True)

# ================================================== the launch route (part two)
#
# `POST /api/launch/send` is the second place in the process where a stored key
# becomes a signature, and the first that anyone can reach from a page: a sweep
# needs a plan and a destination the user typed, while this one signs the raw
# bytes it is handed. That is the whole reason its guards are pinned here as
# behaviour rather than described in a comment - the endpoint is one missing
# check away from being "sign anything with my key", reachable from every page
# on the machine.
#
# The cases are numbered S1 upward and not 1 upward, because part one already
# spends 1 to 8 in this file.
#
# No case here sends anything: `bridge.sign_tx` is replaced, and the chain
# reads the route makes are replaced with a fake client, so the file's promise
# - nothing in it goes to the network - still holds. What is signed is checked,
# never performed.


def _post(body: dict):
    """Call the route the way FastAPI does, and read its refusal as a status.

    The tests in this project do not use TestClient - a test that starts the
    app also starts its lifespan, and with it the indexer - so the route
    function is called directly and `HTTPException` is read for its status
    code. A route that is not there yet raises `AttributeError`, which is the
    same "there is no route at this address" as a 404 and is reported as one,
    rather than taking the whole file down with it.
    """
    try:
        return srv.api_launch_send(body), None
    except HTTPException as e:
        return None, e.status_code
    except AttributeError as e:
        return None, "no route (%s)" % str(e)[:60]


def _swap(obj, name: str, new):
    """Put a stand-in in place and hand back the call that removes it again.

    Whether the attribute was there at all is remembered, because this file was
    written before half of what it stubs existed: run against the code that has
    no `sign_tx` and no route, a test that assumed both were present would die
    on its own setup line and report nothing about the code under test.
    """
    had = hasattr(obj, name)
    old = getattr(obj, name, None)
    setattr(obj, name, new)

    def undo():
        if had:
            setattr(obj, name, old)
        else:
            delattr(obj, name)
    return undo


_signed: list = []


def _signer(secret, tx):
    """Stand in for the one function that turns a key into a broadcast."""
    _signed.append((secret, tx))
    return "0x" + "ab" * 32


# ---------------- S1. a shut vault does not sign, whatever the request says
# The first guard that can refuse, and the one whose answer the page already
# knows how to draw (`kvNeeds` turns 423 into "unlock it and press again").
srv.keysafe.lock()
_signed.clear()
undo_sign = _swap(srv.bridge, "sign_tx", _signer)
try:
    d1, code1 = _post({"from": W1, "to": C.FACTORY,
                       "data": srv.chain.LAUNCH_SEL + "00", "value": "0"})
finally:
    undo_sign()
case("S1 a locked vault refuses the send", code1, 423)
case("S1a and nothing was signed at all", _signed, [])

# ---------------- S2. `to` is a whitelist, and that is the point of the route
# Without this check the endpoint is a signer for arbitrary calldata, and a
# stored key is a wallet. The assertion that matters is not the 400 - it is
# that the key was never even opened to answer it.
srv.keysafe.unlock("keywallets-test-phrase", [])
_opened: list = []
_signed.clear()
undo_secret = _swap(srv.store, "keywallet_secret",
                    lambda a: (_opened.append(a), "0x" + "11" * 32)[1])
undo_sign = _swap(srv.bridge, "sign_tx", _signer)
try:
    d2, code2 = _post({"from": W1, "to": W2,
                       "data": srv.chain.LAUNCH_SEL + "00", "value": "0"})
    case("S2 a destination outside the whitelist is refused", code2, 400)
    case("S2a and the key was never opened to say so", _opened, [])
    case("S2b and nothing was signed", _signed, [])

    # ---------------- S3. the destination and the selector are checked as a pair
    # A router with the factory's selector is not a near miss, it is the exact
    # shape of a mistake that two independent lists would let through: the
    # address is allowed and the selector is allowed, and the pair of them
    # calls something nobody meant to call.
    d3, code3 = _post({"from": W1, "to": C.ROUTER,
                       "data": srv.chain.LAUNCH_SEL + "00", "value": "0"})
    case("S3 the router refuses the factory's selector", code3, 400)
    d4, code4 = _post({"from": W1, "to": C.FACTORY,
                       "data": srv.chain.LAUNCH_AND_BUY_SEL + "00", "value": "0"})
    case("S3a and the factory refuses launchAndBuy", code4, 400)
finally:
    undo_secret()
    undo_sign()

# ---------------- the fixture for the cases that get as far as signing
#
# Only the node is faked here, not the route's own helper. `_send_chain` is
# where the estimate turns into a gas limit and the fee into a cap, and a
# fixture that replaced it would be testing its own arithmetic instead of the
# code's - so the fake is a client with four methods, and the real block read,
# the real headroom, the real clamp and the real nonce rule all run.
SALT = "ab" * 32
DATA = srv.chain.LAUNCH_SEL + "0" * 24 + SALT + "%064x" % 7
FEE = 10 ** 15
EST = 300_000
_st = {"base": 100, "priority": 3, "limit": 30_000_000,
       "estimate": EST, "pending": 7}


class _FakeEth:
    def __init__(self, s):
        self._s = s

    def get_block(self, tag):
        return {"baseFeePerGas": self._s["base"], "gasLimit": self._s["limit"]}

    @property
    def max_priority_fee(self):
        return self._s["priority"]

    def estimate_gas(self, tx):
        if self._s.get("refuse"):
            raise RuntimeError("execution reverted: insufficient funds")
        return self._s["estimate"]

    def get_transaction_count(self, addr, tag):
        return self._s["pending"]


class _FakeW3:
    """Enough of a client for this route, with the real checksumming.

    `to_checksum_address` is the real function and not a stub: `_addr` is shared
    with the sweep and is not what any of this is about, so faking it would be
    testing the fixture.
    """

    def __init__(self, s, real):
        self.eth = _FakeEth(s)
        self._real = real

    def to_checksum_address(self, a):
        return self._real.to_checksum_address(a)


_real_w3 = srv.chain.w3
undo_rpc = _swap(srv.chain, "_rpc", lambda fn, *a, **k: fn(*a, **k))
undo_w3 = _swap(srv.chain, "w3", _FakeW3(_st, _real_w3))
undo_fee = _swap(srv.chain, "launch_fee", lambda: FEE)
undo_sign = _swap(srv.bridge, "sign_tx", _signer)
try:
    # ---------------- S4. the bytes signed are the bytes that arrived
    # The salt is random per build and the token address is a function of it, so
    # "let the server assemble the transaction" is not an optimisation here: it
    # is a different deployment at a different address than the confirmation
    # panel printed. This case is that rule as an assertion - whatever arrives
    # in `data` is what goes into the signature, byte for byte, with nothing
    # rebuilt, re-encoded or tidied on the way through.
    _signed.clear()
    d4, code4 = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                       "value": str(FEE)})
    case("S4 the launch goes through", code4, None)
    case("S4a one transaction was signed", len(_signed), 1)
    got4 = _signed[0][1] if _signed else {}
    case("S4b and it carries the exact bytes the page sent",
         got4.get("data"), DATA)
    case("S4c to the address the page named",
         got4.get("to"), _real_w3.to_checksum_address(C.FACTORY))
    case("S4d for the value the page named", got4.get("value"), FEE)
    # The gas is the estimate plus the configured headroom, capped by the
    # block's own limit - the three numbers the node gave, run through the
    # arithmetic that exists for the block the transaction lands in.
    case("S4e gas is the estimate plus its margin",
         got4.get("gas"), EST * C.GAS_HEADROOM_BPS // 10_000)
    case("S4f and the cap is relay's margin over base plus priority",
         got4.get("fee_cap"), srv.bridge.fee_cap(_st["base"] + _st["priority"]))
    case("S4g with the priority the node asked for",
         got4.get("max_priority_fee_per_gas"), _st["priority"])

    # A chain whose next block could not hold the estimate: the request still
    # goes out, because a transaction larger than a block is invalid and the
    # node says so - what must not happen is a gas limit above the block's.
    _st["estimate"] = 40_000_000
    _st["limit"] = 30_000_000
    d4b, code4b = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                         "value": str(FEE)})
    case("S4h an estimate past the block limit is clamped to it",
         _signed[-1][1].get("gas"), 30_000_000)
    # A node that will not estimate: refused, with a phrase, and never replaced
    # by a number - the number would be the cost of a transaction that reverts.
    _st["refuse"] = True
    d4c, code4c = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                         "value": str(FEE)})
    case("S4i a refused estimate is a 502 and not a guess", code4c, 502)
    _st["refuse"] = False
    _st["estimate"], _st["limit"] = EST, 30_000_000

    # ---------------- S5. the answer carries the hash and nothing else
    # Not a style point: the key is in this process's memory and in the blob, so
    # a response that echoed it would put it in the page, in the browser's
    # history of fetches, and in whatever the page logs. Same for the raw
    # signed transaction, which is the key's work made portable.
    body5 = json.dumps(d4)
    case("S5 the response does not contain the key",
         "not-a-real-keystore" in body5, False)
    case("S5a and has no field for a raw transaction",
         sorted(d4 or {}), ["from", "gas", "nonce", "to", "tx_hash", "value_wei"])
    case("S5b while it does carry the hash the send returned",
         (d4 or {}).get("tx_hash"), "0x" + "ab" * 32)

    # ---------------- S6. two presses in one block are two transactions
    # Both presses read the same pending nonce from the node - which is what a
    # second press inside one block does - and the second must not be signed
    # with the first one's number, because a replacement pays the fee twice for
    # one launch.
    srv._SEND_NONCE.clear()
    _st["pending"] = 7
    d6a, _ = _post({"from": W1, "to": C.FACTORY, "data": DATA, "value": "0"})
    d6b, _ = _post({"from": W1, "to": C.FACTORY, "data": DATA, "value": "0"})
    case("S6 the first press takes the node's nonce", (d6a or {}).get("nonce"), 7)
    case("S6a the second takes the next one", (d6b or {}).get("nonce"), 8)
    case("S6b and it is the number that was signed with",
         _signed[-1][1].get("nonce"), 8)
    # The other direction, because a rule that only ever adds is a rule that
    # skips a nonce the moment the node has moved on without us.
    _st["pending"] = 20
    d6c, _ = _post({"from": W1, "to": C.FACTORY, "data": DATA, "value": "0"})
    case("S6c a node that has moved past the memory is believed",
         (d6c or {}).get("nonce"), 20)

    # A send that failed spent nothing, so the number it was going to use is
    # still free - and remembering it would make every later press from this
    # wallet skip a nonce and wait for a transaction that does not exist.
    def _refuser(secret, tx):
        raise srv.bridge.RelayError("the fee moved past the reserved cap")

    undo_sign2 = _swap(srv.bridge, "sign_tx", _refuser)
    try:
        _st["pending"] = 5
        d6d, code6d = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                             "value": "0"})
    finally:
        undo_sign2()
    case("S6d a signer that refuses is a 502", code6d, 502)
    case("S6e and its nonce is not remembered",
         srv._SEND_NONCE.get(W1.lower()), None)
    _st["pending"] = 5
    d6e, _ = _post({"from": W1, "to": C.FACTORY, "data": DATA, "value": "0"})
    case("S6f so the number was never spent", (d6e or {}).get("nonce"), 5)

    # ---------------- S7. what a launch may cost is bounded
    # The same ceiling the form already refuses to build past, plus a second
    # launch fee for a fee that moved between the build and this request. Above
    # it is a 400 rather than a signature, and the number is parsed as a whole
    # number of wei - a value that arrived as a float has already lost the digit
    # this bound exists to protect.
    ceiling = 2 * FEE + int(C.MAX_INITIAL_BUY_QUOTE * 1e18)
    d7a, code7a = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                         "value": str(ceiling)})
    case("S7 the ceiling itself is allowed", code7a, None)
    d7b, code7b = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                         "value": str(ceiling + 1)})
    case("S7a a wei above it is not", code7b, 400)
    d7c, code7c = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                         "value": "12.5"})
    case("S7b and a value that is not a whole number is not either",
         code7c, 400)
    d7d, code7d = _post({"from": W1, "to": C.FACTORY, "data": DATA,
                         "value": "-1"})
    case("S7c nor is a negative one", code7d, 400)
    # The body is not hex: the selector can match and the rest still be junk,
    # which is the one thing a signature would make permanent.
    d7e, code7e = _post({"from": W1, "to": C.FACTORY,
                         "data": srv.chain.LAUNCH_SEL + "zz", "value": "0"})
    case("S7d calldata that is not hex is refused", code7e, 400)
    d7f, code7f = _post({"from": W1, "to": C.FACTORY,
                         "data": srv.chain.LAUNCH_SEL + "0", "value": "0"})
    case("S7e and so is an odd number of hex digits", code7f, 400)
finally:
    undo_sign()
    undo_fee()
    undo_w3()
    undo_rpc()

# ---------------- S8. the shared signer still refuses a key that is not its own
# The ownership check moved out of `sign_send` and into `sign_tx` when the two
# paths were merged, so this case is here to hold it in place: a leg whose user
# is not the key's address must be refused, and refused before a node is asked
# anything - which is why this case needs no fake client at all.
KEY8 = "0x" + "11" * 32
OWNER8 = Account.from_key(KEY8).address
leg8 = {"origin_chain_id": C.CHAIN_ID, "user": W1, "to": W2, "value": 0,
        "data": "0x", "gas": 21000, "max_fee_per_gas": 10 ** 9}
err8 = ""
try:
    srv.bridge.sign_send(KEY8, leg8)
except srv.bridge.RelayError as e:
    err8 = str(e)
except Exception as e:
    err8 = "%s: %s" % (type(e).__name__, str(e)[:60])
case("S8 a leg signed with someone else's key is refused",
     err8, "the stored key does not belong to this wallet")
case("S8a and the key does belong to an address, just not that one",
     OWNER8.lower() == W1.lower(), False)

print()
print("ALL CASES PASS:", bool(ok_all))
c.close()
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(_TMP + suffix)
    except OSError:
        pass
sys.exit(0 if ok_all else 1)
