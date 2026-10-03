"""The arithmetic behind "send everything", with no network in it.

`plan` is the only place in this project that solves for an amount, and the
thing it solves is a transaction that spends the whole balance: value plus
gasLimit times maxFeePerGas may not exceed it, or the node rejects the send
before it ever reaches relay. The fee is the part that moves, so the cap the
amount is solved from has to be the cap the transaction is signed with - which
is the one thing the first version of this got wrong, and the reason this file
exists.

The quoting loop is exercised against a fake relay whose fee walks the way the
real one does, because that is the behaviour that broke it: the fee is not a
constant between two quotes a second apart.
"""
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import bridge
import config as C

allok = True


def case(name, ok, detail=""):
    global allok
    allok &= bool(ok)
    print("%s %-46s %s" % ("ok  " if ok else "FAIL", name, detail))


# ------------------------------------------------------------ max_sendable
# The reserve is the full gas limit at the cap, not the gas that gets used:
# the unused part comes back afterwards, and until it does the wallet has to
# be able to cover it.
case("reserves gas limit times cap",
     bridge.max_sendable(10**18, 21000, 100) == 10**18 - 21000 * 100,
     "10**18 - 21000*100")

case("exactly the gas cost leaves zero",
     bridge.max_sendable(21000 * 100, 21000, 100) == 0, "no room to send")

case("under the gas cost goes negative",
     bridge.max_sendable(21000 * 100 - 1, 21000, 100) == -1,
     "the caller refuses rather than sending")

# ----------------------------------------------------------------- fee cap
# relay's own cap plus the margin. The margin is what keeps the signed
# transaction valid when the base fee has risen since the quote, and it is the
# only reason the amount and the signature agree.
cap = bridge.fee_cap(100_000_000)
case("fee cap is relay's plus the margin",
     cap == 100_000_000 + 100_000_000 * C.RELAY_FEE_HEADROOM_BPS // 10_000,
     "%d -> %d (%d bps)" % (100_000_000, cap, C.RELAY_FEE_HEADROOM_BPS))
case("the cap can absorb a fee that moved",
     cap > 100_000_000, "strictly above relay's own quote")
case("zero stays zero", bridge.fee_cap(0) == 0, "no fee quoted, no margin")

# ------------------------------------------------------------- the quoting
# A fake relay that answers with the transaction shape the real one does, and
# with a fee that moves on every call - the thing the loop has to survive.
CALLS = []


class FakeRelay:
    """One leg's worth of relay, with a fee that moves the way the real one did.

    The real fee came back as 101819200, 102320400, 101122000, 101878000 across
    four calls a few seconds apart: it wobbles by under a percent and it does not
    climb. The last fee in the list repeats, so a list of one is a still fee.
    """

    def __init__(self, fees, gas=32713):
        self.fees = list(fees) or [0]
        self.gas = gas
        self.n = 0

    def quote(self, origin, dest, user, recipient, amount):
        CALLS.append(amount)
        fee = self.fees[min(self.n, len(self.fees) - 1)]
        self.n += 1
        data = {"chainId": origin, "to": "0x4cd00e387622c35bddb9b4c962c136462338bc31",
                "data": "0x49290c1c", "value": str(amount), "gas": self.gas,
                "maxFeePerGas": fee, "maxPriorityFeePerGas": 0}
        return {"requestId": "0xdead", "steps": [{"items": [{"data": data}]}],
                "fees": {}, "details": {"currencyOut": {"amount": str(amount * 99 // 100)},
                                        "timeEstimate": 30}}


def run_plan(fake, balance):
    real_quote, real_balance = bridge.quote, bridge.balance_of
    bridge.quote = fake.quote
    bridge.balance_of = lambda chain_id, address: balance
    try:
        return bridge.plan(4663, 42161, "0x1111111111111111111111111111111111111111",
                           "0x2222222222222222222222222222222222222222")
    finally:
        bridge.quote, bridge.balance_of = real_quote, real_balance


BAL = 10_000_000_000_000_000        # 0.01 ETH
leg = run_plan(FakeRelay([100_000_000]), BAL)
case("a still fee settles",
     leg["settled"] and leg["amount"] + leg["gas_cost"] == leg["balance_before"],
     "amount %d, gas %d, left %d" % (leg["amount"], leg["gas_cost"], leg["left_estimate"]))

# The fees the real endpoint actually returned, in order. The amount has to come
# out solved against the highest cap seen rather than the fee of the quote that
# carried it, or the transaction it produces is one the node refuses for
# spending past the balance.
WOBBLE = [101_819_200, 102_320_400, 101_122_000, 101_878_000]
CALLS.clear()
leg = run_plan(FakeRelay(WOBBLE), BAL)
case("a wobbling fee still settles exactly",
     leg["settled"] and leg["amount"] + leg["gas_cost"] == leg["balance_before"],
     "%d quotes, amount %d, left %d" % (len(CALLS), leg["amount"], leg["left_estimate"]))
case("the amount never exceeds the balance",
     leg["amount"] + leg["gas_cost"] <= BAL, "value + gas <= balance")
case("the signed cap is the one solved from",
     leg["fee_cap"] * leg["gas"] == leg["gas_cost"], "cap * gas == gas_cost")
case("the cap is above every fee relay quoted",
     leg["fee_cap"] >= max(WOBBLE) * (10000 + C.RELAY_FEE_HEADROOM_BPS) // 10000,
     "cap %d over %d quotes" % (leg["fee_cap"], len(CALLS)))
case("it settles well inside the round limit",
     len(CALLS) <= C.RELAY_MAX_ROUNDS, "%d quotes" % len(CALLS))

# A fee that climbs without stopping is not a thing to send into: every quote
# leaves less room than the last, so there is no amount that is still safe by
# the time it is signed. Refusing is the answer, and it is said out loud.
try:
    run_plan(FakeRelay([100_000_000 + i * 1_000_000 for i in range(20)]), BAL)
    case("a fee climbing out of reach is refused", False, "it built a transaction")
except bridge.RelayError as e:
    case("a fee climbing out of reach is refused",
         "moving faster" in str(e), str(e)[:64])

# A balance that cannot cover the gas is refused, not sent.
try:
    run_plan(FakeRelay([100_000_000]), 1000)
    case("a balance under the gas is refused", False, "it was quoted")
except bridge.RelayError as e:
    case("a balance under the gas is refused", True, str(e)[:60])

# An explicit amount is one quote and no solving: nothing to converge on when
# the caller has already decided what to send.
CALLS.clear()
real_quote, real_balance = bridge.quote, bridge.balance_of
fake = FakeRelay([100_000_000])
bridge.quote, bridge.balance_of = fake.quote, lambda c, a: BAL
try:
    leg = bridge.plan(4663, 42161, "0x1111111111111111111111111111111111111111",
                      "0x2222222222222222222222222222222222222222", amount=BAL // 2)
finally:
    bridge.quote, bridge.balance_of = real_quote, real_balance
case("an explicit amount is quoted once",
     len(CALLS) == 1 and leg["amount"] == BAL // 2, "%d call" % len(CALLS))
case("an explicit amount is not max-send",
     leg["left_estimate"] == BAL - BAL // 2 - leg["gas_cost"],
     "the remainder is real and stated")

print("\nALL UNIT CASES PASS:", bool(allok))
sys.exit(0 if allok else 1)
