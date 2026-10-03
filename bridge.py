"""Cross-chain transfers through relay.link, and signing for the stored keys.

Two directions, one shape. Both move a whole balance off one chain and back to
a different address on the other:

    out    the connected browser wallet pays, the money lands on a stored key
    back   a stored key pays, the money lands on the connected wallet

The route is two relay deposits rather than one direct send because a direct
send is the thing being avoided: a native transfer from the burner to the main
wallet writes the link between them into the chain permanently. Through relay
the receiving wallet is paid by the solver, and the two ends are only connected
off-chain.

The amount is always the whole balance minus the gas of the very transaction
being built, which is why the quote and the amount depend on each other: the
first quote is not for the calldata that gets sent. The fee is the part that
moves - relay quotes it at about the current base fee, and the base fee is a
different number a second later - so the fee this machine signs with is chosen
here (`fee_cap`) rather than taken from the last quote. `max_sendable` holds the
arithmetic and `plan` holds the loop, so both directions and both signers agree
on what "all of it" means.

Nothing here signs for the browser. The `out` direction returns a transaction
for the page to hand to the wallet; `sign_tx` is the one function that touches a
private key, and it is reached only for work a stored key pays for itself - the
`back` direction of a transfer, and a launch the user asked to send from a
stored key rather than from the wallet.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Sequence

import requests
from web3 import Web3

import chain
import config as C

log = logging.getLogger("bridge")

NATIVE = "0x0000000000000000000000000000000000000000"
# relay's own status vocabulary. Anything else is passed through as it arrives
# rather than mapped to a word this file invented.
DONE = "success"
FAILED = {"failure", "refund"}


class RelayError(RuntimeError):
    """relay refused the quote. `code` is its own errorCode, when it sent one."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ chains
_arb: Web3 | None = None


def w3_for(chain_id: int) -> Web3:
    """The node to send and read a given chain through.

    Robinhood goes through chain.py's client, so this shares the one rate gate
    the rest of the app is throttled by. Arbitrum is read only for balances and
    used only when a stored key signs, so it gets a plain client of its own.
    """
    global _arb
    if chain_id == C.CHAIN_ID:
        return chain.w3
    if chain_id == C.ARB_CHAIN_ID:
        if _arb is None:
            _arb = Web3(Web3.HTTPProvider(C.ARB_RPC_URL,
                                          request_kwargs={"timeout": 20}))
        return _arb
    raise ValueError(f"no client for chain {chain_id}")


def balance_of(chain_id: int, address: str) -> int | None:
    """Native balance in wei, or None if the node would not say."""
    if chain_id == C.CHAIN_ID:
        return chain.balance(address)
    addr = w3_for(chain_id).to_checksum_address(address)
    try:
        return int(w3_for(chain_id).eth.get_balance(addr))
    except Exception as e:
        log.debug("arb balance(%s) failed: %s", address, str(e)[:80])
        return None


def arb_balances(addresses: Sequence[str]) -> dict[str, int]:
    """Arbitrum balances for a list of addresses.

    One JSON-RPC batch rather than a call per wallet: the Wallets tab draws a
    row per stored key, and a page of them arriving as a page of requests is
    how a tab picks up a rate limit for a column of numbers.
    """
    addrs = [a for a in addresses if a]
    if not addrs:
        return {}
    try:
        w3 = w3_for(C.ARB_CHAIN_ID)
        payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_getBalance",
                    "params": [w3.to_checksum_address(a), "latest"]}
                   for i, a in enumerate(addrs)]
        r = requests.post(C.ARB_RPC_URL, json=payload,
                          timeout=C.RELAY_TIMEOUT)
        r.raise_for_status()
        out = {}
        for item in r.json():
            out[addrs[item["id"]]] = int(item["result"], 16)
        return out
    except Exception as e:
        log.debug("arb batch failed: %s", str(e)[:120])
        return {}


