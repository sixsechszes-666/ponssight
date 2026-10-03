"""Chain access: logs, multicall, and decoding of Pons launch/curve state."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Iterable, Sequence

from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError

import config as C

log = logging.getLogger("chain")


def is_rate_limit(exc: Any) -> bool:
    s = str(exc)
    return "429" in s or "Too Many Requests" in s


class RpcGate:
    """Spaces out RPC requests; a 429 parks every caller, not just one loop.

    The public node limits per IP, so per-loop backoff does not work: the
    loops simply trip over each other. One shared gate fixes that.
    """

    def __init__(self, spacing: float) -> None:
        self._lock = threading.Lock()
        self._spacing = spacing
        self._next_at = 0.0
        self._until = 0.0
        self._penalty = 0.0
        self._last_hit = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                start = max(self._next_at, self._until)
                if start <= now:
                    self._next_at = now + self._spacing
                    return
                delay = start - now
            time.sleep(min(delay, 2.0))

    def ok(self) -> None:
        with self._lock:
            if self._penalty and time.monotonic() - self._last_hit > C.RPC_PENALTY_DECAY:
                self._penalty = 0.0

    def hit(self) -> float:
        """Register a 429. Concurrent callers must not each escalate it.

        A burst fails as a group, so without the window check twenty threads
        all failing at once would take the penalty straight to the ceiling.
        """
        with self._lock:
            now = time.monotonic()
            escalated = now - self._last_hit > C.RPC_PENALTY_WINDOW
            if escalated:
                self._penalty = min(self._penalty * 2 or C.RPC_PENALTY,
                                    C.RPC_PENALTY_MAX)
            self._until = now + self._penalty
            self._last_hit = now
            p = self._penalty
        if escalated:
            log.warning("RPC rate limited - pausing all requests for %.0fs", p)
        return p


gate = RpcGate(C.RPC_MIN_SPACING)
_slots = threading.BoundedSemaphore(C.RPC_MAX_CONCURRENCY)


def _rpc(fn, *args, **kwargs):
    """Run one RPC call through the gate and the concurrency cap."""
    gate.wait()
    with _slots:
        try:
            out = fn(*args, **kwargs)
        except Exception as e:
            if is_rate_limit(e):
                gate.hit()
            raise
    gate.ok()
    return out


# The public RPC is rate limited; callers use retry() where it matters.
w3 = Web3(Web3.HTTPProvider(C.RPC_URL, request_kwargs={"timeout": 45}))

TOPIC0 = "0x" + keccak(text=C.TOKEN_LAUNCHED_SIG).hex()
BUY_TOPIC = "0x" + keccak(text=C.CURVE_BUY_SIG).hex()
SELL_TOPIC = "0x" + keccak(text=C.CURVE_SELL_SIG).hex()
SWEPT_TOPIC = "0x" + keccak(text=C.CURVE_FEES_SWEPT_SIG).hex()

_MC3_ABI = [{
    "name": "aggregate3", "type": "function", "stateMutability": "payable",
    "inputs": [{"name": "calls", "type": "tuple[]", "components": [
        {"name": "target", "type": "address"},
        {"name": "allowFailure", "type": "bool"},
        {"name": "callData", "type": "bytes"}]}],
    "outputs": [{"name": "returnData", "type": "tuple[]", "components": [
        {"name": "success", "type": "bool"},
        {"name": "returnData", "type": "bytes"}]}],
}]

mc3 = w3.eth.contract(address=w3.to_checksum_address(C.MULTICALL3), abi=_MC3_ABI)


def sels(names: Iterable[str]) -> dict[str, str]:
    """Map 'foo(uint256)' -> 4-byte selector hex."""
    return {n: "0x" + keccak(text=n)[:4].hex() for n in names}


TOKEN_FNS = ["name()", "symbol()", "description()", "logo()", "socials()",
             "totalSupply()", "decimals()"]
CURVE_FNS = ["realQuoteReserve()", "graduationThreshold()", "phantomQuote()",
             "sellableTokens()", "reservedTokens()", "graduated()",
             "readyToGraduate()", "isNativeQuote()", "pairToken()",
             # The creator side of the curve: what is owed but not yet
             # swept, and the rate the launch set. Both come back in the
             # same multicall the rest of the curve state does.
             "creatorTaxBalance()", "creatorTaxBps()"]
ERC20_FNS = ["symbol()", "decimals()", "name()"]

SEL = sels(TOKEN_FNS + CURVE_FNS + ERC20_FNS)

# output types, used by the decoders
TYPES = {
    "name()": "string", "symbol()": "string", "description()": "string",
    "logo()": "string", "totalSupply()": "uint256", "decimals()": "uint8",
    "realQuoteReserve()": "uint256", "graduationThreshold()": "uint256",
    "phantomQuote()": "uint256", "sellableTokens()": "uint256",
    "reservedTokens()": "uint256", "graduated()": "bool",
    "readyToGraduate()": "bool", "isNativeQuote()": "bool",
    "pairToken()": "address",
    "creatorTaxBalance()": "uint256", "creatorTaxBps()": "uint256",
}


# ------------------------------------------------------------------ basics
def block_number() -> int:
    return _rpc(lambda: w3.eth.block_number)


def block_timestamps(numbers: Sequence[int],
                     workers: int = 16) -> dict[int, int]:
    """Timestamps for a set of blocks.

    One eth_getBlockByNumber per block is the only way to get a timestamp, so
    this is the hot path during backfill. The endpoint takes parallel calls
    fine, so a pool is worth it here.
    """
    from concurrent.futures import ThreadPoolExecutor

    uniq = sorted(set(numbers))
    if not uniq:
        return {}

    def one(n: int) -> tuple[int, int | None]:
        try:
            return n, _rpc(lambda: w3.eth.get_block(n)["timestamp"])
        except Exception:
            return n, None

    out: dict[int, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, ts in pool.map(one, uniq):
            if ts is not None:
                out[n] = ts
    return out


def get_logs_chunked(from_block: int, to_block: int,
                     address: str | None = C.FACTORY,
                     topics: Sequence[str] | None = None,
                     span: int | None = None) -> list[dict[str, Any]]:
    """eth_getLogs with adaptive span so provider limits never break us.

    Passing no address indexes every contract that emits the topic, which is
    how trades are collected: each trade log already names its own curve, so
    one query covers all tokens instead of one query per token.
    """
    addr = w3.to_checksum_address(address) if address else None
    if topics is None:
        topics = [TOPIC0]
    if span is None:
        span = C.GETLOGS_MAX_SPAN
    out: list[dict[str, Any]] = []
    cur = from_block
    while cur <= to_block:
        end = min(cur + span - 1, to_block)
        flt: dict[str, Any] = {"topics": list(topics),
                               "fromBlock": cur, "toBlock": end}
        if addr:
            flt["address"] = addr
        try:
            out.extend(_rpc(lambda: w3.eth.get_logs(flt)))
            cur = end + 1
        except Exception as e:
            if is_rate_limit(e):
                # Not a range problem - the gate has already parked us, so
                # retry the same span instead of shrinking it to nothing.
                continue
            if span <= 100:
                log.error("getLogs %s..%s failed at min span: %s", cur, end, e)
                cur = end + 1
                span = C.GETLOGS_MAX_SPAN
                continue
            span //= 2
            log.debug("getLogs span -> %s (%s)", span, str(e)[:80])
    return out


def _as_int(v: Any) -> int:
    """Robinhood nodes put blockTimestamp in logs as a hex string.

    web3 decodes every other numeric field, but this one arrives raw both
    over HTTP and over a subscription, and sometimes without the 0x.
    """
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


def normalize_log(lg: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw JSON log (websocket) to the shape web3 hands us.

    Over HTTP web3 decodes topics into HexBytes, so `topic[-20:]` means the
    last twenty *bytes*. The raw JSON from a subscription is plain hex text
    and publicnode does not even prefix it with 0x, so slicing it as bytes
    would quietly produce garbage. Convert by hand instead.
    """
    out = dict(lg)
    for k in ("blockNumber", "logIndex", "transactionIndex", "blockTimestamp"):
        v = out.get(k)
        if isinstance(v, str) and v.startswith("0x"):
            out[k] = int(v, 16)
    topics = out.get("topics")
    if topics is not None:
        out["topics"] = [
            HexBytes(t if str(t).startswith("0x") else "0x" + str(t))
            for t in topics
        ]
    data = out.get("data")
    if isinstance(data, str):
        # `data` needs the same conversion as the topics, and needs it for a
        # different reason: the codec refuses anything that is not bytes, so
        # leaving the text a subscription sends in place made every
        # subscribed trade undecodable. The failure was invisible because
        # `decode_trade` reports an unreadable log as None, which is
        # indistinguishable from a log that is not a trade at all.
        out["data"] = HexBytes(data if data.startswith("0x") else "0x" + data)
    return out


