"""Was this token sniped?

The chain hands us two facts that together answer the question precisely,
without guessing at heuristics:

1. A launch and a buy are both logs, and every log carries its transaction
   hash and block number. If the first buy sits in the *same transaction* as
   the launch, someone bought atomically with the launch - a bundle, which is
   the strongest form of sniping there is because no human could have reacted.
2. Otherwise the distance in blocks between the launch and the first buy by
   someone other than the deployer is a direct measure of reaction speed. At
   roughly 0.1s per block, two blocks is a fifth of a second: faster than a
   person can click, so it is a bot.

Neither fact alone is enough. A deployer buying in their own launch looks
identical to a sniper by block distance, so the deployer has to be excluded
from the "who raced in" question while still being reported. And a wallet that
does this once might be lucky, so the same wallet doing it across many
launches is counted separately as a bot fingerprint.

Everything here is pure: it takes rows and returns a verdict, so it can be
checked against known tokens without a running server.
"""
from __future__ import annotations

import logging
from typing import Any

import config as C

log = logging.getLogger("snipe")

LABELS = ("bundled", "sniped", "early", "slow", "none", "unknown")

# Score weights. The block delta dominates because it is the one measurement
# that cannot be faked by simply spending more money.
_DELTA_POINTS = {0: 45, 1: 40, 2: 35}
_DELTA_LATE = (5, 25), (C.EARLY_BLOCKS, 15)
_BUNDLE_POINTS = 30
_SHARE_POINTS = ((10.0, 20), (5.0, 15), (2.0, 10), (0.5, 5))
_CONCENTRATION_POINTS = ((1, 15), (2, 8), (4, 3))
_BOT_POINTS = 15


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _delta_points(delta: int) -> int:
    if delta in _DELTA_POINTS:
        return _DELTA_POINTS[delta]
    for limit, pts in _DELTA_LATE:
        if delta <= limit:
            return pts
    return 5


def _share_points(share: float | None) -> int:
    if share is None:
        return 0
    for limit, pts in _SHARE_POINTS:
        if share >= limit:
            return pts
    return 0


def _concentration_points(buyers: int) -> int:
    for limit, pts in _CONCENTRATION_POINTS:
        if buyers <= limit:
            return pts
    return 0


def verdict(token: dict[str, Any], trade: dict[str, Any] | None,
            early: list[dict[str, Any]] | None = None,
            bot_hits: int | None = None,
            early_buyers: int | None = None,
            indexed: bool = True) -> dict[str, Any]:
    """Judge one token.

    token  needs launch_block, deployer, launch_tx, graduation_threshold
    trade  is the trades row: first_buy_block/ts/buyer/quote/tokens/tx
    early  is the early_buys rows for this token
    """
    early = early or []
    hits = int(bot_hits or 0)
    out: dict[str, Any] = {
        "label": "unknown",
        "score": None,
        "delta": None,
        "bundled": False,
        "deployer_first": False,
        "first_buy_block": None,
        "first_buy_ts": None,
        "first_buyer": None,
        "first_buy_quote": None,
        "first_buy_share": None,
        "early_buyers": int(early_buyers or 0),
        "bot_hits": hits,
        "bot": hits >= C.BOT_HITS_MIN,
    }
    if not indexed:
        # The history walk has not reached this token yet. Reporting "none"
        # here would claim the token was never bought when we simply have not
        # looked, which is a different and much worse statement.
        return out

    fb_block = trade.get("first_buy_block") if trade else None
    if fb_block is None:
        # Fully indexed and nobody ever bought. Not a snipe, just unloved.
        out["label"] = "none"
        out["score"] = 0
        return out

    launch_block = int(token.get("launch_block") or 0)
    deployer = (token.get("deployer") or "").lower()
    launch_tx = (token.get("launch_tx") or "").lower()
    fb_buyer = (trade.get("first_buyer") or "")
    fb_tx = (trade.get("first_buy_tx") or "").lower()

    bundled = bool(launch_tx and fb_tx and fb_tx == launch_tx)
    deployer_first = bool(deployer and fb_buyer.lower() == deployer)

    # The wallet that actually raced in: the first early buy by anyone other
    # than the deployer, and never one from the launch transaction itself.
    # A buy inside the launch transaction is by definition the launcher's own
    # (nobody can insert a call into someone else's transaction), so treating
    # it as a race would flag every dev buy as a snipe. The deployer exclusion
    # covers the normal case; the transaction exclusion holds even when the
    # launch is routed through a helper contract that holds the tokens for a
    # moment, which makes the on-chain buyer the router rather than the human.
    cand = next(
        (e for e in sorted(early, key=lambda x: (x["block"], x["log_index"] or 0))
         if (e.get("buyer") or "").lower() != deployer
         and (e.get("tx") or "").lower() != launch_tx),
        None,
    )

    if cand is not None:
        block = int(cand["block"])
        quote = _f(cand.get("quote"))
        buyer = cand.get("buyer")
        ts = cand.get("ts")
    else:
        # Nobody outside the deployer bought inside the window, so the only
        # thing left to report is whoever bought first overall.
        block = int(fb_block)
        quote = _f(trade.get("first_buy_quote"))
        buyer = trade.get("first_buyer")
        ts = trade.get("first_buy_ts")

    delta = max(0, block - launch_block)
    threshold = _f(token.get("graduation_threshold"))
    share = (quote / threshold * 100.0) if (quote and threshold) else None

    out.update(
        delta=delta,
        bundled=bundled,
        deployer_first=deployer_first,
        first_buy_block=block,
        first_buy_ts=ts,
        first_buyer=buyer,
        first_buy_quote=quote,
        first_buy_share=share,
    )

    # A bundle that ended up in a wallet other than the launcher's is a snipe
    # in the strictest sense: it was paid for and executed atomically, which
    # is the one thing no human can compete with. A bundle that went to the
    # launcher is just a dev buy and is reported as such.
    outsider_bundle = bundled and not deployer_first
    raced = cand is not None
    if outsider_bundle or (raced and delta <= C.SNIPE_BLOCKS):
        out["label"] = "sniped"
    elif raised_bundle_alone(bundled, deployer_first, raced):
        out["label"] = "bundled"
    elif raced:
        out["label"] = "early"
    else:
        out["label"] = "slow"

    if not raced and not outsider_bundle:
        # Only the launcher ever bought. Nothing was sniped, but an atomic
        # dev buy is still worth a small marker.
        out["score"] = 1 if bundled else 0
        return out

    score = 0
    if raced:
        score += _delta_points(delta)
    if outsider_bundle:
        score += _BUNDLE_POINTS
    score += _share_points(share)
    score += _concentration_points(out["early_buyers"])
    if out["bot"]:
        score += _BOT_POINTS
    out["score"] = min(100, score)
    return out


def raised_bundle_alone(bundled: bool, deployer_first: bool,
                        raced: bool) -> bool:
    """The launcher bought inside their own launch and nobody raced in.

    Read as one named fact rather than three booleans at the call site,
    because "the dev bought their own token at block zero" is a normal thing
    that only becomes a signal in the absence of anyone else racing.
    """
    return bool(bundled and deployer_first and not raced)


def summarise(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in LABELS}
    for r in rows:
        counts[r.get("label", "unknown")] = counts.get(
            r.get("label", "unknown"), 0) + 1
    return counts