# ------------------------------------------------------------------ relay
def _relay(method: str, path: str, body: dict | None = None,
           params: dict | None = None) -> dict:
    url = C.RELAY_API.rstrip("/") + path
    try:
        r = requests.request(method, url, json=body, params=params,
                             timeout=C.RELAY_TIMEOUT)
    except Exception as e:
        raise RelayError(f"relay is unreachable: {type(e).__name__}") from e
    if r.status_code >= 400:
        # Its own refusals are the useful part of the message - AMOUNT_TOO_LOW
        # is a fact about the amount, not a server fault - so they are lifted
        # out here instead of becoming a bare 500 three frames up.
        code, text = None, ""
        try:
            d = r.json()
            code = d.get("errorCode") or d.get("message")
            text = d.get("message") or ""
        except Exception:
            text = r.text[:200]
        if r.status_code == 400 and code == "AMOUNT_TOO_LOW":
            raise RelayError("amount is too small for relay to bridge", code)
        raise RelayError(f"relay refused: {text or r.status_code}", code)
    return r.json()


def status(request_id: str) -> dict:
    return _relay("GET", "/intents/status/v3", params={"requestId": request_id})


def quote(origin: int, dest: int, user: str, recipient: str,
          amount: int) -> dict:
    return _relay("POST", "/quote/v2", body={
        "user": user,
        "recipient": recipient,
        "originChainId": origin,
        "destinationChainId": dest,
        "originCurrency": NATIVE,
        "destinationCurrency": NATIVE,
        "amount": str(int(amount)),
        "tradeType": "EXACT_INPUT",
    })


# ------------------------------------------------------------- the maximum
def max_sendable(balance: int, gas: int, max_fee: int) -> int:
    """What can be sent when the gas for the send has to come out of it.

    A transaction may spend `value + gasLimit * maxFeePerGas` and not a wei
    more, so that product is the reserve. `value` is what is left, and it is
    what leaves the wallet: the gas actually used comes back afterwards as an
    unused-gas refund, which is the only reason this is not zero in practice.

    Negative means the balance cannot cover the gas at all, and the caller
    should say so rather than send something that will be rejected.
    """
    return int(balance) - int(gas) * int(max_fee)


def fee_cap(max_fee: int) -> int:
    """The fee this machine signs with: relay's own, plus a margin.

    relay quotes maxFeePerGas at about the current base fee, and the base fee
    moves between quotes. The amount is solved from this number, so it has to be
    fixed before the transaction is built and it has to still be valid when the
    transaction is signed - which is what the margin buys. See
    RELAY_FEE_HEADROOM_BPS for why that costs almost nothing.
    """
    return int(max_fee) + int(max_fee) * C.RELAY_FEE_HEADROOM_BPS // 10_000


def _leg_from_quote(q: dict, origin: int, dest: int, user: str,
                    recipient: str, balance: int) -> dict:
    """Shape one relay quote into a leg, reading the transaction out of it."""
    steps = q.get("steps") or []
    tx = {}
    for step in steps:
        for item in (step.get("items") or []):
            data = item.get("data") or {}
            if data.get("to") and data.get("chainId") == origin:
                tx = data
                break
        if tx:
            break
    if not tx:
        raise RelayError("relay returned no transaction for the origin chain")

    amount = int(tx.get("value") or 0)
    gas = int(tx.get("gas") or 0)
    max_fee = int(tx.get("maxFeePerGas") or 0)
    priority = int(tx.get("maxPriorityFeePerGas") or 0)
    cap = fee_cap(max_fee)
    fees = q.get("fees") or {}
    details = q.get("details") or {}
    out = (details.get("currencyOut") or {}).get("amount") or 0
    out_min = ((details.get("route") or {}).get("destination", {})
               .get("outputCurrency", {}).get("minimumAmount") or 0)
    return {
        "origin_chain_id": origin,
        "dest_chain_id": dest,
        "user": user,
        "recipient": recipient,
        "amount": amount,
        "gas": gas,
        # relay's own fee cap, kept for the record. What gets signed is
        # `fee_cap`, which is this plus the margin.
        "max_fee_per_gas": max_fee,
        "fee_cap": cap,
        "max_priority_fee_per_gas": priority,
        "gas_cost": gas * cap,
        "balance_before": balance,
        # What is left once value and the full gas limit are gone. The gas that
        # is not used comes back, so the real remainder is this or less.
        "left_estimate": max(0, balance - amount - gas * cap),
        "relayer_fee": max(0, amount - int(out)),
        "out_estimate": int(out),
        "out_min": int(out_min),
        "seconds": (details.get("timeEstimate") or 0),
        "request_id": q.get("requestId") or "",
        "to": tx.get("to") or "",
        "data": tx.get("data") or "0x",
        "value": int(tx.get("value") or 0),
    }