def _hexstr(v: Any) -> str | None:
    """Log fields arrive as HexBytes over HTTP and as bare hex over ws."""
    if v is None:
        return None
    s = v.hex() if isinstance(v, (bytes, bytearray)) else str(v)
    return s if s.startswith("0x") else "0x" + s


def parse_launch(lg: dict[str, Any]) -> dict[str, Any]:
    """TokenLaunched(token, curve, deployer, pairToken, configId, threshold)."""
    if not isinstance(lg.get("topics", [None])[0], (bytes, bytearray)):
        lg = normalize_log(lg)
    topics = lg["topics"]
    row = {
        "address": w3.to_checksum_address(topics[1][-20:]),
        "curve": w3.to_checksum_address(topics[2][-20:]),
        "deployer": w3.to_checksum_address(topics[3][-20:]),
        "launch_block": lg["blockNumber"],
        "log_index": lg["logIndex"],
    }
    # The launch transaction is what a same-transaction buy has to match to
    # prove a token was bundled rather than sniped by a fast bot.
    tx = _hexstr(lg.get("transactionHash"))
    if tx:
        row["launch_tx"] = tx
    # A subscription event carries the block timestamp with it - one less
    # eth_getBlockByNumber to spend the request budget on.
    if lg.get("blockTimestamp") is not None:
        row["launch_ts"] = _as_int(lg["blockTimestamp"])
    return row


