"""Background indexer: launches, metadata, curve state, USD rates."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

import chain
import config as C
import fees
import imgcache
import store
import trades

log = logging.getLogger("indexer")

_stop = threading.Event()


def stop() -> None:
    _stop.set()
    trades.stop()


# ------------------------------------------------------------------ launches
def _index_history(floor: int, ceiling: int) -> int:
    """Walk backwards over blocks older than anything already indexed.

    Backwards because an interrupted walk should resume rather than
    start again: the oldest launch in the table is the marker of how far
    this got, and every span it finishes moves that marker back. It does
    not touch `last_indexed_block`, which is about the tip - letting a
    pass over the past move the live cursor would make the tail loop
    replay thirty days of blocks it had already done.
    """
    n = 0
    hi = ceiling
    while hi > floor and not _stop.is_set():
        lo = max(floor, hi - C.HISTORY_CHUNK)
        logs = chain.get_logs_chunked(lo, hi)
        rows = [chain.parse_launch(lg) for lg in logs]
        n += store.upsert_launches(rows)
        log.info("history %s..%s (%s blocks back), +%s launches",
                 lo, hi, hi - floor, len(rows))
        hi = lo - 1
    return n


def _history_floor(tip: int) -> int:
    """How far back the history walk goes, which is deeper than the window.

    The window is a retention depth and `FACTORY_DEPLOY_BLOCK` is a fact about
    the launchpad, so the older of the two is where history begins. That is the
    deployment block in practice: the window floor moves forward with the tip
    while the deployment stays where it is. The `min` is for the other case, so
    that a window wider than the launchpad's whole life cannot make the walk
    start late and skip the blocks in between.
    """
    floor = max(tip - C.INDEX_WINDOW_BLOCKS, 0)
    if C.FACTORY_DEPLOY_BLOCK <= 0:
        return floor
    return min(floor, C.FACTORY_DEPLOY_BLOCK)


def backfill() -> None:
    """Index the declared history at startup, then hand over to the tail.

    The window is a depth rather than a retention, so raising it has to
    reach back for the difference. Without that, a bigger number changed
    nothing: the forward cursor already sat at the tip, `backfill` found
    nothing to do, and the extra history never arrived.

    The past is walked to `_history_floor`, which is the factory's deployment
    rather than the window: the index is meant to answer "has this handle ever
    launched" about the launchpad, and a launchpad that has been running for 43
    days is not answered by 30 days of it.
    """
    tip = chain.block_number()
    floor = max(tip - C.INDEX_WINDOW_BLOCKS, 0)
    deep = _history_floor(tip)
    have = store.first_launch_block()

    if have is None:
        # Nothing indexed at all: the whole of the pad's history is the job,
        # forward, and there is no reason for a new database to begin thirty
        # days before the tip.
        log.info("backfill %s..%s (%s blocks)", deep, tip, tip - deep)
        _index_range(deep, tip)
        return

    if have > deep + 1:
        log.info("history: reaching back %s blocks, %s..%s",
                 have - deep, deep, have - 1)
        _index_history(deep, have - 1)

    # The forward cursor keeps the window's floor, not the deeper one. It
    # resumes from `last_indexed_block`, which sits at the tip, so the two only
    # differ on a database whose cursor was lost - and re-reading the window
    # forward is the right repair for that, not re-reading forty-three days.
    last = store.kv_get("last_indexed_block")
    start = max(floor, int(last) + 1) if last else floor
    if start <= tip:
        log.info("backfill %s..%s (%s blocks)", start, tip, tip - start + 1)
        _index_range(start, tip)


def _index_range(from_block: int, to_block: int) -> int:
    logs = chain.get_logs_chunked(from_block, to_block)
    rows = [chain.parse_launch(lg) for lg in logs]
    n = store.upsert_launches(rows)
    store.kv_set("last_indexed_block", to_block)
    if n:
        log.info("+%s launches (blocks %s..%s)", n, from_block, to_block)
    return n


def tail_loop() -> None:
    while not _stop.is_set():
        try:
            last = store.kv_get("last_indexed_block")
            tip = chain.block_number()
            if last is None:
                store.kv_set("last_indexed_block", tip - C.INDEX_WINDOW_BLOCKS)
                last = tip - C.INDEX_WINDOW_BLOCKS
            last = int(last)
            if tip > last:
                # cap catch-up per tick so we never spin on a huge gap
                upto = min(tip, last + C.TAIL_CATCHUP_BLOCKS)
                _index_range(last + 1, upto)
        except Exception as e:
            msg = str(e)
            import traceback
            log.warning("tail_loop: %s | %s", msg[:160],
                        traceback.format_exc().replace("\n", " <- ")[-600:])
            store.discard()
            if _is_rate_limit(msg):
                _stop.wait(C.RATE_LIMIT_BACKOFF)
                continue
        _stop.wait(C.POLL_SECONDS)


# ------------------------------------------------------------------ live tail
# A websocket subscription delivers TokenLaunched the moment it is mined, so
# the dashboard shows a launch in about a second instead of on the next poll.
# tail_loop keeps running underneath as a safety net: it is one getLogs every
# few seconds and it guarantees no gap if the socket drops.
#
# It subscribes to launches only. Trades used to come over the same socket and
# be folded the moment they arrived, and that turned out to be two bugs in one
# shape: the shape of a subscription log does not decode (see `normalize_log`),
# so not one live trade was ever folded; and the blocks it would have folded
# are blocks the trade walk collects again a second later, where the volumes
# and buckets it writes are sums. Two writers for one block is double counting,
# and the only way to make the live one safe would be to let it own the range
# and move the walk's cursor, which turns a dropped notification into a
# permanently missing buy - the exact failure that cursor exists to prevent.
# The walk folds its own blocks, and the deferral buffer in `trades` handles
# the tokens whose launch row has not landed yet.
async def _ws_session() -> None:
    import asyncio
    import json

    import websockets

    async with websockets.connect(C.WSS_URL, open_timeout=20,
                                  ping_interval=15, ping_timeout=20) as ws:
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "eth_subscribe",
            "params": ["logs", {"address": C.FACTORY, "topics": [chain.TOPIC0]}],
        }))
        ack = json.loads(await asyncio.wait_for(ws.recv(), 20))
        if "result" not in ack:
            raise RuntimeError("subscribe rejected: %s" % str(ack)[:140])
        log.info("ws subscribed to launches")

        while not _stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), 1.0)
            except asyncio.TimeoutError:
                continue
            params = (json.loads(raw).get("params")) or {}
            if params.get("subscription") != ack["result"]:
                continue
            try:
                row = chain.parse_launch(params.get("result") or {})
            except Exception as e:
                log.debug("ws launch skipped: %s", str(e)[:100])
                continue
            if row and store.upsert_launches([row]):
                log.info("ws launch %s (block %s)",
                         row["address"][:10], row["launch_block"])


def ws_loop() -> None:
    if not C.WSS_ENABLED:
        log.info("websocket tail disabled")
        return
    import asyncio

    while not _stop.is_set():
        try:
            asyncio.run(_ws_session())
        except Exception as e:
            log.warning("ws_loop: %s", str(e)[:160])
            store.discard()
        if not _stop.is_set():
            log.info("ws reconnecting in %.0fs", C.WSS_RECONNECT)
            _stop.wait(C.WSS_RECONNECT)


# ------------------------------------------------------------------ timestamps
def _harvest_launches() -> int:
    """Date tokens, and pick up their launch transaction, from the logs.

    Every TokenLaunched log carries its own block timestamp and the hash of
    the transaction that emitted it, so a single pass over the window fills in
    both for thousands of tokens at once. Asking the node per block would cost
    one request per token.

    The transaction hash matters because a buy in the same transaction as the
    launch is the one unambiguous snipe signal there is, and rows written
    before the column existed have no other way to recover it.
    """
    pending = store.missing_timestamps_count()
    no_tx = store.missing_launch_tx_count()
    if pending < C.TS_SWEEP_MIN and no_tx < C.TS_SWEEP_MIN:
        return 0
    tip = chain.block_number()
    start = tip - C.INDEX_WINDOW_BLOCKS
    log.info("launch sweep over %s blocks (%s undated, %s without a tx)",
             tip - start, pending, no_tx)
    times: list[tuple[int, str]] = []
    txs: list[tuple[str, str]] = []
    for lg in chain.get_logs_chunked(start, tip):
        try:
            row = chain.parse_launch(lg)
        except Exception:
            continue
        if row.get("launch_ts"):
            times.append((row["launch_ts"], row["address"]))
        if row.get("launch_tx"):
            txs.append((row["launch_tx"], row["address"]))
    store.set_launch_ts_many(times)
    n = store.set_launch_tx_many(txs)
    log.info("launch sweep: %s timestamps, %s transaction hashes",
             len(times), n)
    return len(times)


def timestamp_loop() -> None:
    swept = False
    while not _stop.is_set():
        try:
            if not swept:
                swept = True
                _harvest_launches()
            todo = store.missing_timestamps(4000)
            if todo:
                blocks = [r["launch_block"] for r in todo]
                known = store.cached_block_ts(blocks)
                missing = [b for b in set(blocks) if b not in known]
                if missing:
                    fetched = chain.block_timestamps(missing)
                    store.save_block_ts(fetched)
                    known.update(fetched)
                pairs = [(known[r["launch_block"]], r["address"]) for r in todo
                         if known.get(r["launch_block"])]
                store.set_launch_ts_many(pairs)
                log.info("timestamps %s/%s tokens (%s new blocks)",
                         len(pairs), len(todo), len(missing))
        except Exception as e:
            log.warning("timestamp_loop: %s", str(e)[:160])
            store.discard()
        _stop.wait(2.0)


# ------------------------------------------------------------------ enrichment
def enrich_loop() -> None:
    while not _stop.is_set():
        try:
            todo = store.pending_metadata(C.BATCH)
            if todo:
                t0 = time.time()
                data = chain.enrich(todo)
                _write(todo, data)
                log.info("enriched %s tokens in %.2fs",
                         len(todo), time.time() - t0)
                _stop.wait(C.ENRICH_PAUSE)   # do not burst the public RPC
            else:
                _stop.wait(3.0)
                continue
        except Exception as e:
            msg = str(e)
            log.warning("enrich_loop: %s", msg[:160])
            store.discard()
            _stop.wait(C.RATE_LIMIT_BACKOFF if _is_rate_limit(msg) else 5.0)


def _write(todo: list[dict[str, Any]],
           data: dict[str, dict[str, Any]]) -> None:
    for t in todo:
        d = data.get(t["address"]) or {}
        if not d:
            continue
        pair = d.get("pair_token")
        quote = _quote_for(pair, d.get("is_native_quote"))
        store.apply_enrichment(t["address"], d, quote)


# ------------------------------------------------------------------ refresh
# Two speeds: the newest tokens move constantly and are refreshed every tick,
# everything else is walked in rotation so a wide window stays reasonably fresh
# without hammering the RPC.
_rotate = 0


def _is_rate_limit(msg: str) -> bool:
    return chain.is_rate_limit(msg)


def refresh_loop() -> None:
    global _rotate
    while not _stop.is_set():
        try:
            total = store.stats()["tokens"]
            if _rotate >= total:
                _rotate = 0

            head = store.refresh_candidates(C.REFRESH_HEAD)
            seen = {h["address"] for h in head}
            tail = [t for t in store.refresh_window(C.REFRESH_TAIL, _rotate)
                    if t["address"] not in seen]
            _rotate += C.REFRESH_TAIL

            todo = head + tail
            if todo:
                data = chain.enrich(todo)
                _write(todo, data)
                log.debug("refreshed %s tokens (rotate %s/%s)",
                          len(todo), min(_rotate, total), total)
        except Exception as e:
            msg = str(e)
            log.warning("refresh_loop: %s", msg[:160])
            store.discard()
            if _is_rate_limit(msg):
                _stop.wait(C.RATE_LIMIT_BACKOFF)
                continue
        _stop.wait(C.REFRESH_SECONDS)


# ------------------------------------------------------------------ quotes
_quote_cache: dict[str, dict[str, Any]] = {}


def _quote_for(pair: str | None, is_native: bool | None) -> dict[str, Any] | None:
    """Resolve a quote asset row (with USD rate) for a token."""
    if pair is None:
        return None
    if pair == C.NATIVE_QUOTE or is_native:
        eth = store.kv_get("eth_usd")
        return {"address": C.NATIVE_QUOTE, "symbol": "ETH", "decimals": 18,
                "usd_price": float(eth) if eth else None}
    key = pair.lower()
    q = _quote_cache.get(key)
    if q is None:
        known = {r["address"].lower(): r for r in store.quotes()}
        q = known.get(key)
        if q is None:
            info = chain.read_erc20([pair]).get(pair, {})
            q = {
                "address": pair,
                "symbol": info.get("symbol") or "?",
                "decimals": info.get("decimals") if info.get("decimals") is not None else 18,
                "usd_price": None,
            }
            store.set_quote(q["address"], q["symbol"], info.get("name") or "",
                            q["decimals"], _kind(q["symbol"]), None)
        _quote_cache[key] = q
    return q


def _kind(symbol: str) -> str:
    """Classify a quote asset.

    On this chain almost every non-stable quote is a tokenised equity (there
    are dozens of them: NVDA, RDDT, GME, ...), so anything that is not a
    stablecoin is priced from the public quote API. An unknown ticker simply
    yields no USD price, which is exactly what happened before, so guessing
    costs nothing and covers the tickers we have not listed.
    """
    s = (symbol or "").upper()
    if s in C.STABLE_SYMBOLS:
        return "usd"
    if s in C.CRYPTO_SYMBOL_MAP:
        return "crypto"
    return "stock"


# ------------------------------------------------------------------ pricing
def _coinbase_spot(pair: str) -> float | None:
    try:
        r = requests.get(C.COINBASE_SPOT.format(pair=pair), timeout=10)
        r.raise_for_status()
        return float(r.json()["data"]["amount"])
    except Exception as e:
        log.debug("coinbase %s failed: %s", pair, str(e)[:80])
        return None


def _yahoo_price(symbol: str) -> float | None:
    try:
        r = requests.get(C.YAHOO_QUOTE.format(sym=symbol),
                         timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        return float(res["meta"]["regularMarketPrice"])
    except Exception as e:
        log.debug("yahoo %s failed: %s", symbol, str(e)[:80])
        return None


def price_loop() -> None:
    while not _stop.is_set():
        try:
            # ETH/USD
            eth = _coinbase_spot("ETH-USD")
            if eth:
                store.kv_set("eth_usd", eth)
                store.apply_quote_rate(C.NATIVE_QUOTE, eth)
                log.info("ETH/USD = %.2f", eth)

            # quote assets used by tokens we have indexed
            seen = {a.lower() for a in store.known_quote_addresses()}
            for q in store.quotes():
                if q["address"].lower() not in seen and q["kind"] != "usd":
                    continue
                if q["kind"] == "usd":
                    store.apply_quote_rate(q["address"], 1.0)
                elif q["kind"] == "crypto":
                    pair = C.CRYPTO_SYMBOL_MAP.get((q["symbol"] or "").upper())
                    if pair:
                        p = _coinbase_spot(pair)
                        if p:
                            store.apply_quote_rate(q["address"], p)
                            log.info("%s = %.2f USD", q["symbol"], p)
                elif q["kind"] == "stock":
                    sym = C.STOCK_SYMBOL_MAP.get((q["symbol"] or "").upper(),
                                                 (q["symbol"] or "").upper())
                    if sym:
                        p = _yahoo_price(sym)
                        if p:
                            store.apply_quote_rate(q["address"], p)
                            log.info("%s = %.2f USD", q["symbol"], p)
        except Exception as e:
            log.warning("price_loop: %s", str(e)[:160])
            store.discard()
        _stop.wait(C.PRICE_REFRESH_SECONDS)


# ------------------------------------------------------------------ prune
def prune_loop() -> None:
    """A no-op unless pruning is switched on.

    Launches are the index, not a cache of it, so the default is to keep
    every one ever seen. The loop stays so the switch still works and so
    nobody has to guess where the deletion used to happen.
    """
    if not C.PRUNE_LAUNCHES:
        return
    while not _stop.is_set():
        try:
            tip = chain.block_number()
            n = store.prune(C.INDEX_WINDOW_BLOCKS, tip)
            if n:
                log.info("pruned %s launches outside the window", n)
        except Exception as e:
            log.warning("prune_loop: %s", str(e)[:160])
            store.discard()
        _stop.wait(300.0)


# ------------------------------------------------------------- cache prune
def cache_loop() -> None:
    """Delete the image cache entries that are older than the ttl.

    Its own loop rather than a branch of `prune_loop`, because the two share
    nothing but the word: that one is off by default and about launches,
    which are the index, and this one is always on and about a folder whose
    only job is to hold things for a while. The cadences differ for the same
    reason - half an hour here against five minutes there - and `cached`
    already treats an expired entry as a miss, so what waits between passes
    is disk space, not correctness.

    Nothing here is on the request path, so the walk over ninety thousand
    directory entries costs the dashboard nothing.
    """
    while not _stop.is_set():
        try:
            n, freed = imgcache.prune()
            if n:
                log.info("image cache: dropped %s entries, %.1f MB, older than %.0f h",
                         n, freed / 1048576.0, C.IMAGE_TTL_SEC / 3600.0)
        except Exception as e:
            log.warning("cache_loop: %s", str(e)[:160])
            store.discard()
        _stop.wait(C.IMAGE_PRUNE_EVERY)


# ------------------------------------------------------------------ start
# The deep walk runs before the rest and then returns: it is a one-shot, and
# a thread that finishes is how a one-shot belongs in a list of loops.
# -------------------------------------------------------------- logo cache
def logo_loop() -> None:
    """Fetch the logos of the newest tokens before a browser asks for them.

    The grid opens on the newest two hundred tokens and those are exactly the
    logos nobody has ever asked for, so a cold page is a hundred separate
    round trips to an ipfs gateway that the browser makes six at a time. This
    walks the same list slowly, one image at a time, and leaves them on the
    disk, so opening the dashboard is mostly reads off this machine.

    One at a time and paced on purpose: this is the only thing here that
    spends somebody else's bandwidth rather than ours, the gateways are
    already the slow part, and a burst of two hundred parallel fetches is how
    this machine gets rate limited out of the very mirrors the page needs.

    It does nothing at all when the cache is already warm, which is the
    steady state once the first pass has run.
    """
    while not _stop.is_set():
        try:
            for u in store.recent_logos(C.LOGO_PREFETCH):
                if _stop.is_set():
                    break
                if imgcache.cached(u):
                    continue
                got = imgcache.ensure(u)
                if got is not None:
                    log.debug("logo cached: %s", u[:60])
                _stop.wait(C.LOGO_PREFETCH_PAUSE)
        except Exception as e:
            log.warning("logo_loop: %s", str(e)[:160])
            store.discard()
        _stop.wait(C.LOGO_PREFETCH_EVERY)


LOOPS = [
    ("backfill", backfill),
    ("ws", ws_loop),
    ("tail", tail_loop),
    ("trades", trades.trades_loop),
    ("fees", fees.fees_loop),
    ("candles", trades.candles_loop),
    ("timestamp", timestamp_loop),
    ("enrich", enrich_loop),
    ("refresh", refresh_loop),
    ("price", price_loop),
    ("prune", prune_loop),
    ("logos", logo_loop),
    ("cache", cache_loop),
]


def start_all() -> None:
    for name, fn in LOOPS:
        threading.Thread(target=fn, name=name, daemon=True).start()
        log.info("started loop: %s", name)