def plan(origin: int, dest: int, user: str, recipient: str,
         amount: int | None = None, balance: int | None = None) -> dict:
    """One leg, quoted for what will actually be sent.

    With `amount` given it is one quote and done. Without it the whole balance
    is being moved, and the amount and the fee cap are solved together: the
    amount is what the balance leaves once gas is reserved at the cap, and the
    quote has to be redone for that amount, because relay prices the calldata it
    hands back for the value it was asked about - the number is carried in the
    transaction's value, not in the calldata, so a quote for one amount is not a
    quote for another.

    The cap only ever rises between rounds, which is what makes this terminate:
    the second round is normally the last. The loop stays bounded rather than
    trusted, and running out of rounds is said out loud instead of passed off as
    a converged answer.
    """
    if balance is None:
        balance = balance_of(origin, user)
    if balance is None:
        raise RelayError(f"the node would not report a balance on chain {origin}")

    if amount is not None:
        q = quote(origin, dest, user, recipient, amount)
        return _leg_from_quote(q, origin, dest, user, recipient, balance)

    if balance <= 0:
        raise RelayError("nothing to send: the balance is zero")

    cap = 0
    leg = None
    asked = balance
    settled = False
    for _ in range(C.RELAY_MAX_ROUNDS):
        q = quote(origin, dest, user, recipient, asked)
        leg = _leg_from_quote(q, origin, dest, user, recipient, balance)
        cap = max(cap, leg["fee_cap"])
        room = max_sendable(balance, leg["gas"], cap)
        if room <= 0:
            raise RelayError(
                "the balance does not cover the gas for this transfer "
                f"({leg['gas'] * cap} wei needed)")
        if leg["amount"] == room:
            settled = True
            break
        asked = room

    if not settled:
        # The fee is still climbing. Quote once more for exactly what the cap
        # leaves, so that the value the deposit carries is a value relay priced:
        # it reads the bridged amount off the deposit, and a value it was never
        # asked about is a bridge nobody quoted.
        room = max_sendable(balance, leg["gas"], cap)
        leg = _leg_from_quote(quote(origin, dest, user, recipient, room),
                              origin, dest, user, recipient, balance)
        cap = max(cap, leg["fee_cap"])
        log.warning("relay max-send did not settle in %d rounds, using %d",
                    C.RELAY_MAX_ROUNDS, leg["amount"])

    if leg["amount"] + leg["gas"] * cap > balance:
        # Over budget is not shaved down here: the value is what the quote was
        # for, and it is the only thing relay prices the bridged amount from.
        raise RelayError(
            "the fee is moving faster than this transfer can be quoted, "
            "try again in a moment")

    # Everything is restated against the cap that will be signed with, not
    # against the fee that came back in the last quote: the cap is the number
    # the amount was solved from, and the only one this machine controls.
    leg["fee_cap"] = cap
    leg["gas_cost"] = leg["gas"] * cap
    leg["left_estimate"] = max(0, balance - leg["amount"] - leg["gas_cost"])
    leg["settled"] = settled
    return leg