def decode_trade(lg: dict[str, Any]) -> dict[str, Any] | None:
    """One CurveBuy / CurveSell log -> a normalised trade.

    Both events declare the same argument types but mean different things by
    them:

        CurveBuy(buyer, recipient, quoteIn, tokensOut, fee, tax)
        CurveSell(seller, recipient, tokensIn, quoteOut, fee, tax)

    So on a buy the first value is the quote paid and the second is tokens
    received, and on a sell it is the other way round. Reading them the same
    way turns a sell's token amount into an enormous fake quote figure, which
    is why the side decides the mapping rather than the position.

    The `buyer` is the address that called the curve, which for anything
    routed through a helper contract is the router, not the person. The
    `recipient` is who the tokens were actually sent to, and that is the
    wallet whose behaviour is being judged, so it is what `who` means here.
    Attribution matters twice over: a router address shared by every user of
    it would otherwise look like one impossibly prolific sniper.
    """
    topics = lg.get("topics")
    if topics is None or len(topics) < 3:
        return None
    if not isinstance(topics[0], (bytes, bytearray)):
        lg = normalize_log(lg)
        topics = lg["topics"]
    topic0 = _hexstr(topics[0])
    side = "buy" if topic0 == BUY_TOPIC else \
           "sell" if topic0 == SELL_TOPIC else None
    if side is None:
        return None
    try:
        first, second, _fee, _tax = w3.codec.decode(
            ["uint256", "uint256", "uint256", "uint256"], lg["data"])
    except Exception as exc:
        # topic0 says this is a curve trade, so a codec failure is a buy or a
        # sell we cannot read - not something that was never a trade. Saying
        # so at debug level is what makes the next one of these findable.
        log.debug("trade log not decodable: %s", str(exc)[:120])
        return None
    quote, tokens = (first, second) if side == "buy" else (second, first)
    ts = lg.get("blockTimestamp")
    actor = w3.to_checksum_address(topics[1][-20:])
    recipient = (w3.to_checksum_address(topics[2][-20:])
                 if len(topics) > 2 else actor)
    if int(recipient, 16) == 0:
        recipient = actor
    return {
        "curve": w3.to_checksum_address(lg["address"]),
        "side": side,
        "who": recipient,
        "actor": actor,
        "block": lg["blockNumber"],
        "log_index": lg["logIndex"],
        "quote": quote,
        "tokens": tokens,
        "tx": _hexstr(lg.get("transactionHash")),
        "ts": _as_int(ts) if ts is not None else None,
    }


def parse_fees_swept(lg: dict[str, Any]) -> dict[str, Any] | None:
    """One FeesSwept log -> what the curve paid the token's creator.

        FeesSwept(uint256 protocol, uint256 tokens, uint256 creator)

    Read off a live sweep rather than guessed at. The sweep pays the pair
    token out twice - once to the protocol treasury and once to the
    creator fee recipient - and the third value is the second of those
    transfers. That is money that has actually reached the creator, which
    is not what `creatorTaxBalance()` reports: that is the part no sweep
    has collected yet, and it falls back to near zero every time one runs.

    Nothing is indexed, so the emitting address is the only thing that
    says which curve this is. The token amount is skipped: it is the same
    split in the token the curve sells, and a figure in two different
    units cannot be added up or sorted.
    """
    topics = lg.get("topics")
    if not topics:
        return None
    if not isinstance(topics[0], (bytes, bytearray)):
        lg = normalize_log(lg)
        topics = lg["topics"]
    if _hexstr(topics[0]) != SWEPT_TOPIC:
        return None
    try:
        _protocol, _tokens, creator = w3.codec.decode(
            ["uint256", "uint256", "uint256"], lg["data"])
    except Exception:
        return None
    return {"curve": w3.to_checksum_address(lg["address"]),
            "creator": int(creator)}


# ------------------------------------------------------------------ multicall
class MulticallError(RuntimeError):
    pass


