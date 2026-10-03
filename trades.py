"""Indexing curve trades: volume per token, and the early window a snipe lives in.

Two things are collected from the same pass over the logs:

* running totals per token, plus time buckets so the UI can ask for 5m / 1h /
  24h without ever rescanning;
* every buy inside the launch window, which is the only slice the snipe check
  needs and is a bounded table rather than one row per trade ever made;
* what each wallet did in each token - bought, sold, paid, received - which is
  the coin card's holder list and its profit figures in one row;
* a finer price series per token, a minute to a point, for the card's chart.

The logs name the curve that emitted them, and a curve maps to exactly one
token, so a single query with no address filter covers every token at once.
That is what makes this affordable: one pass over 10k blocks returns a few
thousand trades, where asking per token would be twenty thousand queries.

History is walked backwards from the tip and live blocks forwards, in the same
tables. That is why every write is a guarded merge: the same span can be
replayed after a restart, and "the first buy" has to mean the earliest one the
chain saw, not the last one this process happened to look at.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Sequence

import chain
import config as C
import store

log = logging.getLogger("trades")

# eth_getLogs topics are positional, so a plain [buy, sell] would ask for
# logs whose topic0 is buy AND whose topic1 is sell, which matches nothing.
# Matching either event means one position holding a list of alternatives.
TRADE_TOPICS = [[chain.BUY_TOPIC, chain.SELL_TOPIC]]

_stop = threading.Event()

# curve address (lowercased) -> the token it belongs to. Rebuilding this is a
# single small query, so it is refreshed rather than invalidated precisely.
_curves: dict[str, dict[str, Any]] = {}
_curves_at = 0.0
_CURVES_TTL = 30.0

# Logs whose curve the map does not know yet.
#
# A log names the curve that emitted it and nothing else, and the curve is the
# only thing that says which token the trade belongs to - so a log folded
# before the launch indexer has written that token's row cannot be folded at
# all. It also cannot be looked at later: the walk has moved past the block,
# and no cursor ever goes back over it. Dropping such a log therefore lost a
# buy permanently, and the buys it lost were precisely the ones inside a fresh
# token's early window - the snipe verdict, which is the thing the display is
# for. The walk runs a second or two ahead of the launch indexer most of the
# time, so on a fresh token this was most of them.
#
# They wait here instead, in the order they were seen, and are folded on the
# first pass that finds their token indexed. Each entry carries the curve it
# belongs to, so deciding whether it can go in is a dict lookup rather than a
# second decode.
#
# Bounded by count, because the one situation that grows this list is a launch
# indexer that has stopped, and the only thing that must not happen then is for
# the memory to grow without limit. Entries leave as soon as their token
# appears, so in the ordinary case this list is a handful of logs wide: the
# walk only gets ahead of the launch indexer by the few blocks the two of them
# race over, and one tick of that loop clears them. Whatever is evicted is
# reported, never dropped quietly.
_pending: list[tuple[str, int, int, dict[str, Any]]] = []
_pending_keys: set[tuple[str, int, int]] = set()
_pending_lock = threading.Lock()
_pending_dropped = 0
_pending_warned = 0.0
_PENDING_MAX = 8000
_DROP_WARN_SEC = 60.0


def stop() -> None:
    _stop.set()


def curves(refresh: bool = False) -> dict[str, dict[str, Any]]:
    global _curves, _curves_at
    now = time.monotonic()
    if refresh or not _curves or now - _curves_at > _CURVES_TTL:
        _curves = store.curve_map()
        _curves_at = now
    return _curves


# ------------------------------------------------------------------ aggregate
def aggregate(logs: Sequence[dict[str, Any]],
              curve_map: dict[str, dict[str, Any]],
              decode=None, fetch_ts=None) -> dict[str, list[tuple]]:
    """Fold a span of raw buy/sell logs into the four tables' worth of rows.

    `decode` and `fetch_ts` are the seam that lets a second chain reuse this
    function rather than a copy of it. Everything below - the buckets, the
    candles, the positions, the early buys - is arithmetic over a decoded trade
    and a map keyed by whatever that chain calls a market, so the only things
    that are Robinhood's are the two calls that read a log and read a block.

    Both default to the Robinhood readers, and both are resolved here rather
    than bound in the signature: a default evaluated at import is a second copy
    of a module-level name that a patch or a reload would leave behind, which is
    the trap `chain.get_logs_chunked` already carries.
    """
    decode = decode or chain.decode_trade
    fetch_ts = fetch_ts or chain.block_timestamps
    # token -> mutable running state for this span
    agg: dict[str, dict[str, Any]] = {}
    buckets: dict[tuple[str, int], list[float]] = {}
    candles: dict[tuple[str, int], list[float]] = {}
    wallets: dict[tuple[str, str], list] = {}
    early: list[tuple] = []
    buyers: dict[tuple[str, str], None] = {}
    missing_ts: set[int] = set()
    # Logs folded too early to be folded: see the pending buffer above.
    unknown: list[tuple[str, int, int, dict[str, Any]]] = []

    for lg in logs:
        try:
            t = decode(lg)
        except Exception:
            continue
        if t is None:
            continue
        meta = curve_map.get(t["curve"].lower())
        if meta is None:
            # Either a deployment on this chain that is not ours, or a token
            # whose launch row has not landed yet. The two are told apart by
            # the caller, which is the only place that knows whether waiting
            # is worth anything.
            unknown.append((t["curve"].lower(), t["block"], t["log_index"], lg))
            continue

        addr = meta["address"]
        ts = t["ts"]
        if ts is None:
            missing_ts.add(t["block"])

        a = agg.get(addr)
        if a is None:
            a = agg[addr] = {
                "curve": t["curve"], "buys": 0, "sells": 0,
                "buy_volume": 0.0, "sell_volume": 0.0,
                "first": None, "last_block": 0, "last_ts": None,
            }
        if t["side"] == "buy":
            a["buys"] += 1
            a["buy_volume"] += t["quote"]
            buyers[(addr, t["who"])] = None
            launch_block = meta.get("launch_block")
            if launch_block is not None and \
                    t["block"] <= int(launch_block) + C.EARLY_BLOCKS:
                early.append((addr, t["block"], t["log_index"], ts, t["who"],
                              float(t["quote"]), float(t["tokens"]), t["tx"]))
        else:
            a["sells"] += 1
            a["sell_volume"] += t["quote"]

        # Logs arrive ordered by block then log index, so the first one seen
        # for a curve in this span is that span's earliest.
        if a["first"] is None or t["block"] < a["first"][0]:
            a["first"] = (t["block"], ts, t["who"], float(t["quote"]),
                          float(t["tokens"]), t["tx"])
        if t["block"] >= a["last_block"]:
            a["last_block"] = t["block"]
            if ts is not None:
                a["last_ts"] = ts

        # Position keeping is independent of the timestamp, so it happens
        # even when the node withheld blockTimestamp: the amounts are the
        # part a card cannot do without, and the times are decoration.
        buy = t["side"] == "buy"
        w = wallets.get((addr, t["who"]))
        if w is None:
            w = wallets[(addr, t["who"])] = [0.0, 0.0, 0.0, 0.0, None, None]
        w[0 if buy else 1] += float(t["tokens"])
        w[2 if buy else 3] += float(t["quote"])
        if ts is not None:
            if w[4] is None or ts < w[4]:
                w[4] = ts
            if w[5] is None or ts > w[5]:
                w[5] = ts

        if ts is not None:
            key = (addr, ts // C.TRADE_BUCKET_SEC)
            b = buckets.get(key)
            if b is None:
                b = buckets[key] = [0, 0, 0.0, 0.0]
            ck = (addr, ts // C.CANDLE_BUCKET_SEC)
            p = candles.get(ck)
            if p is None:
                p = candles[ck] = [0, 0, 0.0, 0.0]
            if buy:
                b[0] += 1
                b[2] += t["quote"]
                p[0] += 1
            else:
                b[1] += 1
                b[3] += t["quote"]
                p[1] += 1
            p[2] += t["quote"]
            p[3] += float(t["tokens"])

    rows: list[tuple] = []
    for addr, a in agg.items():
        f = a["first"] or (None,) * 6
        rows.append((addr, a["curve"], a["buys"], a["sells"],
                     a["buy_volume"], a["sell_volume"],
                     f[0], f[1], f[2], f[3], f[4], f[5],
                     a["last_block"] or None, a["last_ts"]))

    bucket_rows = [(addr, bucket, int(v[0]), int(v[1]), v[2], v[3])
                   for (addr, bucket), v in buckets.items()]
    # ts is stored next to the bucket so the chart never has to know the
    # bucket size the row was written with.
    candle_rows = [(addr, bucket, bucket * C.CANDLE_BUCKET_SEC, v[2], v[3],
                    int(v[0]), int(v[1]))
                   for (addr, bucket), v in candles.items()]
    wallet_rows = [(addr, who, v[0], v[1], v[2], v[3], v[4], v[5])
                   for (addr, who), v in wallets.items()]
    buyer_rows = [(addr, who) for (addr, who) in buyers]

    if missing_ts:
        # This node normally puts blockTimestamp in the log, but when it does
        # not, the buckets are worthless without it, so fetch it.
        known = store.cached_block_ts(sorted(missing_ts))
        todo = [b for b in missing_ts if b not in known]
        if todo:
            fetched = fetch_ts(todo)
            store.save_block_ts(fetched)
            known.update(fetched)
        bucket_rows, candle_rows, early, wallet_rows = _backfill_ts(
            agg, early, wallets, known, logs, curve_map, decode)

    # Deduplicate the early rows: the same buy can appear twice if a span is
    # replayed, and the primary key would silently drop one of them.
    seen: set[tuple] = set()
    uniq_early = []
    for e in early:
        k = (e[0], e[1], e[2])
        if k in seen:
            continue
        seen.add(k)
        uniq_early.append(e)

    return {"trades": rows, "buckets": bucket_rows, "candles": candle_rows,
            "wallets": wallet_rows, "early": uniq_early, "buyers": buyer_rows,
            "unknown": unknown}


def _backfill_ts(agg, early, wallets, known, logs, curve_map, decode):
    """Redo the timestamped parts once the block timestamps are known.

    Rare path: only when the node omits blockTimestamp from logs. Rather than
    threading timestamps through the main loop, replay from the same in-memory
    logs, which costs nothing extra to decode twice. The decoder is handed in
    for the same reason it is in `aggregate`: this is the same pass over the
    same logs, and a replay that read them with a different chain's decoder
    would be a second, disagreeing answer to a question already answered.
    """
    buckets: dict[tuple[str, int], list[float]] = {}
    candles: dict[tuple[str, int], list[float]] = {}
    out_early: list[tuple] = []
    for lg in logs:
        try:
            t = decode(lg)
        except Exception:
            continue
        if t is None:
            continue
        meta = curve_map.get(t["curve"].lower())
        if meta is None:
            continue
        ts = t["ts"] or known.get(t["block"])
        if ts is None:
            continue
        addr = meta["address"]
        key = (addr, ts // C.TRADE_BUCKET_SEC)
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = [0, 0, 0.0, 0.0]
        ck = (addr, ts // C.CANDLE_BUCKET_SEC)
        p = candles.get(ck)
        if p is None:
            p = candles[ck] = [0, 0, 0.0, 0.0]
        if t["side"] == "buy":
            b[0] += 1
            b[2] += t["quote"]
            p[0] += 1
        else:
            b[1] += 1
            b[3] += t["quote"]
            p[1] += 1
        p[2] += t["quote"]
        p[3] += float(t["tokens"])
        # The positions themselves were already accumulated; only the times
        # they carry had to wait for the block timestamps.
        w = wallets.get((addr, t["who"]))
        if w is not None:
            if w[4] is None or ts < w[4]:
                w[4] = ts
            if w[5] is None or ts > w[5]:
                w[5] = ts
        launch_block = meta.get("launch_block")
        if t["side"] == "buy" and launch_block is not None and \
                t["block"] <= int(launch_block) + C.EARLY_BLOCKS:
            out_early.append((addr, t["block"], t["log_index"], ts, t["who"],
                              float(t["quote"]), float(t["tokens"]), t["tx"]))
    rows = [(addr, bucket, int(v[0]), int(v[1]), v[2], v[3])
            for (addr, bucket), v in buckets.items()]
    candle_rows = [(addr, bucket, bucket * C.CANDLE_BUCKET_SEC, v[2], v[3],
                    int(v[0]), int(v[1]))
                   for (addr, bucket), v in candles.items()]
    return rows, candle_rows, out_early, wallets


def _defer(entries: Sequence[tuple[str, int, int, dict[str, Any]]]) -> None:
    """Hold logs whose token is not indexed yet, and age out what nobody came for.

    In the ordinary case this list is a handful of logs wide: the walk only
    gets ahead of the launch indexer by the few blocks the two of them race
    over, and one tick of that loop clears them. The bound below is for the
    case where it does not - a launch indexer that has stopped - where waiting
    forever would be a leak and saying nothing would be a lie.
    """
    global _pending, _pending_keys, _pending_dropped, _pending_warned
    dropped: list[tuple[str, int, int, dict[str, Any]]] = []
    with _pending_lock:
        for curve, block, log_index, lg in entries:
            key = (curve, block, log_index)
            if key in _pending_keys:
                continue
            _pending_keys.add(key)
            _pending.append((curve, block, log_index, lg))

        if len(_pending) > _PENDING_MAX:
            # Entries arrive in block order, so the front of the list is the
            # oldest, which is also the one least likely to resolve: whatever
            # was going to index these tokens has not done it in the time it
            # took this many more logs to arrive.
            cut = len(_pending) - _PENDING_MAX
            dropped = _pending[:cut]
            _pending = _pending[cut:]
            _pending_keys -= {(e[0], e[1], e[2]) for e in dropped}

    if not dropped:
        return
    _pending_dropped += len(dropped)
    now = time.monotonic()
    if now - _pending_warned > _DROP_WARN_SEC:
        _pending_warned = now
        blocks = [e[1] for e in dropped]
        log.warning(
            "trades: %s trade logs (blocks %s..%s) waited for a token that was "
            "never indexed and were dropped to keep the buffer bounded; if any "
            "of them belonged to a launch of this factory, its buys are missing "
            "from its verdict (%s dropped so far)",
            len(dropped), min(blocks), max(blocks), _pending_dropped)


def _retry_pending() -> tuple[int, int]:
    """Fold the logs that were waiting for their token to be indexed.

    Runs on every pass of the walk, including the passes whose own block range
    came back empty: a launch row landing between two spans is exactly the case
    where the range is empty and the buys waiting for it are not.
    """
    global _pending, _pending_keys
    with _pending_lock:
        if not _pending:
            return 0, 0
        cmap = curves(refresh=True)
        ready: list[dict[str, Any]] = []
        waiting: list[tuple[str, int, int, dict[str, Any]]] = []
        for e in _pending:
            (ready if e[0] in cmap else waiting).append(e)
        if not ready:
            return 0, 0
        # Taken out of the buffer here, while the lock is held, so that two
        # passes cannot both fold the same log and count it twice.
        _pending = waiting
        _pending_keys = {(e[0], e[1], e[2]) for e in waiting}

    logs = [e[3] for e in ready]
    res = aggregate(logs, cmap)
    # The map held every curve a moment ago and nothing removes keys from it,
    # so this cannot be populated. Keeping the call is what makes "no log is
    # dropped quietly" a property of the code rather than of that argument.
    _defer(res.get("unknown") or [])
    n, e = store.apply_trades(res)
    log.debug("trades: folded %s deferred tokens, %s early buys", n, e)
    return n, e


def index_spans(from_block: int, to_block: int,
                cards_only: bool = False) -> tuple[int, int]:
    """Index a block range of trades and merge it into the tables."""
    n = e = 0
    if from_block <= to_block:
        logs = chain.get_logs_chunked(from_block, to_block, address=None,
                                      topics=TRADE_TOPICS, span=C.TRADES_SPAN)
        if logs:
            res = aggregate(logs, curves())
            if not cards_only:
                # A cards_only pass replays blocks the walk has already been
                # through. Deferring from one would fold those logs a second
                # time when the retry ran, and volumes are sums.
                _defer(res.get("unknown") or [])
            n, e = store.apply_trades(res, cards_only=cards_only)
    rn, re = _retry_pending()
    return n + rn, e + re


def repair_spans(from_block: int, to_block: int,
                 dry_run: bool = False, on_span=None) -> dict[str, int]:
    """Re-read a walked range and give back the first buys it dropped.

    The walk reads each block once and moves on, so a log it could not attach
    to a token - because the token's row had not been written yet - was gone
    for good; the deferral buffer stops that happening now, but everything it
    already lost is still missing, and a missing first buy reads as an
    ordinary late one. This walks the same blocks again for those facts alone.

    Only first-buy columns and the two tables the verdict reads are written
    (see `store.repair_first`). Volumes, buckets and candles are sums and are
    deliberately left alone: this reads blocks the walk has already folded.

    `dry_run` runs the very same statements and rolls them back, so the counts
    it reports are the ones a real pass would write rather than a guess at
    them. Unknown curves are counted and skipped: they belong to a token that
    is not indexed at all, which is the running indexer's business, not this.
    """
    out = {"spans": 0, "logs": 0, "unknown": 0, "moved": 0, "early": 0,
           "dry": 1 if dry_run else 0}
    if from_block > to_block:
        return out
    cmap = curves(refresh=True)
    c = store.conn()
    for start in range(from_block, to_block + 1, C.TRADES_SPAN):
        end = min(start + C.TRADES_SPAN - 1, to_block)
        logs = chain.get_logs_chunked(start, end, address=None,
                                      topics=TRADE_TOPICS, span=C.TRADES_SPAN)
        out["spans"] += 1
        out["logs"] += len(logs)
        if logs:
            res = aggregate(logs, cmap)
            out["unknown"] += len(res.get("unknown") or [])
            moved, added = store.repair_first(res)
            if dry_run:
                c.rollback()
            else:
                c.commit()
            out["moved"] += moved
            out["early"] += added
        if on_span is not None:
            on_span(start, end, out)
    return out


def candles_loop() -> None:
    """Fill the coin-card tables for history indexed before they existed.

    price_points and wallet_trades arrived after the trade walk had already
    covered its window, so on an existing database they start empty and would
    only fill in for blocks seen from here on - every token already listed
    would open a card with no chart and no holders. This walks the range the
    trade walk has already covered, once, and stops.

    The upper bound is read once at the start and never moved. Everything
    above it belongs to the live tail, which writes these same tables as it
    goes, so a bound that followed the tip would have both of them counting
    the same blocks twice. A database built from scratch needs none of this -
    the walks fill the tables as they go - and finds nothing to do here.
    """
    if store.kv_get("candles_done"):
        return
    floor = int(store.kv_get("trades_back_block") or 0)
    # trades_block is written after the blocks below it are indexed, so it is
    # the top of what has actually been covered.
    top = int(store.kv_get("trades_block") or 0)
    if not floor or top <= floor:
        store.kv_set("candles_done", 1)
        return

    at = int(store.kv_get("candles_block") or floor)
    if at < floor:
        at = floor
    log.info("candles: backfilling %s..%s (%s blocks)",
             at, top, top - at)
    while not _stop.is_set() and at < top:
        upto = min(top, at + C.TRADES_SPAN)
        try:
            index_spans(at + 1, upto, cards_only=True)
        except Exception as exc:
            log.warning("candles: span %s..%s failed: %s", at, upto, exc)
            store.discard()
            _stop.wait(C.TRADES_PAUSE * 4)
            continue
        # Written after the span, the same way the trade walk does it: a
        # crash replays one span, which is the tolerance the additive tables
        # already have, and is why this is a cursor and not a count.
        store.kv_set("candles_block", upto)
        at = upto
        if at % (C.TRADES_SPAN * 10) < C.TRADES_SPAN:
            log.info("candles: at block %s of %s", at, top)
        _stop.wait(C.TRADES_PAUSE)
    if not _stop.is_set():
        store.kv_set("candles_done", 1)
        log.info("candles: backfill complete")



# ------------------------------------------------------------------ loop
def _finalize(back: int, fwd: int) -> None:
    """Credit the wallets that raced into tokens whose window has closed.

    Judged against the walk's cursors rather than the chain tip: a token is
    only judgeable once every block of its early window has been indexed, and
    the tip is not a promise that they have been.
    """
    rows = store.unfinalized_snipes(fwd, min_launch_block=back)
    if rows:
        racers = store.first_racers([r["address"] for r in rows])
        n = store.count_snipes(rows, racers)
        log.info("snipe check: %s tokens finalised, %s wallets credited",
                 len(rows), n)


def trades_loop() -> None:
    """Walk trades forward from the tip and backwards into history."""
    # Start live: the newest tokens are what the dashboard is for, and the
    # history walk fills in behind them.
    if not store.kv_get("trades_block"):
        store.kv_set("trades_block", chain.block_number())
    if not store.kv_get("trades_back_block"):
        store.kv_set("trades_back_block", chain.block_number())

    last_prune = 0.0
    while not _stop.is_set():
        try:
            tip = chain.block_number()

            fwd = int(store.kv_get("trades_block"))
            if tip > fwd:
                # Catch up in bounded steps so a long outage cannot turn into
                # one enormous range the node will refuse. The bound is one
                # span rather than three because a span is also the size of a
                # single write transaction: everything the walk reads in one
                # go it commits in one go, and a transaction of thirty
                # thousand blocks of trades is most of a gigabyte into the
                # write-ahead log, which is the file that then cannot be
                # checkpointed. Three smaller steps cost two more getLogs.
                upto = min(tip, fwd + C.TRADES_SPAN)
                n, e = index_spans(fwd + 1, upto)
                store.kv_set("trades_block", upto)
                if n:
                    log.info("trades +%s tokens (%s early buys) blocks %s..%s",
                             n, e, fwd + 1, upto)

            back = int(store.kv_get("trades_back_block"))
            floor = tip - C.TRADES_BACKFILL_BLOCKS
            if back > floor:
                start = max(floor + 1, back - C.TRADES_SPAN)
                n, e = index_spans(start, back - 1)
                store.kv_set("trades_back_block", start - 1)
                log.info("trades history %s..%s: %s tokens, %s early buys",
                         start, back - 1, n, e)

            _finalize(int(store.kv_get("trades_back_block")),
                      int(store.kv_get("trades_block")))

            if time.monotonic() - last_prune > C.WAL_TRUNCATE_SEC:
                last_prune = time.monotonic()
                store.prune_trades()
                # Asked unconditionally, because a guard here is a guard that
                # never opens: this walk has new blocks to fold on nearly every
                # iteration - they arrive every two seconds and the pause
                # between passes is half of one - so a flag meaning "this pass
                # wrote something" is true almost always, and the log it is
                # meant to reclaim would never be reclaimed. The flag was also
                # answering the wrong question: it is about the iteration, not
                # about a transaction being open at this instant, and those are
                # different things when every chunk commits on its own.
                # Contention is `wal_checkpoint`'s own business: it sets a two
                # second busy timeout and reports busy in the first column when
                # it cannot get the lock, rather than raising. So a failed
                # attempt costs two seconds and says so on the next line.
                at = store.wal_checkpoint()
                log.info("wal checkpoint: busy=%s frames=%s copied=%s", *at)
        except Exception as e:
            msg = str(e)
            log.warning("trades_loop: %s", msg[:200])
            store.discard()
            if chain.is_rate_limit(msg):
                _stop.wait(C.RATE_LIMIT_BACKOFF)
                continue
            _stop.wait(5.0)
        _stop.wait(C.TRADES_PAUSE)