# --------------------------------------------------------------- signing
def sign_tx(secret: str, tx: dict) -> str:
    """Turn a stored key into a broadcast transaction and hand back the hash.

    The one place in this process where a key becomes a signature, and the one
    place a raw transaction is built. Two callers reach it and neither is more
    trusted than the other: the sweep signs a relay leg with it and a launch
    signs the calldata the page is holding. It does not matter which - it
    checks that the key belongs to the sender, refuses to sign past the fee cap
    this transaction was built around, and returns the hash and nothing else.
    The raw transaction never leaves this function.

    `tx` is read as given and not reinterpreted:
    {chain_id, from, to, value, data, gas, nonce, max_priority_fee_per_gas,
    fee_cap}. A launch and a leg do not share a shape - one has no quote behind
    it, the other has no calldata anyone was shown - and pretending otherwise
    is how a field ends up meaning two things. What they share is this.
    """
    w3 = w3_for(int(tx["chain_id"]))
    acct = w3.eth.account.from_key(secret)
    if acct.address.lower() != str(tx["from"]).lower():
        raise RelayError("the stored key does not belong to this wallet")

    priority = int(tx.get("max_priority_fee_per_gas") or 0)
    cap = int(tx.get("fee_cap") or tx["max_fee_per_gas"])
    latest = w3.eth.get_block("latest")
    base = int(latest.get("baseFeePerGas") or 0)
    if base + priority > cap:
        raise RelayError(
            f"the fee moved past the reserved cap ({base} + {priority} > {cap}), "
            "quote again")

    # The nonce is the caller's when it has one to give, and read here when it
    # does not - after the ownership check above, never before it, because a
    # leg whose key does not belong to its user has to be refused without a
    # single request to a node.
    nonce = tx.get("nonce")
    if nonce is None:
        nonce = w3.eth.get_transaction_count(acct.address, "pending")

    out = {
        "from": acct.address,
        "to": w3.to_checksum_address(tx["to"]),
        "value": int(tx["value"]),
        "data": tx["data"],
        "gas": int(tx["gas"]),
        "maxFeePerGas": cap,
        "maxPriorityFeePerGas": priority,
        "nonce": int(nonce),
        "chainId": int(tx["chain_id"]),
        "type": 2,
    }
    try:
        signed = acct.sign_transaction(out)
        # web3 renamed this attribute across 6 and 7 and both spellings are out
        # in the wild, so neither version is assumed here.
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        h = w3.eth.send_raw_transaction(raw)
    except Exception as e:
        raise RelayError(f"the send failed: {str(e)[:160]}") from e
    return h.hex() if not isinstance(h, str) else h


def sign_send(secret: str, leg: dict) -> str:
    """Sign a leg with a stored key and broadcast it. Returns the tx hash.

    Sent through this machine rather than a wallet, which is the whole
    difference between the two directions. The fee comes from the quote, so the
    amount the leg was built around is the amount that leaves - but the cap the
    amount was solved from is the one that has to be signed with, or the
    arithmetic no longer closes: with a higher fee the transaction would be
    rejected for spending past the balance, and with a lower one more of the
    balance stays behind than the plan promised. A base fee that has moved past
    the cap is said here rather than discovered as a failed send, and the answer
    is a fresh quote, not a bigger fee.

    All of that is the leg's half of the question. The signing itself is
    `sign_tx`, which is the only thing the two directions do not have to spell
    out twice.
    """
    return sign_tx(secret, {
        "chain_id": int(leg["origin_chain_id"]),
        "from": leg["user"],
        "to": leg["to"],
        "value": int(leg["value"]),
        "data": leg["data"],
        "gas": int(leg["gas"]),
        "max_priority_fee_per_gas": leg.get("max_priority_fee_per_gas") or 0,
        "fee_cap": leg.get("fee_cap") or leg["max_fee_per_gas"],
    })


def wait_receipt(chain_id: int, tx_hash: str, timeout: float = 90.0) -> dict:
    """Wait for a sent transaction to be mined. Raises if it reverted."""
    w3 = w3_for(chain_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            r = None
        if r is not None:
            if int(r.get("status") or 0) != 1:
                raise RelayError(f"the transaction reverted: {tx_hash}")
            return dict(r)
        time.sleep(2)
    raise RelayError(f"the transaction was not mined in {int(timeout)}s: {tx_hash}")


def wait_fill(request_id: str, timeout: float = 180.0,
              on_tick: Any = None) -> dict:
    """Wait for relay to report the intent filled.

    Polls its status endpoint rather than watching the destination balance: the
    solver pays the recipient, so the balance is the outcome, not the state,
    and a status that says `failure` or `refund` is the one thing a balance
    check would never tell us.
    """
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            last = status(request_id)
        except RelayError as e:
            last = {"status": "unknown", "error": str(e)}
        st = str(last.get("status") or "")
        if on_tick:
            on_tick(last)
        if st == DONE:
            return last
        if st in FAILED:
            raise RelayError(f"relay reported {st}"
                             + (f": {last.get('error')}" if last.get("error") else ""))
        time.sleep(3)
    return dict(last, timed_out=True)