def aggregate3(calls: list[tuple[str, str]]) -> list[bytes | None]:
    """calls: [(target, calldata)] -> [raw result or None if reverted]."""
    if not calls:
        return []
    payload = [(w3.to_checksum_address(t), True, d) for t, d in calls]
    try:
        res = _rpc(lambda: mc3.functions.aggregate3(payload).call())
    except (ContractLogicError, ValueError) as e:
        raise MulticallError(str(e)) from e
    return [raw if ok else None for ok, raw in res]


def _col(fn: str) -> str:
    """'realQuoteReserve()' -> 'real_quote_reserve' (DB column name)."""
    import re
    name = fn[:-2] if fn.endswith("()") else fn
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _decode(fn: str, raw: bytes) -> Any:
    t = TYPES[fn]
    return w3.codec.decode([t], raw)[0]


def _decode_socials(raw: bytes) -> tuple[str, str, str, str, str]:
    return w3.codec.decode(
        ["string", "string", "string", "string", "string"], raw)


def enrich(tokens: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Read metadata + curve state for tokens.

    tokens: [{'address':..., 'curve':...}]
    returns {address: {field: value, ...}}
    """
    calls: list[tuple[str, str]] = []
    meta: list[tuple[str, str]] = []
    for t in tokens:
        for fn in TOKEN_FNS:
            calls.append((t["address"], SEL[fn]))
            meta.append((t["address"], fn))
        for fn in CURVE_FNS:
            calls.append((t["curve"], SEL[fn]))
            meta.append((t["address"], fn))

    out: dict[str, dict[str, Any]] = {t["address"]: {} for t in tokens}
    if not calls:
        return out

    # Split into chunks - one bad batch cannot poison everything - and run
    # them concurrently. Each chunk is one eth_call of ~600 sub-calls, and the
    # node spends a couple of seconds on it, so doing them one after another
    # is what made enrichment slow; the gate still paces the real requests.
    from concurrent.futures import ThreadPoolExecutor

    chunks = [(calls[i:i + C.MULTICALL_CHUNK], meta[i:i + C.MULTICALL_CHUNK])
              for i in range(0, len(calls), C.MULTICALL_CHUNK)]

    def run(chunk: tuple[list, list]) -> list:
        c, m = chunk
        try:
            return list(zip(m, aggregate3(c)))
        except MulticallError as e:
            log.warning("enrich batch failed (%s calls): %s", len(c), str(e)[:120])
            return []

    workers = max(1, min(C.RPC_MAX_CONCURRENCY, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        batches = list(pool.map(run, chunks))

    for pairs in batches:
        for (addr, fn), raw in pairs:
            if raw is None:
                continue
            try:
                if fn == "socials()":
                    tw, tg, dc, web, fc = _decode_socials(raw)
                    out[addr].update(twitter=tw, telegram=tg, discord=dc,
                                     website=web, farcaster=fc)
                else:
                    out[addr][_col(fn)] = _decode(fn, raw)
            except Exception as e:
                log.debug("decode %s for %s failed: %s", fn, addr, e)
    return out


def read_erc20(addresses: Sequence[str]) -> dict[str, dict[str, Any]]:
    """symbol / name / decimals for a set of ERC20s (quote assets)."""
    calls, meta = [], []
    for a in addresses:
        for fn in ERC20_FNS:
            calls.append((a, SEL[fn]))
            meta.append((a, fn))
    out: dict[str, dict[str, Any]] = {a: {} for a in addresses}
    try:
        results = aggregate3(calls)
    except MulticallError as e:
        log.warning("erc20 multicall failed: %s", str(e)[:120])
        return out
    for (a, fn), raw in zip(meta, results):
        if raw is None:
            continue
        try:
            out[a][_col(fn)] = _decode(fn, raw)
        except Exception:
            pass
    return out


def retry(fn, attempts: int = 4, base: float = 0.5):
    """Small retry helper for the indexer loops."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1:
                raise
            time.sleep(base * (2 ** i))


# ------------------------------------------------------------------ launching
# The factory's real launch entry point, confirmed against its bytecode
# (selector 0xf35abbcf is present in the deployed code) and against the
# getLaunchedToken struct it writes, which returns the same field order.
LAUNCH_SEL = "0x" + keccak(text=C.LAUNCH_TOKEN_SIG)[:4].hex()
LAUNCH_AND_BUY_SEL = "0x" + keccak(text=C.LAUNCH_AND_BUY_SIG)[:4].hex()

_LAUNCH_TYPES = [
    "(string,string,string,string,"
    "(string,string,string,string,string),address,uint16,bool,bytes32,bytes32)",
    "uint256",
    "address",
]
# Same struct as the factory call, then configId, pairToken, the buy, the floor
# the buy will accept, who receives the tokens, and an extra recipient list that
# every launch in the sample leaves empty.
_LAUNCH_AND_BUY_TYPES = _LAUNCH_TYPES + ["uint256", "uint256", "address",
                                        "address[]"]

# getLaunchConfig(uint256) -> (supply, curveFeeBps, phantomQuote,
#                              graduationThreshold, poolFee, tickSpacing, enabled)
_LAUNCH_CONFIG_TYPES = ["uint256", "uint256", "uint256", "uint256",
                        "uint24", "int24", "bool"]


def _raw_call(to: str, sig: str, argtypes: Sequence[str],
              args: Sequence[Any], outtypes: Sequence[Any]) -> Any:
    data = "0x" + keccak(text=sig)[:4].hex()
    for t, a in zip(argtypes, args):
        if t == "address":
            data += a.lower().replace("0x", "").rjust(64, "0")
        elif t == "bytes32":
            data += (a.hex() if isinstance(a, (bytes, bytearray))
                     else str(a).replace("0x", "")).rjust(64, "0")
        else:
            data += f"{int(a):064x}"
    raw = _rpc(lambda: w3.eth.call({"to": to, "data": data}))
    return w3.codec.decode(list(outtypes), raw)


def launch_enabled() -> bool:
    return bool(_raw_call(C.FACTORY, "launchEnabled()", [], [], ["bool"])[0])


def launch_fee() -> int:
    try:
        return int(_raw_call(C.FACTORY, "launchFee()", [], [], ["uint256"])[0])
    except Exception:
        return C.LAUNCH_FEE_WEI


def launch_config(config_id: int = C.LAUNCH_CONFIG_ID) -> dict[str, Any]:
    """The economics a launch is priced against, straight from the factory."""
    v = _raw_call(C.FACTORY, "getLaunchConfig(uint256)", ["uint256"],
                  [config_id], _LAUNCH_CONFIG_TYPES)
    keys = ("supply", "curve_fee_bps", "phantom_quote", "graduation_threshold",
            "pool_fee", "tick_spacing", "enabled")
    return dict(zip(keys, v))


def preview_economics(config_id: int = C.LAUNCH_CONFIG_ID,
                      pair_token: str = C.NATIVE_QUOTE) -> str:
    """The expectedEconomics hash launchToken insists on.

    It is a commitment to the curve's economics at launch time, so it has to
    be read from the factory rather than guessed: a stale or made-up value
    makes the launch revert after the user has already paid gas.
    """
    v = _raw_call(C.FACTORY, "previewLaunchEconomics(uint256,address)",
                  ["uint256", "address"], [config_id, pair_token], ["bytes32"])
    return "0x" + bytes(v[0]).hex()


def expected_tokens_out(config_id: int, quote_wei: int) -> int:
    """Tokens a native-pair buy of this size fills at, before slippage.

    Constant product against the launch config's own virtual reserves, with the
    curve fee taken off the input first. It is not a guess: for every
    native-pair launch in the last 80 that set a floor, the floor its launcher
    chose lands on a clean 0.95 or 0.98 of this number, which is the slippage
    they picked, so this is the curve the chain is actually running. The launches
    that do not fit are exactly the ones paired against a token instead of the
    native address, where the reserves are not the config's.
    """
    if quote_wei <= 0:
        return 0
    cfg = launch_config(config_id)
    x = int(cfg.get("phantom_quote") or 0)
    y = int(cfg.get("supply") or 0)
    if x <= 0 or y <= 0:
        return 0
    net = quote_wei * (10000 - int(cfg.get("curve_fee_bps") or 0)) // 10000
    if net <= 0:
        return 0
    return y - (x * y) // (x + net)


def launch_calldata(fields: dict[str, Any]) -> dict[str, Any]:
    """Build the unsigned launchToken transaction for a copy plan.

    Nothing here signs or sends: the wallet in the browser does that. This
    only assembles the call and its exact cost so the UI can show it first.
    """
    config_id = int(fields.get("launch_config_id") or C.LAUNCH_CONFIG_ID)
    pair_token = w3.to_checksum_address(
        fields.get("pair_token") or C.NATIVE_QUOTE)

    salt_raw = (fields.get("salt") or "").strip()
    if salt_raw:
        salt = "0x" + salt_raw.replace("0x", "").rjust(64, "0")[-64:]
    else:
        salt = "0x" + os.urandom(32).hex()

    recipient = (fields.get("creator_fee_recipient") or "").strip()
    if not recipient or recipient == C.NATIVE_QUOTE:
        recipient = C.NATIVE_QUOTE
    else:
        recipient = w3.to_checksum_address(recipient)

    expected = preview_economics(config_id, pair_token)
    fee = launch_fee()

    socials = (str(fields.get("twitter") or ""), str(fields.get("telegram") or ""),
               str(fields.get("discord") or ""), str(fields.get("website") or ""),
               str(fields.get("farcaster") or ""))
    params = (
        str(fields.get("name") or ""),
        str(fields.get("symbol") or ""),
        str(fields.get("logo") or ""),
        str(fields.get("description") or ""),
        socials,
        recipient,
        int(fields.get("creator_tax_bps") or 0),
        bool(fields.get("buyback_enabled")),
        expected,
        salt,
    )

    from eth_utils import to_checksum_address
    body = w3.codec.encode(_LAUNCH_TYPES,
                           [params, config_id, to_checksum_address(pair_token)])
    data = LAUNCH_SEL + body.hex()

    # The value is the flat launch fee plus whatever the launcher buys in the
    # same transaction. Only the native pair is funded this way; a token pair
    # would need an approval first, so it is refused rather than mis-signed.
    initial = float(fields.get("initial_buy_quote") or 0.0)
    if pair_token != w3.to_checksum_address(C.NATIVE_QUOTE):
        raise ValueError("only the native ETH pair can be launched for now")
    if initial < 0:
        raise ValueError("initial buy cannot be negative")
    if initial > C.MAX_INITIAL_BUY_QUOTE:
        raise ValueError("initial buy of %s exceeds the %s cap"
                         % (initial, C.MAX_INITIAL_BUY_QUOTE))
    buy_wei = int(round(initial * 1e18))

    # Which of the two entry points carries it. They are not interchangeable:
    # the factory takes exactly the fee as its value and reverts on a wei more,
    # so anything with a buy in it has to go through the router, and a router
    # call with no buy is a pointless extra hop.
    entry = (str(fields.get("entry") or "factory")).strip().lower()
    if entry not in ("factory", "router"):
        raise ValueError("unknown entry point %r" % entry)
    min_out = 0
    expected_tokens = 0
    buyer = ""
    if entry == "router":
        if buy_wei <= 0:
            raise ValueError("the router is for a launch with a buy; with a buy "
                             "of 0 there is nothing for it to do, use the factory")
        # Named, and named as an address. The field is empty whenever the form
        # was blanked with no wallet connected, and web3's own error for an
        # empty string is a normalize() stack trace that says nothing about
        # which field is at fault.
        raw_buyer = str(fields.get("buyer") or "").strip()
        if not raw_buyer:
            raise ValueError("a router launch has to name the address the buy "
                             "sends its tokens to")
        try:
            buyer = w3.to_checksum_address(raw_buyer)
        except Exception:
            raise ValueError("%r is not an address the buy can be sent to"
                             % raw_buyer)
        expected_tokens = expected_tokens_out(config_id, buy_wei)
        slip = int(fields.get("slippage_bps", C.DEFAULT_SLIPPAGE_BPS) or 0)
        if slip < 0 or slip > 9900:
            raise ValueError("slippage of %s bps is outside 0 to 9900" % slip)
        min_out = expected_tokens * (10000 - slip) // 10000
        data = LAUNCH_AND_BUY_SEL + w3.codec.encode(
            _LAUNCH_AND_BUY_TYPES,
            [params, config_id, w3.to_checksum_address(pair_token), buy_wei,
             min_out, buyer, []]).hex()
    elif buy_wei:
        raise ValueError("the factory call takes the launch fee as its whole "
                         "value, so it cannot carry a buy; send this one through "
                         "the router instead")
    value = fee + buy_wei
    to = w3.to_checksum_address(C.ROUTER if entry == "router" else C.FACTORY)

    return {
        "to": to,
        "data": data,
        "value": str(value),
        "value_wei": value,
        "launch_fee_wei": fee,
        "buy_wei": buy_wei,
        "entry": entry,
        "buyer": buyer,
        "expected_tokens": expected_tokens,
        "min_tokens_out": min_out,
        "slippage_bps": int(fields.get("slippage_bps", C.DEFAULT_SLIPPAGE_BPS) or 0),
        "config_id": config_id,
        "pair_token": w3.to_checksum_address(pair_token),
        "salt": salt,
        "expected_economics": expected,
        "creator_fee_recipient": recipient,
    }


def deployer_tokens(deployer: str, limit: int = 200) -> list[str]:
    """Tokens a wallet launched, read from the factory itself.

    The local window only reaches back a day, so this is how the wallet tab
    can still show a deployer's older work.
    """
    addr = w3.to_checksum_address(deployer)
    try:
        n = deployer_token_count(addr)
    except Exception:
        return []
    out: list[str] = []
    for i in range(min(n, limit)):
        try:
            out.append(_raw_call(C.FACTORY, "deployerTokens(address,uint256)",
                                 ["address", "uint256"], [addr, i],
                                 ["address"])[0])
        except Exception:
            break
    return out


def deployer_token_count(deployer: str) -> int:
    """How many tokens a wallet has ever launched, in total."""
    return int(_raw_call(C.FACTORY, "deployerTokenCount(address)",
                         ["address"], [w3.to_checksum_address(deployer)],
                         ["uint256"])[0])


def balance(address: str) -> int | None:
    """Native balance in wei, or None if the node would not say."""
    addr = w3.to_checksum_address(address)
    try:
        return int(_rpc(lambda: w3.eth.get_balance(addr)))
    except Exception as e:
        log.debug("balance(%s) failed: %s", address, str(e)[:80])
        return None


# ------------------------------------------------ account state, in bulk
# What a deployer's address *is* takes three readings, and the shape of each
# one is decided by whether it can be put in a multicall.
#
#   * the balance can - Multicall3 exposes `getEthBalance(address)`, so the
#     sub-calls still happen but they travel in one HTTP request. A thousand
#     deployers cost two round trips instead of a thousand.
#   * the nonce cannot - a transaction count is state, not the return value of
#     a contract, and no contract can be asked for somebody else's. One request
#     per address.
#   * the code cannot either, and it is not optional: a deployer can be a
#     contract. Multicall3 itself is the deployer of 243 tokens belonging to one
#     sniper, and it carries nonce 1 and a zero balance, which without a code
#     check reads exactly like a wallet that launched once and is now empty.
#     That is the whole reason this reading exists, so it is paid for.
ETH_BALANCE_SEL = "0x" + keccak(text="getEthBalance(address)")[:4].hex()

# The profile walk runs beside the indexer, so it gets a slot budget of its own:
# sharing `_slots` would let a thousand-address backfill hold every slot the
# indexer needs and stall the launch feed for as long as the walk lasts.
#
# What it deliberately does NOT get is a second RpcGate. The gate protects a
# shared IP, and two of them on one URL would pace half the process at one rate
# and half at another - so a 429 arriving through one would park those callers
# while the others sailed straight past the limit that had just rejected them,
# and gate.hit() would escalate a penalty that only some callers ever wait out.
_sw_slots = threading.BoundedSemaphore(C.SW_RPC_CONCURRENCY)


def _sw_rpc(fn, *args, **kwargs):
    """One RPC call for the profile walk: same gate, its own slot budget."""
    gate.wait()
    with _sw_slots:
        try:
            out = fn(*args, **kwargs)
        except Exception as e:
            if is_rate_limit(e):
                gate.hit()
            raise
    gate.ok()
    return out


def _abi_word(addr: str) -> str:
    """A 20-byte address as one ABI word: the low 20 bytes of 32."""
    return addr[-40:].lower().rjust(64, "0")


def transaction_counts(addresses: Sequence[str],
                       workers: int = 0) -> dict[str, int | None]:
    """{address: nonce} - how many transactions each account has ever sent.

    A nonce of 0 is a real answer: an account that has never sent anything. A
    `None` is the node declining to answer, and the two must not be drawn the
    same way - one is a wallet that has not moved, the other is a reading we do
    not have. Callers that need to tell a contract from a wallet ask
    `code_sizes` as well.

    `workers` only decides how many threads queue here; `_sw_slots` caps how
    many of them are ever on the wire, so a large number is a queue and not a
    burst.
    """
    out: dict[str, int | None] = {}
    addrs = [a for a in dict.fromkeys(addresses) if a]
    if not addrs:
        return out
    from concurrent.futures import ThreadPoolExecutor

    def one(a: str) -> tuple[str, int | None]:
        try:
            return a, int(_sw_rpc(lambda: w3.eth.get_transaction_count(
                w3.to_checksum_address(a))))
        except Exception as e:
            log.debug("nonce(%s) failed: %s", a, str(e)[:80])
            return a, None

    n = max(1, workers or C.SW_RPC_CONCURRENCY)
    with ThreadPoolExecutor(max_workers=n) as pool:
        for a, nonce in pool.map(one, addrs):
            out[a] = nonce
    return out


# ------------------------------------------------- chains we do not index
# A deployer's nonce on the chains its funding most likely came from. One gate
# and one slot budget per host, built on first use and kept: the limit being
# respected is the remote node's own and each node counts separately, so a
# shared gate would park Robinhood calls when Ethereum complained about a limit
# they do not share. See the note above `gate` for the case that IS wrong - two
# gates on one URL - which is the opposite mistake.
_EXT: dict[int, tuple[Any, RpcGate, threading.BoundedSemaphore]] = {}
_EXT_LOCK = threading.Lock()


def _ext(chain_id: int) -> tuple[Any, RpcGate, threading.BoundedSemaphore]:
    """(web3, gate, slots) for one external chain, built once."""
    with _EXT_LOCK:
        got = _EXT.get(chain_id)
        if got is None:
            url = next((u for cid, _, u in C.SW_EXT_CHAINS if cid == chain_id),
                       None)
            if not url:
                raise KeyError("no url configured for chain %s" % chain_id)
            got = (Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 25})),
                   RpcGate(C.SW_EXT_SPACING),
                   threading.BoundedSemaphore(C.SW_EXT_CONCURRENCY))
            _EXT[chain_id] = got
        return got


