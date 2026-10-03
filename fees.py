"""What each token has paid the wallet that launched it.

The curve charges two things per trade: a protocol fee and the creator tax.
Both sit on the curve until a sweep collects them, and the sweep is a
transaction of its own, not part of a trade - so the balance of a curve says
nothing about what it has paid out over its life. `creatorTaxBalance()` is
exactly that: what is still owed. On a curve that is swept every couple of
hundred blocks, which is most of them, it is a small residual and reads as
nothing at all next to the total.

The total is in the sweep's own event, and the event names no addresses, so
the emitter is what says which curve it belongs to:

    FeesSwept(uint256 protocol, uint256 tokens, uint256 creator)

Three of the six values the curve pays are read here; this keeps the third.
Which of the three is the creator's was not guessed - the sweep transaction
that carried it was decoded and the pair token followed out of it, and the
third value is the transfer that lands on the creator fee recipient.

One pass covers every curve at once: the logs are fetched by topic with no
address filter, the same way trades are. The walk only ever moves forward, and
the cursor is written after the span that produced it, so a span replayed
after a restart is a span that will not be walked twice - which matters here,
because this figure is a running sum and counting a span twice would inflate
it by exactly the amount it is measuring.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import chain
import config as C
import store

log = logging.getLogger("fees")

_stop = threading.Event()

# curve address (lowercased) -> the token it belongs to. Same shape and the
# same reason as the trade walk's: one small query, refreshed rather than
# invalidated, so a log can be attributed without asking the database about it.
_curves: dict[str, dict[str, Any]] = {}
_curves_at = 0.0
_CURVES_TTL = 30.0


def stop() -> None:
    _stop.set()


def curves(refresh: bool = False) -> dict[str, dict[str, Any]]:
    global _curves, _curves_at
    now = time.monotonic()
    if refresh or not _curves or now - _curves_at > _CURVES_TTL:
        _curves = store.curve_map()
        _curves_at = now
    return _curves


def read_span(from_block: int, to_block: int) -> dict[str, int]:
    """One block span -> {token address: base units paid to its creator}."""
    logs = chain.get_logs_chunked(from_block, to_block, address=None,
                                  topics=[chain.SWEPT_TOPIC], span=C.FEES_SPAN)
    cmap = curves()
    paid: dict[str, int] = {}
    unknown: set[str] = set()
    for lg in logs:
        try:
            d = chain.parse_fees_swept(lg)
        except Exception:
            continue
        if d is None or not d["creator"]:
            continue
        meta = cmap.get(d["curve"].lower())
        if meta is None:
            # A curve this database has never seen - another deployment on
            # this chain, or a token that has aged out of the window.
            unknown.add(d["curve"])
            continue
        addr = meta["address"]
        paid[addr] = paid.get(addr, 0) + d["creator"]
    if unknown:
        log.debug("fees: %s sweeps from unknown curves in %s..%s",
                  len(unknown), from_block, to_block)
    return paid


def scan(from_block: int, to_block: int) -> int:
    """Collect one span and fold it into the running totals."""
    paid = read_span(from_block, to_block)
    n = store.add_creator_fees(paid) if paid else 0
    if paid:
        log.info("fees: %s curves, %s tokens paid, blocks %s..%s",
                 len(paid), n, from_block, to_block)
    return n


def start_block() -> int:
    """Where the first walk begins.

    The whole indexed window, not the tip: the figure is a total, and a total
    that starts today would show every token launched before it as having paid
    its creator nothing. The earliest launch the database holds is the floor,
    because a sweep cannot precede the curve that emitted it.
    """
    first = store.first_launch_block()
    if not first:
        return chain.block_number()
    return max(0, int(first) - 1)


def fees_loop() -> None:
    if not store.kv_get("fees_block"):
        store.kv_set("fees_block", start_block())
    while not _stop.is_set():
        try:
            tip = chain.block_number()
            at = int(store.kv_get("fees_block"))
            if tip > at:
                # Bounded per tick for the same reason the trade walk is: a
                # long outage must not become one range the node refuses.
                upto = min(tip, at + C.FEES_SPAN)
                scan(at + 1, upto)
                store.kv_set("fees_block", upto)
                # The first walk is the whole window and there is nothing
                # else to do while it runs, so it does not wait between spans.
                if upto - at < C.FEES_SPAN:
                    _stop.wait(C.FEES_SECONDS)
            else:
                _stop.wait(C.FEES_SECONDS)
        except Exception as e:
            msg = str(e)
            log.warning("fees_loop: %s", msg[:200])
            if chain.is_rate_limit(msg):
                _stop.wait(C.RATE_LIMIT_BACKOFF)
                continue
            _stop.wait(10.0)