def transaction_counts_ext(chain_id: int,
                           addresses: Sequence[str],
                           workers: int = 0) -> dict[str, int | None]:
    """{address: nonce} on a chain that is not the one this indexer reads.

    A nonce of 0 is a real answer - an account that has never sent anything on
    this chain - and it is the answer that matters most here, because a deployer
    with no history anywhere is the signal the profile is looking for. `None` is
    the node declining to answer, and the two are drawn differently: one is a
    wallet that has not moved, the other is a reading we do not have.

    A refused call is returned as `None` rather than raised, so one unreachable
    chain does not lose the readings from the other: the caller writes what came
    back and the next walk retries.
    """
    out: dict[str, int | None] = {}
    addrs = [a for a in dict.fromkeys(addresses) if a]
    if not addrs:
        return out
    from concurrent.futures import ThreadPoolExecutor

    w3x, gate, slots = _ext(chain_id)

    def one(a: str) -> tuple[str, int | None]:
        def call():
            gate.wait()
            with slots:
                try:
                    v = w3x.eth.get_transaction_count(w3x.to_checksum_address(a))
                except Exception as e:
                    if is_rate_limit(e):
                        gate.hit()
                    raise
            gate.ok()
            return v
        try:
            return a, int(call())
        except Exception as e:
            log.debug("nonce(%s) on %s failed: %s", a, chain_id, str(e)[:80])
            return a, None

    n = max(1, workers or C.SW_EXT_CONCURRENCY)
    with ThreadPoolExecutor(max_workers=n) as pool:
        for a, nonce in pool.map(one, addrs):
            out[a] = nonce
    return out


def code_sizes(addresses: Sequence[str]) -> dict[str, int | None]:
    """{address: bytecode length} - 0 means a wallet, above it a contract.

    The length and not a flag, because it is what the node returned and there is
    nothing to interpret: the caller decides what a non-zero value means. `None`
    is a failed read, which is not the same as 0 - a wallet with no code and an
    address we could not check are different facts.
    """
    out: dict[str, int | None] = {}
    addrs = [a for a in dict.fromkeys(addresses) if a]
    if not addrs:
        return out
    from concurrent.futures import ThreadPoolExecutor

    def one(a: str) -> tuple[str, int | None]:
        try:
            return a, len(_sw_rpc(lambda: w3.eth.get_code(
                w3.to_checksum_address(a))))
        except Exception as e:
            log.debug("code(%s) failed: %s", a, str(e)[:80])
            return a, None

    with ThreadPoolExecutor(max_workers=C.SW_RPC_CONCURRENCY) as pool:
        for a, size in pool.map(one, addrs):
            out[a] = size
    return out


def balances(addresses: Sequence[str]) -> dict[str, int | None]:
    """{address: wei} for a set of accounts, in multicall batches.

    A sub-call that comes back reverted stays `None` rather than becoming a zero
    balance: "this account holds nothing" and "this read did not work" are
    different facts about a deployer and the page has a use for both.

    The batches run through the indexer's own concurrency cap and not the
    walk's, because each one is a single heavy eth_call - what needs bounding
    here is how many of those the node is chewing on at once, not how many
    addresses are in them.
    """
    out: dict[str, int | None] = {}
    addrs = [a for a in dict.fromkeys(addresses) if a]
    if not addrs:
        return out
    for a in addrs:
        out[a] = None
    from concurrent.futures import ThreadPoolExecutor

    chunks = [addrs[i:i + C.MULTICALL_CHUNK]
              for i in range(0, len(addrs), C.MULTICALL_CHUNK)]

    def run(chunk: list[str]) -> tuple[list[str], list[bytes | None]]:
        calls = [(C.MULTICALL3, ETH_BALANCE_SEL + _abi_word(a)) for a in chunk]
        try:
            return chunk, aggregate3(calls)
        except Exception as e:
            log.warning("balance batch failed (%d addresses): %s",
                        len(chunk), str(e)[:120])
            return chunk, []

    workers = max(1, min(C.RPC_MAX_CONCURRENCY, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for chunk, raws in pool.map(run, chunks):
            for a, raw in zip(chunk, raws):
                if raw is None:
                    continue
                try:
                    out[a] = int(w3.codec.decode(["uint256"], raw)[0])
                except Exception as e:
                    log.debug("balance decode for %s failed: %s", a, e)
    return out
