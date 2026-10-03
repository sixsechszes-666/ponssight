"""FastAPI app: JSON API + static dashboard + cached image proxy."""
from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import logging.handlers
import mimetypes
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

import requests
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import bridge
import chain
import config as C
import handles
import imgcache
import indexer
import keysafe
import snipe
import store
import trades
import xsource
from concurrent.futures import ThreadPoolExecutor

# Two handlers, and the file one is the point. Until it was added this call
# set a level and a format and nowhere to put the output, so every line this
# app wrote went to stderr - which the way it is started does not capture.
# That is how a warning about the write-ahead log reached nobody while the
# log grew to a hundred and nineteen gigabytes, four hours and a working
# detector after the same thing had already happened once. A stream handler
# stays for anyone watching a console; the file is what survives.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            str(C.DATA_DIR / "server.log"),
            maxBytes=32 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("server")

# The last answer /api/stats gave, and when. See the route for why.
_stats_lock = threading.Lock()
_stats_cache: dict[str, Any] = {"value": None, "at": 0.0}

from contextlib import asynccontextmanager, contextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init()
    # A walk runs in a thread of this process, so anything the table still calls
    # running is a walk whose thread died with the last shutdown. Left alone it
    # reads as a walk in flight forever - and, since the followings list offers
    # its sort controls only once a walk has ended, as a list that can never be
    # sorted.
    stuck = store.x_lists_unstick()
    if stuck:
        log.info("x walks: %d left running by the last shutdown are now stopped",
                 stuck)
    if C.INDEX_ENABLED:
        indexer.start_all()
    else:
        log.info("indexer off (PONS_INDEX=0): reading the database as it stands")
    log.info("dashboard on http://%s:%s", C.HOST, C.PORT)
    yield
    indexer.stop()


app = FastAPI(title="ponssight", docs_url="/docs", lifespan=lifespan)


@app.middleware("http")
async def revalidate_assets(request, call_next):
    """Make the browser check the page and its modules on every load.

    Nothing here is content-hashed and there is no build step, so a browser
    that keeps js/router.js from an hour ago and fetches index.html fresh gets a
    page whose buttons and whose router disagree - and the failure is silent and
    looks like a bug in the app. It is not hypothetical: the Handles tab
    appeared and clicking it landed on Launches, because router.js still held
    the six-tab list and `TABS.indexOf("handles") < 0` falls back to Launches by
    design. no-cache means revalidate, not refetch: the etag and last-modified
    are already on these responses, so the usual answer is a 304 with no body.

    It also carries the transaction sweep, because this is the one place every
    request passes through. A failed write in a request handler leaves that
    thread's transaction open, and a transaction nobody closes pins the
    write-ahead log at its snapshot for the life of the process - see
    `store.release_snapshots`. The sweep only takes transactions that have been
    open for minutes, so it cannot touch one a request still owns, and it is
    throttled inside `store.sweep_stale_transactions`. Running it before
    `call_next` means it happens between requests rather than after a response
    has been built.
    """
    store.sweep_stale_transactions()
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp
STATIC = Path(__file__).resolve().parent / "static"


# ------------------------------------------------------------------ API
@app.get("/api/tokens")
def api_tokens(
    sort: str = "newest",
    limit: int = Query(C.DEFAULT_LIMIT, ge=1, le=C.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    q: str | None = None,
    quote: str | None = None,
    min_progress: float | None = None,
    graduated: bool | None = None,
    since_minutes: int | None = None,
    min_volume_usd: float | None = Query(None, ge=0),
) -> dict[str, Any]:
    since = int(time.time()) - since_minutes * 60 if since_minutes else None
    rows = store.list_tokens(sort=sort, limit=limit, offset=offset, q=q,
                             quote=quote, min_progress=min_progress,
                             graduated=graduated, since_ts=since,
                             min_volume_usd=min_volume_usd)
    now = int(time.time())
    return {
        "now": now,
        "count": len(rows),
        "eth_usd": _f(store.kv_get("eth_usd")),
        "tokens": _attach(rows, now),
    }


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _creator_fees(r: dict[str, Any]) -> float | None:
    """What this token has paid the wallet that launched it, in whole
    units of the pair token, or None when nothing about it is known.

    Two numbers, because neither is the answer on its own. The paid
    total is every sweep the curve has run and is zero for anything
    launched since the last one; the curve balance is what it is holding
    for the creator now and is all a token too young to have been swept
    has. Added together the figure is right at both ends and a little
    under in the middle, where a curve is holding a fee share that the
    balance does not count.
    """
    try:
        scale = 10 ** int(r.get("quote_decimals") or 18)
    except (TypeError, ValueError):
        scale = 10 ** 18
    total, seen = 0, False
    for key in ("creator_fees_paid", "creator_tax_balance"):
        v = r.get(key)
        if v in (None, ""):
            continue
        try:
            total += int(v)
            seen = True
        except (TypeError, ValueError):
            continue
    return total / scale if seen else None


def _shape(r: dict[str, Any], now: int,
           extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "address": r["address"],
        "curve": r["curve"],
        "deployer": r["deployer"],
        "pair_token": r["pair_token"],
        "symbol": r["symbol"],
        "name": r["name"],
        "description": r["description"],
        "logo": r["logo"],
        "twitter": r["twitter"],
        "telegram": r["telegram"],
        "discord": r["discord"],
        "website": r["website"],
        "farcaster": r["farcaster"],
        "launch_block": r["launch_block"],
        "launch_ts": r["launch_ts"],
        "launch_tx": r.get("launch_tx"),
        "age_seconds": (now - r["launch_ts"]) if r["launch_ts"] else None,
        "quote_symbol": r["quote_symbol"] or "ETH",
        "quote_decimals": r["quote_decimals"],
        "price_quote": r["price_quote"],
        "mcap_quote": r["mcap_quote"],
        "mcap_usd": r["mcap_usd"],
        "progress_pct": r["progress_pct"],
        "graduated": bool(r["graduated"]),
        "ready_to_graduate": bool(r["ready_to_graduate"]),
        "real_quote_reserve": _f(r["real_quote_reserve"]),
        "graduation_threshold": _f(r["graduation_threshold"]),
        "phantom_quote": _f(r["phantom_quote"]),
        "total_supply": r["total_supply"],
        "decimals": r["decimals"],
        "meta_ok": bool(r["meta_ok"]),
        "creator_fees": _creator_fees(r),
        "creator_fees_paid": r.get("creator_fees_paid"),
        "creator_tax_balance": r.get("creator_tax_balance"),
        "creator_tax_bps": r.get("creator_tax_bps"),
    }
    out.update(extra or {})
    return out


# ------------------------------------------------------------------ trades
def _usd_rate(r: dict[str, Any]) -> float | None:
    """Recover the quote's USD rate from a row's own numbers.

    mcap_usd is written as mcap_quote times the rate, so the ratio is the rate
    without a second lookup. It is exactly 1 for a stablecoin pair.
    """
    mq, mu = r.get("mcap_quote"), r.get("mcap_usd")
    if mq and mu:
        return mu / mq
    if (r.get("quote_symbol") or "").upper() in C.STABLE_SYMBOLS:
        return 1.0
    if not r.get("quote_address") or \
            r["quote_address"].lower() == C.NATIVE_QUOTE:
        return _f(store.kv_get("eth_usd"))
    for q in store.quotes():
        if q["address"].lower() == (r.get("quote_address") or "").lower():
            return _f(q["usd_price"])
    return None


def _vol_object(t: dict[str, Any] | None, rate: float | None,
                qdec: int | None = None) -> dict[str, Any] | None:
    """One token's trade totals, in whole units of the pair token.

    The decimals are the token row's, not the trades row's. The trades
    row has no decimals of its own, so reading them from it quietly fell
    back to 18 - correct for an ether pair and wrong by 10^12 for every
    USDG one, which is how a token with 173,689 of volume came to be
    displayed as 0.0000001.
    """
    if not t:
        return None
    div = float(10 ** (qdec or 18))
    buy = float(t.get("buy_volume") or 0) / div
    sell = float(t.get("sell_volume") or 0) / div
    total = buy + sell
    return {
        "buys": int(t.get("buys") or 0),
        "sells": int(t.get("sells") or 0),
        "volume_quote": total,
        "volume_usd": total * rate if rate else None,
        "buy_volume_quote": buy,
        "sell_volume_quote": sell,
        "net_quote": buy - sell,
        "buyers": int(t.get("buyers") or 0),
        "last_trade_ts": t.get("last_trade_ts"),
    }


def _verdicts(rows: list[dict[str, Any]],
              trade_rows: dict[str, dict[str, Any]] | None = None,
              hits: dict[str, int] | None = None
              ) -> tuple[dict[str, dict[str, Any]], dict[str, list[Any]]]:
    """Snipe verdict for a page of token rows, keyed by address.

    The early buys are read even when the caller will not return them: the
    verdict needs the first buy by a wallet *other than the deployer*, and
    that row only exists here. Passing None instead would leave every raced
    token looking like nobody ever raced it.

    `hits` is the bot fingerprint count keyed by token address, not by wallet,
    because the wallet it belongs to depends on the verdict itself: the racer
    when someone raced, the first buyer otherwise.
    """
    if not rows:
        return {}, {}
    addrs = [r["address"] for r in rows]
    if trade_rows is None:
        trade_rows = store.trades_for(addrs)
    counts = store.early_buyer_counts(addrs)
    early_rows = store.early_buys_for(addrs)
    # Tokens that launched before the history walk reached them have not been
    # looked at yet, and calling those "none" would be a lie. The forward edge
    # is the other half of the same statement: the verdict is made of the buys
    # inside the token's early window, so a token whose window the walk has not
    # reached yet has none of them recorded, and a label for it would be about
    # blocks nobody has read. This is the bound `store.unfinalized_snipes` uses
    # to decide a token is ready to be judged, applied to the one being shown.
    back = _f(store.kv_get("trades_back_block"))
    fwd = _f(store.kv_get("trades_block"))
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        tr = trade_rows.get(r["address"])
        launch_block = float(r["launch_block"])
        indexed = (back is None or launch_block >= back) and \
                  (fwd is None or launch_block + C.EARLY_BLOCKS <= fwd)
        out[r["address"]] = snipe.verdict(
            r, tr, early_rows.get(r["address"]),
            (hits or {}).get(r["address"]),
            counts.get(r["address"]), indexed=indexed)
    return out, early_rows


def _judge(rows: list[dict[str, Any]],
           trade_rows: dict[str, dict[str, Any]] | None = None
           ) -> tuple[dict[str, dict[str, Any]], dict[str, list[Any]],
                      dict[str, dict[str, Any]]]:
    """Verdicts for a page of rows, with the bot fingerprint resolved.

    Two passes, because the fingerprint belongs to whoever the verdict names
    as first in, and that answer is what the first pass produces: when the
    deployer bought inside their own launch, the wallet that matters is the
    racer, not the row's own first buyer. The work is a few thousand
    comparisons over an already-loaded window.

    Every caller goes through here rather than calling `_verdicts` directly.
    A second path that skipped the second pass would score the same token 15
    points lower and make two tabs disagree about one coin.
    """
    if trade_rows is None:
        trade_rows = store.trades_for([r["address"] for r in rows])
    first, early_rows = _verdicts(rows, trade_rows, hits={})
    racers = [v["first_buyer"] for v in first.values() if v["first_buyer"]]
    hits = store.sniper_hits(racers) if racers else {}
    if not hits:
        return first, early_rows, trade_rows
    verd, _ = _verdicts(rows, trade_rows,
                        hits={a: hits.get(v["first_buyer"], 0)
                              for a, v in first.items() if v["first_buyer"]})
    return verd, early_rows, trade_rows


# The label distribution is derived from the same verdicts the rows are, and
# cached for a few seconds because it is asked for on every poll. Computing it
# in SQL instead is what produced badges that disagreed with the table: the
# rules live in snipe.verdict, and the second copy of them in SQL drifted - it
# counted a dev's own atomic buy as a snipe, since that buy is at delta zero
# and the guard only excluded a bundle that went to someone else.
# "unknown" is a token the history walk has not reached and "none" is one
# nobody ever bought; neither can be listed, so neither gets a chip. Counting
# them would put a number on a filter that opens an empty table. The walk's
# own progress is reported separately as `indexed`.
_LISTABLE = tuple(l for l in snipe.LABELS if l not in ("none", "unknown"))

_STATS_TTL = 5.0
_scored_cache: dict[str, Any] = {"at": 0.0, "val": None}


def _scored() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]],
                       dict[str, int]]:
    """Every judged token with its verdict, plus the label distribution.

    One pass serves the feed, its filters and the badges, so a count in a chip
    is by construction the count of the rows beneath it. It covers the whole
    indexed range rather than a page, which is what lets the badges be honest
    about tokens no page currently shows.
    """
    now = time.monotonic()
    if (_scored_cache["val"] is not None
            and now - _scored_cache["at"] < _STATS_TTL):
        return _scored_cache["val"]
    rows = store.snipe_candidates(limit=C.MAX_LIMIT * 40)
    # The rows already carry every trade column, so they are their own lookup
    # rather than a second read of the same data.
    by_addr = {r["address"]: r for r in rows}
    verd, _, _ = _judge(rows, by_addr)
    counts = {k: 0 for k in _LISTABLE}
    for v in verd.values():
        if v["label"] in counts:
            counts[v["label"]] += 1
    counts["indexed"] = len(rows)
    counts["bot_wallets"] = store.sniper_stats()["repeat"]
    val = (rows, verd, counts)
    _scored_cache.update(at=now, val=val)
    return val


def _scale_snipe(s: dict[str, Any],
                 quote_decimals: int | None) -> dict[str, Any]:
    """Restate a verdict's quote amounts in whole quote units.

    The verdict works in raw on-chain integers because that is what it is
    given, and `first_buy_share` is a ratio of two of them so it is already
    right. But every other *_quote field the API publishes is a whole-unit
    number, so leaving this one raw is what makes a 0.75 USDG buy render as
    "752.93M USDG" - the frontend has no decimal count to apply and no reason
    to expect one field to be different from the rest.
    """
    q = s.get("first_buy_quote")
    if q is None:
        return s
    div = float(10 ** (quote_decimals or 18))
    return dict(s, first_buy_quote=q / div)


def _attach(rows: list[dict[str, Any]], now: int,
            early: bool = False) -> list[dict[str, Any]]:
    """Add the `snipe` and `vol` objects to a page of token rows.

    Batched: the trades, the early buyers and the counts are one query each,
    and the verdict is computed in Python where the rules live in one readable
    place rather than spread through SQL.
    """
    if not rows:
        return []
    verd, early_rows, trade_rows = _judge(rows)
    if not early:
        early_rows = {}
    out = []
    for r in rows:
        s = _scale_snipe(verd[r["address"]], r["quote_decimals"])
        if early:
            div = float(10 ** (r["quote_decimals"] or 18))
            s = dict(s, early_buys=[
                {"block": e["block"], "buyer": e["buyer"],
                 "quote": (e["quote"] or 0) / div,
                 "tokens": e["tokens"], "ts": e["ts"], "tx": e["tx"]}
                for e in early_rows.get(r["address"], [])])
        out.append(_shape(r, now, {
            "snipe": s,
            "vol": _vol_object(trade_rows.get(r["address"]), _usd_rate(r),
                               r.get("quote_decimals")),
        }))
    return out


@app.get("/api/handle")
def api_handle(h: str = "") -> dict[str, Any]:
    """Every indexed launch that claims one X handle.

    Three answers, and the checker has to keep them apart because they mean
    different things to somebody deciding whether to launch under a handle:

      handle is null   the input is not a handle at all - empty, or one of the
                       prose entries this column is full of
      rows is empty    a real handle, and nothing in the window claims it
      rows has entries somebody has already launched under it, and the
                       deployer count says whether that was the account's own
                       launch or a crowd of copiers

    The window matters and is reported rather than assumed: the launch table
    is rolling, so an empty answer means "not in the last N hours", never
    "never".
    """
    q = (h or "").strip()
    handle = handles.norm(q)
    rows: list[dict[str, Any]] = []
    if handle:
        rows = handles.claims(store.by_handle(handle), handle)
    now = int(time.time())
    return {
        "query": q,
        "handle": handle,
        # A reserved path normalises cleanly and is still not an account, so
        # it is its own flag rather than a second kind of None.
        "reserved": handle in handles.RESERVED if handle else False,
        "summary": handles.summarise(rows),
        "coverage": store.handle_coverage(),
        "now": now,
        "tokens": _attach(rows, now) if rows else [],
    }


# --------------------------------------------------- x profiles and followings
# A walk against somebody else's API, so the shape of this is not the shape of
# the rest of the app. Two things follow from that:
#
#  * the rows it produces outlive the process. They are in the database, and a
#    restart mid-walk loses the walk and not the data - so the run dict below is
#    progress, not state, and the page reads the table rather than the run. That
#    is also what makes the list appear in batches: a page is committed the
#    moment X answers it, and the page can read it before the walk has finished.
#  * it is deliberately slow, because the limit is X's and not ours. One walk at
#    a time keeps that limit spendable; a second one would just queue behind the
#    first inside the client's lock with both reported as running.
_X_RUNS: dict[str, dict[str, Any]] = {}
_x_lock = threading.Lock()
X_RUNS_KEEP = 20


def _x_run(run_id: str) -> dict[str, Any] | None:
    with _x_lock:
        return _X_RUNS.get(run_id)


def _x_handle(h: str) -> str:
    """The handle in an /api/x request, or a 400.

    A reserved path normalises cleanly and is not an account - `x.com/i/search`
    is the example this project already has a list for - so it is refused here
    rather than turned into a request for a profile that cannot exist.
    """
    handle = handles.norm(h or "")
    if not handle:
        raise HTTPException(400, "not an x handle")
    if handle in handles.RESERVED:
        raise HTTPException(400, "%r is a reserved x path, not an account" % handle)
    return handle


@app.get("/api/x/profile")
def api_x_profile(h: str = "", refresh: int = 0) -> dict[str, Any]:
    """One account's profile, plus what the local index knows about its handle.

    Cached in `x_users` for `PONS_X_PROFILE_TTL` (a day by default), because a
    profile is not news and every lookup is a request against a limit that is
    spent better on followings. `refresh=1` goes to X anyway.

    It answers from the cache even when X cannot be reached, and says so -
    `source` is `cache-stale` and the age is in the answer. A profile an hour
    old is worth more to somebody reading the tab than an error page, and the
    page can print the age rather than pretending it is current.

    The launch verdicts are the same ones `GET /api/handle` gives and come from
    the same two functions, so the two halves of the tab cannot disagree about
    what "three launches claim this handle" means. They are also the half that
    needs no network at all.
    """
    handle = _x_handle(h)
    now = int(time.time())

    prof = store.x_user_get(handle=handle)
    age = (now - int(prof["fetched_at"] or 0)) if prof else None
    source, note = "cache", ""
    fresh = prof and age is not None and age <= C.X_PROFILE_TTL

    if not prof or not fresh or refresh:
        ok, why = xsource.available()
        if not ok:
            if not prof:
                raise HTTPException(503, why)
            source, note = "cache-stale", why
        else:
            try:
                prof = xsource.profile(handle)
                store.x_user_put(prof)
                age, source = 0, "x"
            except xsource.XUnavailable as e:
                if not prof:
                    # Nothing cached and no answer: this is the one case with
                    # nothing to show, and it is X's fault rather than a 404.
                    raise HTTPException(502, str(e))
                source, note = "cache-stale", str(e)

    rows = handles.claims(store.by_handle(handle), handle)
    summary = handles.summarise(rows)
    out: dict[str, Any] = {
        "query": (h or "").strip(),
        "handle": handle,
        "profile": prof,
        "age": age,
        "source": source,
        "note": note,
        "summary": summary,
        "coverage": store.handle_coverage(),
        "tokens": _attach(rows, now) if rows else [],
        "now": now,
    }
    # What is already parsed of this person's followings, if anything. The page
    # needs it to decide whether to offer the button or draw the list, and it is
    # the whole reason a second visit to the same profile costs no requests.
    if prof:
        out["list"] = store.x_list_get(str(prof["id"]))
        out["parsed"] = store.x_follows_count(str(prof["id"]))
    return out


@app.get("/api/x/followings")
def api_x_followings(h: str = "", offset: int = 0, limit: int = 100,
                     sort: str = "", sort2: str = "") -> dict[str, Any]:
    """One page of a stored followings list, with the launch verdict per person.

    The single read the page makes for both jobs: scrolling asks for the next
    hundred, and polling while a walk runs asks for the same hundred again and
    finds more behind it. One endpoint rather than two means one loop in the
    browser instead of two that can disagree.

    `parsed` is how many rows are actually in the table, `total` is what the
    profile says the account follows, and `state` is whether a walk is running.
    Those three together are what stops a partial list from reading as a
    complete one - the page prints "300 of 5000" while the walk is going and
    "815 of 815" when it is done, and it can only do that because they are all
    in the answer.

    `sort` and `sort2` order the whole list, not the page: the order is built
    over every row the owner has and only then sliced, because sorting the
    hundred rows already loaded would put a person first who is not first. Two
    keys because one is rarely enough - the interesting people are the ones with
    several launches *and* an audience, and that is a different order from
    either alone. Both come from `store.FOLLOW_SORTS`; a name that is not in it
    is dropped, and the keys actually applied are echoed back so the page can
    tell an order it asked for from one it did not get.

    The launch count and the deployer count come from `handle_stats`, which is
    the same call that ordered the list, so the number in a row is the number
    the row was sorted by rather than a second opinion about it. `handles_for`
    is still asked for this page, for the largest token, which is the one thing
    the ordering does not need.
    """
    handle = _x_handle(h)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    prof = store.x_user_get(handle=handle)
    if not prof:
        # The list is keyed by the id X gives an account, so a profile is needed
        # before there is anything to read. Fetched here rather than refused,
        # because the page asks for the profile and the list at the same time
        # and refusing would make which one wins a race. A profile already in
        # the table is used as it is: a second visit to a profile costs no
        # requests at all, which is the point of storing it.
        ok, why = xsource.available()
        if not ok:
            raise HTTPException(503, why)
        try:
            prof = xsource.profile(handle)
            store.x_user_put(prof)
        except xsource.XUnavailable as e:
            raise HTTPException(502, str(e))
    owner_id = str(prof["id"])

    applied = [s for s in (sort, sort2) if s in store.FOLLOW_SORTS][:2]
    if applied:
        order = store.x_follows_order(owner_id, *applied)
        rows = store.x_follows_rows_by_ord(owner_id, order[offset:offset + limit])
    else:
        rows = store.x_follows_page(owner_id, offset, limit)

    names = [r.get("handle") for r in rows]
    stats = store.handle_stats(names)
    tops = store.handles_for(names)
    for r in rows:
        h = handles.norm(r.get("handle"))
        st = stats.get(h) if h else None
        v = tops.get(h) or None
        r["launches"] = int((st or {}).get("launches", 0))
        r["deployers"] = int((st or {}).get("deployers", 0))
        r["top"] = ({"address": v["top_address"], "symbol": v["top_symbol"],
                     "mcap_usd": v["top_mcap_usd"]} if v else None)
    state = store.x_list_get(owner_id) or {}
    return {
        "handle": handle,
        "owner_id": owner_id,
        "rows": rows,
        "offset": offset,
        "limit": limit,
        "sort": applied[0] if applied else "",
        "sort2": applied[1] if len(applied) > 1 else "",
        "parsed": store.x_follows_count(owner_id),
        "total": state.get("total") or (prof.get("following") if prof else None),
        "state": state.get("state") or "none",
        "done_at": state.get("done_at"),
        "error": state.get("error"),
        "running": _x_running(owner_id),
    }


def _x_running(owner_id: str) -> dict[str, Any] | None:
    """The live followings run for this person, if there is one.

    Keyed on `kind` as well as `owner_id`, because `_X_RUNS` holds two shapes of
    run: a followings walk carries `owner_id` and a profiles walk carries
    `address`. Reading `r["owner_id"]` off a profiles run is a KeyError on the
    page, so the kind is checked first and the key read with `.get`.
    """
    with _x_lock:
        for r in _X_RUNS.values():
            if (r.get("kind", "followings") == "followings"
                    and r.get("state") == "running"
                    and r.get("owner_id") == owner_id):
                return _x_public(r)
    return None


def _x_public(run: dict[str, Any]) -> dict[str, Any]:
    """A run as the page sees it, without the thread's control flags.

    The flags are the only keys with a leading underscore, so this stays correct
    as the run grows - and `state` is what the page acts on anyway: a stop that
    has been asked for but not yet honoured is still `running`, and the page
    should keep polling through that rather than draw a stopped list.
    """
    return {k: v for k, v in run.items() if not k.startswith("_")}


def _run_x_list(run_id: str, owner_id: str, handle: str,
                total: int | None, max_pages: int) -> None:
    """Walk one account's followings, committing each page as it lands.

    Runs in its own thread and touches the database through the same store
    functions the request handlers use - so it holds no connection of its own
    and cannot leave one open if it dies.

    `seen_at` is fixed once, at the start, and every row this walk sees is
    stamped with it. That is what makes unfollows visible later: after a walk
    that reached the end, a row still carrying an older stamp is somebody this
    account no longer follows. It is deliberately not computed per page, because
    then every row would look freshly seen and the comparison would say nothing.
    """
    run = _x_run(run_id)
    if not run:
        return
    seen_at = int(time.time())

    def on_page(users: list[dict[str, Any]], cursor: str) -> None:
        store.x_follows_put(owner_id, users, seen_at)
        parsed = store.x_follows_count(owner_id)
        with _x_lock:
            run["pages"] += 1
            run["walked"] += len(users)
            # Read from the table rather than adding up the pages, because the
            # two differ on a re-walk: an account already known is not inserted
            # again, so a page of 200 can add fewer than 200 rows - or none.
            run["parsed"] = parsed
            run["cursor"] = cursor
            run["updated_at"] = int(time.time())
        store.x_list_put(owner_id, handle=handle, state="running",
                         pages=run["pages"], users=parsed, total=total,
                         cursor=cursor, started_at=seen_at)

    state, error = "ok", ""
    try:
        res = xsource.followings(
            owner_id, pages=max_pages, on_page=on_page,
            should_stop=lambda: bool(run.get("_stop")),
            deadline=time.time() + C.X_MAX_SEC)
        state, error = res["state"], res["error"]
        log.info("x walk %s: @%s %s pages, %s users, %s in %.1fs",
                 run_id, handle, res["pages"], res["users"], state, res["seconds"])
    except xsource.XUnavailable as e:
        state, error = "error", str(e)
        log.warning("x walk %s: @%s stopped: %s", run_id, handle, e)
    except Exception as e:  # a bug of ours, not X's - worth a traceback
        state, error = "error", "%s: %s" % (type(e).__name__, str(e)[:200])
        log.exception("x walk %s: @%s failed", run_id, handle)

    parsed = store.x_follows_count(owner_id)
    with _x_lock:
        run["state"] = state
        run["error"] = error
        run["parsed"] = parsed
        run["finished_at"] = int(time.time())
        run["updated_at"] = run["finished_at"]
    store.x_list_put(owner_id, state=state, error=error, pages=run["pages"],
                     users=parsed, total=total, done_at=int(time.time()))


@app.post("/api/x/followings")
def api_x_followings_start(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Start walking one account's followings. Returns the run id.

    Behind a POST and a button rather than triggered by the search box, because
    a walk is twenty-odd requests against somebody else's rate limit and a typo
    in a handle should not spend them.

    One walk at a time. X's limits are per account and the client is a single
    shared session, so a second walk would serialise behind the first at page
    granularity while both claimed to be running - and the page would show two
    progress bars for one rate limit. Refusing is the honest answer.
    """
    handle = _x_handle(body.get("h"))
    refresh = int(body.get("refresh") or 0)
    max_pages = int(body.get("max_pages") or C.X_MAX_PAGES)
    max_pages = max(0, min(max_pages, 500))

    ok, why = xsource.available()
    if not ok:
        raise HTTPException(503, why)

    prof = store.x_user_get(handle=handle)
    if refresh or not prof:
        try:
            prof = xsource.profile(handle)
        except xsource.XUnavailable as e:
            if not prof:
                raise HTTPException(502, str(e))
        else:
            store.x_user_put(prof)
    owner_id = str(prof["id"])
    total = prof.get("following")

    with _x_lock:
        # Only another followings walk stands in the way of this one. A profiles
        # run is a different question against the same account and holds no
        # `handle`, so it is neither a conflict nor readable as one.
        live = [r for r in _X_RUNS.values()
                if r.get("kind", "followings") == "followings"
                and r.get("state") == "running"]
        if live:
            raise HTTPException(409, "already walking @%s - stop it first"
                                % live[0].get("handle"))
        if len(_X_RUNS) >= X_RUNS_KEEP:
            for k in sorted(_X_RUNS, key=lambda k: _X_RUNS[k]["started_at"])[:-5]:
                _X_RUNS.pop(k, None)
        run_id = uuid.uuid4().hex[:12]
        _X_RUNS[run_id] = {
            "id": run_id, "kind": "followings", "owner_id": owner_id,
            "handle": handle,
            "state": "running", "pages": 0, "walked": 0, "parsed": 0,
            "cursor": "", "total": total, "error": "",
            "started_at": int(time.time()), "updated_at": int(time.time()),
            "_stop": False,
        }
    store.x_list_put(owner_id, handle=handle, state="running", total=total,
                     started_at=int(time.time()))
    log.info("x walk %s: starting @%s (%s followings)",
             run_id, handle, total)
    threading.Thread(target=_run_x_list,
                     args=(run_id, owner_id, handle, total, max_pages),
                     daemon=True).start()
    return _x_public(_x_run(run_id))


@app.get("/api/x/followings/{run_id}")
def api_x_followings_get(run_id: str) -> dict[str, Any]:
    """How far a walk has got. Progress, not the list - the list is the table."""
    run = _x_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return _x_public(run)


@app.post("/api/x/followings/{run_id}/stop")
def api_x_followings_stop(run_id: str) -> dict[str, Any]:
    """Ask a walk to stop at the next page boundary.

    Between pages and not in the middle of one, so what is already committed is
    a whole page and the cursor is a real place to resume from. The run ends as
    `stopped` with `users < total`, which is the same shape as a walk that hit a
    rate limit - a partial list that says it is partial.
    """
    run = _x_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    with _x_lock:
        if run["state"] == "running":
            run["_stop"] = True
            run["updated_at"] = int(time.time())
            log.info("x walk %s: stop asked for", run_id)
    return _x_public(run)


@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    """The header numbers, counted at most once every PONS_STATS_TTL seconds.

    Five counts over the tokens table, one of them a scan, and the page asks
    for the result on every polling tick - several times a tick with more than
    one tab open. None of it changes faster than a block does, so the second
    caller inside the window gets the first caller's answer.

    The lock is held across the count on purpose. Releasing it while counting
    would let two callers count at once, which is the thing this exists to
    stop; a caller that waits a moment for the answer is the better trade.
    """
    now = time.monotonic()
    with _stats_lock:
        if (_stats_cache["value"] is not None
                and now - _stats_cache["at"] < C.STATS_TTL):
            return _stats_cache["value"]
        s = store.stats()
        s["eth_usd"] = _f(store.kv_get("eth_usd"))
        s["chain_id"] = C.CHAIN_ID
        s["factory"] = C.FACTORY
        _stats_cache["value"] = s
        _stats_cache["at"] = time.monotonic()
        return s


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    """What a wallet needs and the page cannot derive.

    Read once at boot. The project id is not a secret - it ships in the page
    either way and only names the app to the relay - but it is configuration
    rather than code, so it comes from the environment like the rest of it.
    """
    return {
        "chain_id": C.CHAIN_ID,
        "chain_hex": hex(C.CHAIN_ID),
        "chain_name": C.CHAIN_NAME,
        "rpc_url": C.PUBLIC_RPC_URL,
        # The chain the transfer route passes through. The page needs to switch
        # the wallet to it for the second leg, and a wallet that has never
        # heard of Arbitrum needs it added, which takes a name and an rpc url.
        "arb_chain_id": C.ARB_CHAIN_ID,
        "arb_chain_hex": C.ARB_CHAIN_HEX,
        "arb_chain_name": C.ARB_CHAIN_NAME,
        "arb_rpc_url": C.ARB_RPC_URL,
        "walletconnect_project_id": C.WALLETCONNECT_PROJECT_ID,
    }


@app.get("/api/quotes")
def api_quotes() -> dict[str, Any]:
    return {"quotes": store.quotes()}


@app.get("/api/token/{address}")
def api_token(address: str) -> dict[str, Any]:
    """One token, everything known about it.

    The whole verdict, not just the label: this is the call that answers "was
    this sniped" for a single coin, so it carries the early buys the verdict
    was made from. Without them the label would have to be taken on faith.
    """
    rows = store.list_tokens(limit=1, q=address)
    if not rows:
        raise HTTPException(404, "not found")
    row = rows[0]
    out = _attach([row], int(time.time()), early=True)[0]
    # Raw base units, the same as the flat fields on this response and the same
    # as /api/launch/config: a reserve is not a trade amount, and the UI scales
    # these with fmtBig. `progress_pct` is the one figure that is already a
    # human number, because it is a ratio.
    out["curve_config"] = {
        "phantom_quote": _f(row.get("phantom_quote")),
        "graduation_threshold": _f(row.get("graduation_threshold")),
        "real_quote_reserve": _f(row.get("real_quote_reserve")),
        "sellable_tokens": row.get("sellable_tokens"),
        "reserved_tokens": row.get("reserved_tokens"),
        "total_supply": row.get("total_supply"),
        "graduated": bool(row["graduated"]),
    }
    return out




# ------------------------------------------------------------------ coin card
# One minute buckets, so a day of them is the cap on a chart request; the
# token's whole life is not, because a card is drawn to be looked at rather
# than scrolled, and the range selector asks for less when it wants less.
_CARD_POINTS = 1440
# The curve state a position is valued against, read off the token row and
# merged into each holder's amounts so one dict holds both halves.
_CURVE_FIELDS = ("graduated", "phantom_quote", "real_quote_reserve",
                 "sellable_tokens", "reserved_tokens", "total_supply",
                 "decimals", "quote_decimals")


def _with_curve(wt: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    return {**(wt or {}), **{k: row.get(k) for k in _CURVE_FIELDS}}


def _pos_usd(pos: dict[str, Any], rate: float | None) -> dict[str, Any]:
    """Add the USD half to a position already valued in its quote units."""
    for k in ("spent", "received", "realized", "unrealized", "pnl"):
        v = pos.get(k)
        pos[k + "_usd"] = v * rate if (v is not None and rate) else None
    return pos


def _row_position(t: dict[str, Any],
                  rate: float | None) -> dict[str, Any]:
    """A sniper row's position in its own quote units, and in USD.

    Both, because the two answer different questions: the quote figure is what
    the wallet actually paid and received on chain, and the USD figure is what
    lets one row be compared with the next when they are not the same asset.
    """
    return _pos_usd(_position(t, t), rate)


def _position(wt: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    """A wallet's position in a token, in whole units, ready to display.

    `store.position_value` works in raw base units because that is what the
    chain gave it; every other number the API publishes is whole units, so
    the conversion happens here, once, for both the holder list and the
    sniper profit column.
    """
    v = store.position_value(wt, _with_curve(wt, row))
    q = float(10 ** (row.get("quote_decimals") or 18))
    d = float(10 ** (row.get("decimals") or 18))
    v["net_tokens"] = v["net_tokens"] / d
    for k in ("spent", "received", "realized", "unrealized", "pnl"):
        if v.get(k) is not None:
            v[k] = v[k] / q
    return v


@app.get("/api/token/{address}/card")
def api_card(address: str,
             hours: int = Query(6, ge=0, le=24),
             holders: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Everything the coin card draws, in one round trip.

    The card is opened by a click, so it is worth one request rather than
    three: the token and its verdict, the chart and the holder list come back
    together, and changing the chart's range refetches this same call. The
    three are built from separate tables and do not agree to the second
    anyway - the chart is bucketed, the holders are read live - so splitting
    them would buy nothing but latency.
    """
    rows = store.list_tokens(limit=1, q=address)
    if not rows:
        raise HTTPException(404, "not found")
    row = rows[0]
    addr = row["address"]
    token = _attach([row], int(time.time()), early=True)[0]

    dec = int(row.get("decimals") or 18)
    qdec = int(row.get("quote_decimals") or 18)
    # Raw quote per raw token, made into whole quote per whole token, which is
    # the same shape as the token's own price_quote.
    scale = (10 ** dec) / (10 ** qdec)
    qdiv = float(10 ** qdec)

    since = int(time.time()) - hours * 3600 if hours else None
    points: list[dict[str, Any]] = []
    for p in store.price_series(addr, since_ts=since, limit=_CARD_POINTS):
        tok = float(p["tokens"] or 0)
        points.append({
            "t": p["ts"],
            "price": (p["quote"] / tok * scale) if tok else None,
            "volume_quote": (p["quote"] or 0) / qdiv,
            "buys": int(p["buys"] or 0),
            "sells": int(p["sells"] or 0),
        })

    # A token's first trade is rarely at its launch, and a chart that starts
    # at the first trade hides the jump everyone is looking at. The launch
    # price is not a guess: nothing is sold yet, so virtual tokens is the
    # whole supply and the ratio is the phantom reserve over it.
    launch_price = None
    try:
        sup = float(row["total_supply"])
        if sup > 0:
            launch_price = float(row["phantom_quote"]) / sup * scale
    except (KeyError, TypeError, ValueError):
        pass
    if launch_price and row["launch_ts"] and (
            not points or points[0]["t"] > row["launch_ts"]):
        points.insert(0, {"t": row["launch_ts"], "price": launch_price,
                          "volume_quote": 0.0, "buys": 0, "sells": 0})

    prices = [p["price"] for p in points if p["price"] is not None]
    hrows, htotal = store.token_holders(addr, limit=holders)
    supply = float(row["total_supply"] or 0) / (10 ** dec)
    deployer = (row["deployer"] or "").lower()
    racer = (token.get("snipe") or {}).get("first_buyer") or ""
    hits = store.sniper_hits([h["wallet"] for h in hrows]) if hrows else {}
    held = 0.0
    holder_rows = []
    for h in hrows:
        pos = _position(h, row)
        wallet = h["wallet"] or ""
        held += pos["net_tokens"]
        n = int(hits.get(h["wallet"], 0))
        holder_rows.append({
            "wallet": wallet,
            "tokens": pos["net_tokens"],
            "share_pct": (pos["net_tokens"] / supply * 100.0) if supply else None,
            "spent_quote": pos["spent"],
            "received_quote": pos["received"],
            "realized_quote": pos["realized"],
            "unrealized_quote": pos["unrealized"],
            "pnl_quote": pos["pnl"],
            "roi_pct": pos["roi_pct"],
            "pnl_known": pos["known"],
            "first_ts": h["first_ts"],
            "last_ts": h["last_ts"],
            "is_deployer": bool(deployer and wallet.lower() == deployer),
            "is_first_buyer": bool(racer and wallet.lower() == racer.lower()),
            "bot_hits": n,
            "bot": n >= C.BOT_HITS_MIN,
        })

    # What the race was worth, on the card itself: the same two facts the
    # snipe verdict is built from, turned into money. A wallet that has sold
    # out is reported too, which is the point of looking it up by address
    # rather than reading it out of the holder list.
    snipe = token.get("snipe") or {}
    first = snipe.get("first_buyer")
    if first:
        wt = store.wallet_position(addr, first)
        if wt:
            snipe = dict(snipe, first_buyer_position=_position(wt, row))
            token["snipe"] = snipe

    return {
        "now": int(time.time()),
        "token": token,
        "chart": {
            "bucket_sec": C.CANDLE_BUCKET_SEC,
            "hours": hours,
            "points": points,
            "launch_ts": row["launch_ts"],
            "launch_price_quote": launch_price,
            "price_quote": row["price_quote"],
            "min_price_quote": min(prices) if prices else None,
            "max_price_quote": max(prices) if prices else None,
            "last_price_quote": prices[-1] if prices else None,
            "volume_quote": sum(p["volume_quote"] for p in points),
            "buys": sum(p["buys"] for p in points),
            "sells": sum(p["sells"] for p in points),
            "graduated": bool(row["graduated"]),
        },
        "holders": {
            "count": htotal,
            "shown": len(holder_rows),
            "supply": supply,
            "held": held,
            "held_pct": (held / supply * 100.0) if supply else None,
            "rows": holder_rows,
        },
    }


# ------------------------------------------------------------------ volume
_WINDOWS = {"5m": 300, "1h": 3600, "6h": 21600, "24h": 86400, "all": None}


def _row_volumes(r: dict[str, Any]) -> tuple[float, float, float]:
    """(buy, sell, total) in quote units, from either row shape."""
    div = float(10 ** (r.get("quote_decimals") or 18))
    buy = float(r.get("buyvol", r.get("buy_volume")) or 0) / div
    sell = float(r.get("sellvol", r.get("sell_volume")) or 0) / div
    return buy, sell, buy + sell


def _volume_shape(r: dict[str, Any], now: int,
                  v: dict[str, Any] | None) -> dict[str, Any]:
    buy, sell, total = _row_volumes(r)
    rate = _usd_rate(r)
    ltt = r.get("ltt", r.get("last_trade_ts"))
    return {
        "address": r["address"], "symbol": r["symbol"], "name": r["name"],
        "logo": r["logo"], "deployer": r["deployer"],
        "launch_ts": r["launch_ts"],
        "age_seconds": (now - r["launch_ts"]) if r["launch_ts"] else None,
        "quote_symbol": r["quote_symbol"] or "ETH",
        "quote_decimals": r.get("quote_decimals"),
        "usd_price": rate,
        "volume_quote": total,
        "volume_usd": total * rate if rate else None,
        "buy_volume_quote": buy, "sell_volume_quote": sell,
        "net_quote": buy - sell,
        "buys": int(r.get("buys") or 0), "sells": int(r.get("sells") or 0),
        "trades": int(r.get("buys") or 0) + int(r.get("sells") or 0),
        "buyers": int(r.get("buyers") or 0),
        "last_trade_ts": ltt,
        "last_trade_age": (now - ltt) if ltt else None,
        "price_quote": r["price_quote"], "mcap_usd": r["mcap_usd"],
        "progress_pct": r["progress_pct"],
        "graduated": bool(r["graduated"]),
        "snipe_label": (v or {}).get("label"),
        "snipe_score": (v or {}).get("score"),
    }


@app.get("/api/volume")
def api_volume(
    window: str = "1h",
    sort: str = "volume",
    limit: int = Query(100, ge=1, le=C.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    q: str | None = None,
    quote: str | None = None,
    graduated: bool | None = None,
) -> dict[str, Any]:
    if window not in _WINDOWS:
        raise HTTPException(400, "window must be one of " + ", ".join(_WINDOWS))
    now = int(time.time())
    rows = store.volume_rows(_WINDOWS[window], sort=sort, limit=limit,
                             offset=offset, q=q, quote=quote,
                             graduated=graduated)
    verd, _, _ = _judge(rows)
    tokens = [_volume_shape(r, now, verd.get(r["address"])) for r in rows]
    return {
        "now": now, "window": window, "count": len(tokens),
        "totals": {
            "volume_quote": sum(t["volume_quote"] for t in tokens),
            "volume_usd": sum(t["volume_usd"] or 0 for t in tokens) or None,
            "trades": sum(t["trades"] for t in tokens),
            "tokens": len(tokens),
        },
        "tokens": tokens,
    }


# ------------------------------------------------------------------ snipes
@app.get("/api/snipes")
def api_snipes(
    limit: int = Query(100, ge=1, le=C.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    sort: str = "score",
    only_bots: int = 0,
    max_delta: int | None = None,
    since_minutes: int | None = None,
    q: str | None = None,
    quote: str | None = None,
    labels: str | None = None,
) -> dict[str, Any]:
    since = int(time.time()) - since_minutes * 60 if since_minutes else None
    rows, verd, labels_seen = _scored()
    # Every row filter runs before anything is counted, and the chips are then
    # counted over exactly the set the table is drawn from. That ordering is
    # the whole point: a chip whose number disagrees with the rows clicking it
    # produces is worse than no chip at all. Only the label chips narrow after
    # counting, because a chip has to report its own unfiltered size for the
    # other chips to still make sense next to it.
    if since:
        rows = [r for r in rows if (r["launch_ts"] or 0) >= since]
    if quote:
        rows = [r for r in rows if r["quote_symbol"] == quote]
    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in (r["symbol"] or "").lower()
                or needle in (r["name"] or "").lower()
                or needle in r["address"].lower()
                or needle in (verd[r["address"]]["first_buyer"] or "").lower()]
    if only_bots:
        rows = [r for r in rows if verd[r["address"]]["bot"]]
    if max_delta is not None:
        rows = [r for r in rows if (verd[r["address"]]["delta"] or 0) <= max_delta]
    # A token the walk has not reached has no verdict to show, so it drops out
    # here and is reported by the walk's own `indexed` figure instead.
    rows = [r for r in rows if verd[r["address"]]["label"] in _LISTABLE]

    now = int(time.time())
    stats: dict[str, int] = {k: 0 for k in _LISTABLE}
    for r in rows:
        stats[verd[r["address"]]["label"]] += 1
    stats["indexed"] = labels_seen["indexed"]
    stats["bot_wallets"] = labels_seen["bot_wallets"]

    want = {x.strip() for x in labels.split(",") if x.strip()} if labels else None

    out = []
    for r in rows:
        v = verd[r["address"]]
        if want and v["label"] not in want:
            continue
        row = _volume_shape(r, now, v)
        row["snipe"] = _scale_snipe(v, r.get("quote_decimals"))
        row["first_buyer"] = v["first_buyer"]
        out.append(row)

    if sort == "score":
        out.sort(key=lambda t: (-(t["snipe"]["score"] or 0), t["age_seconds"] or 0))
    elif sort == "delta":
        out.sort(key=lambda t: (t["snipe"]["delta"] if t["snipe"]["delta"]
                                is not None else 1e9))
    elif sort == "volume":
        out.sort(key=lambda t: -(t["volume_quote"] or 0))
    else:
        out.sort(key=lambda t: t["launch_ts"] or 0, reverse=True)

    return {
        "now": now,
        "count": len(out[offset:offset + limit]),
        "total": len(out),
        "stats": stats,
        "tokens": out[offset:offset + limit],
    }


# ------------------------------------------------------------------ snipers
@app.get("/api/snipers")
def api_snipers(
    limit: int = Query(100, ge=1, le=C.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    sort: str = "hits",
    min_hits: int = Query(1, ge=1),
    per_wallet: int = Query(12, ge=0, le=50),
) -> dict[str, Any]:
    """The wallets doing the sniping, ranked by how often they were first in.

    One row per wallet rather than per launch: a wallet that is first into
    forty launches is one bot, and seeing it forty times in a list of tokens
    hides that. The launches it was first into ride along so a row can be
    opened instead of cross-referenced.
    """
    now = int(time.time())
    wallets = store.sniper_list(limit=limit, offset=offset, sort=sort,
                                min_hits=min_hits)
    addrs = [w["address"] for w in wallets]
    # One walk, not two. `per_wallet` is a slice off the front of the same list,
    # not a different question: both calls run the same scan and the same
    # `NOT EXISTS` over every launch the page's wallets were first into, and the
    # only difference is how much of the result each one keeps. Asking for the
    # capped list and then the full one paid for that scan twice on every poll,
    # which is where the tab's "snipers fetch failed: signal timed out" came
    # from - the pair measured 1.69s and 2.26s warm on a hundred wallets, and
    # the frontend gives the whole request 15s.
    every = store.sniper_tokens(addrs, per_wallet=0)
    # The totals below are taken over this full list and not over the dozen
    # rows a table renders: a total over the page that happens to fit would
    # understate exactly the wallets that matter most.
    #
    # `per_wallet=0` means one token per wallet here, which is what the capped
    # call did with it too - it is a request for the page, and the page is the
    # first row.
    cap = per_wallet or 1
    tokens = {a: v[:cap] for a, v in every.items()}
    # Resolved once per request rather than once per row: the table is small,
    # and a row asking for it individually would read the database hundreds of
    # times for an answer that cannot change mid-request.
    rates = store.quote_rates()

    def rate_of(t: dict[str, Any]) -> float | None:
        """The USD rate for a row's pair, or None when the pair is unknown."""
        return rates.get((t["quote_symbol"] or "").strip().lower())
    out = []
    for w in wallets:
        mine = tokens.get(w["address"], [])
        # Both totals cover every launch the wallet was first into, not the
        # page of them a table can render, or the busiest wallets would look
        # like the smallest ones. The per-token figures on the rows below sum
        # to this only when the whole list is expanded.
        spent, total, unknown = 0.0, 0.0, 0
        spent_usd, pnl_usd, no_rate = 0.0, 0.0, 0
        # The rate is looked up per token, like the divisor, because a wallet's
        # launches are not all quoted in the same asset: `spent_quote` adds ETH
        # to USDG to NVDA, which is a total in no unit at all, and the
        # `quote_symbol` further down can only name the first row's. USD is the
        # one denominator they all have, and this is the figure to show.
        # It is the rate as of now applied to a figure built over hours, which
        # is the same approximation mcap_usd already makes.
        for t in every.get(w["address"], []):
            # Per token, because a wallet's launches need not all be in the
            # same quote asset and one divisor for the list would misprice
            # everything that is not the first row's.
            qd = float(10 ** (t["quote_decimals"] or 18))
            rate = rate_of(t)
            spent += (t["quote"] or 0) / qd
            if rate:
                spent_usd += (t["quote"] or 0) / qd * rate
            else:
                no_rate += 1
            pos = _position(t, t)
            if pos["pnl"] is None:
                unknown += 1
            else:
                total += pos["pnl"]
                if rate:
                    pnl_usd += pos["pnl"] * rate
        out.append({
            "address": w["address"],
            "hits": int(w["hits"] or 0),
            "last_ts": w["last_ts"],
            "bot": int(w["hits"] or 0) >= C.BOT_HITS_MIN,
            "spent_quote": spent,
            "pnl_quote": total,
            # The same two figures in the one unit that covers every launch.
            # A launch whose quote asset has no known rate is left out of these
            # and counted in `pnl_no_rate`, so a missing rate can never read as
            # a zero profit.
            "spent_usd": spent_usd,
            "pnl_usd": pnl_usd,
            "pnl_no_rate": no_rate,
            # Some launches could not be valued - a graduated token has no
            # curve left to price against - so the total is a floor, and the
            # count says how many are missing from it.
            "pnl_partial": bool(unknown),
            "pnl_unknown": unknown,
            "quote_symbol": mine[0]["quote_symbol"] if mine else None,
            "tokens": [{
                "address": t["address"], "symbol": t["symbol"],
                "name": t["name"], "logo": t["logo"],
                "launch_ts": t["launch_ts"], "launch_block": t["launch_block"],
                "block": t["block"], "ts": t["ts"],
                "delta": max(0, int(t["block"]) - int(t["launch_block"] or 0)),
                "quote": (t["quote"] or 0) / float(
                    10 ** (t["quote_decimals"] or 18)),
                "quote_symbol": t["quote_symbol"],
                "share": ((t["quote"] or 0) / float(
                    t["graduation_threshold"] or 1) * 100.0)
                if t["graduation_threshold"] else None,
                "bundled": bool(t["tx"] and t["launch_tx"]
                                and t["tx"].lower() == t["launch_tx"].lower()),
                "buy_tx": t["tx"], "launch_tx": t["launch_tx"],
                "quote_usd": rate_of(t),
                "position": _row_position(t, rate_of(t)),
            } for t in mine],
        })
    return {
        "now": now,
        "count": len(out),
        "stats": store.sniper_stats(min_hits),
        "snipers": out,
    }


# ---------------------------------------------------------- one sniper, opened
def _snipe_deployer(dep: dict[str, Any] | None) -> dict[str, Any]:
    """A deployer reduced to what the row and the sort keys need.

    `is_contract` is read off `code` rather than `state`, because a contract is
    a fact about the address and `state` is a fact about the reading. A contract
    has no nonce in the sense a wallet does, and the page prints `contract`
    where the number would go.

    `ext` is keyed by chain_id and is `{}` rather than None when nothing has
    been read on those chains - the same distinction `state` draws one level up,
    so the page can tell "never asked" from "asked and refused" per chain
    without a third shape to test for.
    """
    d = dep or {}
    bal = d.get("balance_wei")
    try:
        bal = int(bal) / 1e18 if bal is not None else None
    except (TypeError, ValueError):
        bal = None
    return {
        "address": d.get("address"),
        "launches": d.get("launches") or 0,
        "first_block": d.get("first_block"),
        "nonce": d.get("nonce"),
        "balance": bal,
        "balance_wei": None if d.get("balance_wei") is None
        else str(d["balance_wei"]),
        "is_contract": (d.get("code") or 0) == 1,
        "state": d.get("state"),
        "block": d.get("block"),
        "fetched_at": d.get("fetched_at"),
        "ext": {int(k): {"nonce": v.get("nonce"), "state": v.get("state")}
                for k, v in (d.get("ext") or {}).items()},
    }


def _snipe_x(handle: str | None, user: dict[str, Any] | None,
             look: dict[str, Any] | None) -> dict[str, Any]:
    """What is known about the handle a token claims, and what that is not.

    Three states and they are not the same answer: `never` means we have not
    asked, `missing` means we asked and there is no such account - a typo in a
    token's description, cached so it is not asked again - and a populated row
    means we asked and got a profile, which is a snapshot from `fetched_at`.
    A handle we never checked must never render as an account with no
    followers.
    """
    if not handle:
        return {"handle": None, "state": "none"}
    if user:
        return {
            "handle": handle, "state": "ok", "user_id": user.get("id"),
            "followers": user.get("followers"), "following": user.get("following"),
            "statuses": user.get("statuses"), "listed": user.get("listed"),
            "verified": bool(user.get("verified")), "blue": bool(user.get("blue")),
            "created_ts": user.get("created_ts"),
            "fetched_at": user.get("fetched_at"),
        }
    if look:
        return {"handle": handle, "state": look.get("state") or "missing",
                "user_id": look.get("user_id"), "error": look.get("error"),
                "checked_at": look.get("checked_at")}
    return {"handle": handle, "state": "never"}


def _chain_state(d: dict[str, Any] | None, now: int) -> str:
    """read | stale | missing, for one cached chain reading.

    A row that failed to read is `missing` and not `read`: the walk should try
    it again, and a failed reading is the one thing that must never be counted
    as coverage. `contract` counts as read - a contract's nonce and balance are
    as known as a wallet's, they are just not the same kind of fact.
    """
    if not d or d.get("state") not in ("ok", "contract"):
        return "missing"
    if (d.get("fetched_at") or 0) < now - C.SW_CHAIN_TTL:
        return "stale"
    return "read"


def _ext_state(per_chain: dict[int, dict[str, Any]] | None, chain_id: int,
               now: int) -> str:
    """read | stale | missing, for one address's reading on one other chain.

    Takes the address's whole `ext` map rather than one row, because the caller
    asks the same question of every configured chain in a loop and the map is
    already in hand - `ext_cached[address][chain_id]` at every call site would
    be three lookups spelled out twice.

    Its own TTL, not `SW_CHAIN_TTL`: a nonce on Ethereum moves at Ethereum's
    pace, which has nothing to do with how fresh this indexer's own block is,
    and the two numbers are free to disagree.

    Its own state, one level down from `_chain_state`, and for the reason the
    table exists: Ethereum answering while Arbitrum refuses is a real outcome,
    and a single state for the address would have to draw both chains the same
    way - turning "we could not reach it" into "there is nothing there".
    """
    d = (per_chain or {}).get(chain_id)
    if not d or d.get("state") != "ok":
        return "missing"
    if (d.get("fetched_at") or 0) < now - C.SW_EXT_TTL:
        return "stale"
    return "read"


def _snipe_chain_summary(addrs: Sequence[str],
                         deps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """How much of the deployer set is actually known, and what that costs.

    The price is printed before the button is pressed, so it is computed here
    from the same arithmetic the walk will spend: two calls per address that
    cannot be batched - the nonce, because a transaction count is a property of
    state rather than the result of a contract call, and the bytecode, because
    that is how a contract deployer is told from a wallet - plus one chunked
    call per MULTICALL_CHUNK for the balances, which can be.

    The rate is a measured one and not `1 / RPC_MIN_SPACING`. The gate spaces
    *starts*; the walk is bounded by its own slot count and by how long the node
    takes to answer, and it does not reach the spacing's ceiling. An ETA three
    times too optimistic is worse than no ETA, so the number here is the one the
    walk actually sustains (see SW_RPC_PER_SEC).

    The other chains are counted as one call each and priced at the same rate.
    They do not run at it - each has its own host and its own slot budget - so
    this overstates the total, which is the direction an ETA should err in. What
    is *not* overstated is the work they represent, and a round of the walk that
    only has external readings left still has to be paid for.

    `want` is the number the button will say it is about to read, and it counts
    an address once even when both its halves are out of date: it is a count of
    addresses, and the walk visits each address once.
    """
    now = int(time.time())
    addrs = [a for a in dict.fromkeys(addrs) if a]
    read = stale = 0
    contracts = 0
    nonces: list[int] = []
    for a in addrs:
        d = deps.get(a) or {}
        how = _chain_state(d, now)
        if how != "missing":
            read += 1
        if how == "stale":
            stale += 1
        if (d.get("code") or 0) == 1:
            contracts += 1
        elif d.get("state") == "ok" and d.get("nonce") is not None:
            nonces.append(int(d["nonce"]))

    # Per chain, because the three disagree and the page prints them side by
    # side: one chain being fully covered says nothing about the next, and a
    # single combined figure would hide the chain that is not answering. Taken
    # off `deps` rather than queried again - it is the same table read once, so
    # the coverage here cannot disagree with the numbers in the rows below it.
    #
    # Keyed by chain_id and not by label, the same way the deployer rows and
    # `deployer_chain_ext_get` are, because the id is what configures the chain
    # and the label is only what it is called on screen. Keying this one by
    # label while the rows were keyed by id is a mismatch that fails silently:
    # the lookup misses, the line is skipped, and a chain that was read for
    # every address prints exactly like a chain that was never configured.
    ext_cov: dict[int, dict[str, Any]] = {}
    for cid, label, _url in C.SW_EXT_CHAINS:
        # `asked` is counted separately from `read` because zero readings is not
        # the same as no questions: a chain that refused every address has a row
        # for each of them and no readings at all, and a page that decided
        # "never asked" from `read == 0` would report a node that is down as a
        # node nobody has tried - which is exactly the confusion the per-chain
        # state exists to prevent, one level up.
        asked = sum(1 for a in addrs
                    if cid in ((deps.get(a) or {}).get("ext") or {}))
        got = sum(1 for a in addrs
                  if _ext_state((deps.get(a) or {}).get("ext"), cid, now)
                  == "read")
        ext_cov[cid] = {"chain_id": cid, "label": label, "asked": asked,
                        "read": got, "missing": len(addrs) - got}

    stale_local = {a for a in addrs
                   if _chain_state(deps.get(a), now) == "stale"}
    missing_local = {a for a in addrs
                     if _chain_state(deps.get(a), now) == "missing"}
    missing = len(missing_local)
    want = len(missing_local | stale_local
               | {a for a in addrs
                  if any(_ext_state((deps.get(a) or {}).get("ext"), cid, now)
                         != "read" for cid, _, _ in C.SW_EXT_CHAINS)})
    per_sec = C.SW_RPC_PER_SEC
    calls = (want * (2 + len(C.SW_EXT_CHAINS))
             + (want + C.MULTICALL_CHUNK - 1) // C.MULTICALL_CHUNK)
    return {
        "addresses": len(addrs),
        "read": read,
        "stale": stale,
        "missing": missing,
        "contracts": contracts,
        "median_nonce": (sorted(nonces)[len(nonces) // 2] if nonces else None),
        "nonce_known": len(nonces),
        "ext": ext_cov,
        "state": "ready" if not want else "idle",
        "calls": calls,
        "per_sec": round(per_sec, 1),
        "eta_sec": int(calls / per_sec) if calls and per_sec else 0,
        "ttl_sec": C.SW_CHAIN_TTL,
    }


def _snipe_x_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """X coverage over the snipes, counted rather than assumed.

    One state per distinct handle, decided by the first row that claims it.
    Counting per row instead would let a handle claimed by three launches be
    looked up three times and make the coverage figure a statement about
    launches rather than about handles.
    """
    state: dict[str, str] = {}
    claimed = 0
    for r in rows:
        h = r.get("handle")
        if not h:
            continue
        claimed += 1
        state.setdefault(h, (r.get("x") or {}).get("state") or "never")
    known = sum(1 for s in state.values() if s == "ok")
    gone = sum(1 for s in state.values() if s in ("missing", "error"))
    followers = [r["followers"] for r in rows if r.get("followers") is not None]
    return {
        "claimed": claimed,
        "handles": len(state),
        "known": known,
        "missing": gone,
        "never": len(state) - known - gone,
        "coverage": (known / len(state)) if state else None,
        "median_followers": (sorted(followers)[len(followers) // 2]
                             if followers else None),
        "with_followers": len(followers),
    }


@app.get("/api/snipers/{address}")
def api_sniper(address: str,
               limit: int = Query(100, ge=1, le=C.MAX_LIMIT),
               offset: int = Query(0, ge=0),
               sort: str = "",
               sort2: str = "",
               include_lost: int = 0) -> dict[str, Any]:
    """One wallet's sniping, opened up: which launches, whose, and how it buys.

    The whole set is read and paged here rather than in SQL. `LIMIT` does not
    push down through the `NOT EXISTS` that decides what a snipe is - a page of
    a hundred costs the same as the lot - so a page would pay for everything and
    show a hundredth of it. Reading once also means `criteria` and the page are
    computed from the same rows, which is the only way the numbers above a table
    can be trusted to describe the table below it.

    Nothing here reaches the chain or X. The per-deployer and per-handle figures
    are read from the cache and reported as `state`, so a page whose cache is
    empty says it knows nothing instead of drawing zeroes.
    """
    row = store.sniper_get(address)
    # The leaderboard key is the one the counter wrote; a hash typed in another
    # case would find no snipes, because every query compares `buyer` exactly.
    canon = (row or {}).get("address") or address
    rows = store.sniper_snipes(canon)
    if not row and not rows:
        raise HTTPException(404, "no such sniper")

    firsts = [r for r in rows if r.get("first")]
    # The firsts are the snipes. The rest are the races he lost: he entered the
    # launch and somebody beat him into it. They are a separate set, shown on
    # request and counted either way, and they are never mixed into a median.
    visible = rows if include_lost else firsts
    if len(visible) > C.SW_DEPLOYER_LIMIT:
        visible = visible[:C.SW_DEPLOYER_LIMIT]

    dep_addrs = list(dict.fromkeys(
        r["deployer"] for r in visible if r.get("deployer")))
    deps = store.deployer_profiles(dep_addrs)

    # Handles and their X rows are resolved over every launch he entered, not
    # just the page or just the firsts: a row's handle does not depend on which
    # filter is on, and one lookup for the union is one query either way.
    all_addrs = [r["address"] for r in rows]
    handles_by_token = store.handles_for_tokens(all_addrs)
    xusers = store.x_users_by_handle(handles_by_token.values())
    xlook = store.x_lookups_get(handles_by_token.values())

    for r in rows:
        h = handles_by_token.get(r["address"])
        r["handle"] = h
        r["x"] = _snipe_x(h, xusers.get(h) if h else None,
                          xlook.get(h) if h else None)
        r["followers"] = r["x"].get("followers")
        r["deployer_info"] = _snipe_deployer(deps.get(r.get("deployer")))
        # The sort keys, flattened onto the row. `sniper_order` reads them by
        # name, and the row is what the browser already has.
        r["d_launches"] = r["deployer_info"]["launches"]
        r["d_nonce"] = (None if r["deployer_info"]["is_contract"]
                        else r["deployer_info"]["nonce"])

    ordered, applied = store.sniper_order(visible, sort, sort2)
    page = ordered[offset:offset + limit]

    # The chain figures describe the snipes even when the lost races are on
    # screen, and the live walk is attached here rather than inside the summary
    # so that function stays a pure reading of the cache.
    ch = _snipe_chain_summary(
        [r["deployer"] for r in firsts if r.get("deployer")], deps)
    ch["run"] = _sw_public(_sw_run(canon))

    rates = store.quote_rates()
    wts = store.wallet_trades_for(canon, [r["address"] for r in page])
    out = []
    for r in page:
        t = dict(r)
        # The position is looked up for the page only. It needs the wallet's
        # own trades row, which is the join `sniper_tokens` pays 1160 ms for and
        # which nothing above this line needs.
        rate = rates.get((r.get("quote_symbol") or "").strip().lower())
        t["quote_usd"] = rate
        t["position"] = _pos_usd(_position(wts.get(r["address"]), r), rate)
        t["bundled"] = bool(r.get("buy_tx") and r.get("launch_tx")
                            and r["buy_tx"].lower() == r["launch_tx"].lower())
        out.append(t)

    return {
        "now": int(time.time()),
        "address": canon,
        "hits": int((row or {}).get("hits") or 0),
        "last_ts": (row or {}).get("last_ts"),
        # False when the counter has never credited this wallet: its launches
        # are all lost races, and `hits` of 0 would be a claim it did not make.
        "counted": bool(row),
        "criteria": store.sniper_criteria(rows, deps),
        "chain": ch,
        "x": _snipe_x_summary(firsts),
        "page": {
            "offset": offset, "limit": limit,
            "total": len(visible),
            "total_firsts": len(firsts),
            "total_all": len(rows),
            "include_lost": bool(include_lost),
            "applied": applied,
            "deployers": len(deps),
            "deployers_known": len(
                [d for d in deps.values() if d.get("state") in ("ok", "contract")]),
        },
        "tokens": out,
    }


# --------------------------------------------------------- the on-chain walk
# One background pass over a profile's deployers. Three readings each, all of
# them properties of state rather than results of a contract call, which is why
# the cost is dominated by the request count and not by the size of the answer.
#
# Runs are keyed by address and not by a run id, because a wallet has at most
# one walk at a time and pressing the button again while one is going is not a
# second job - it is the same job, and the page should be shown the progress it
# already has rather than a duplicate of the work.
#
# The dict is progress, not state: the readings live in `deployer_chain`, which
# outlives the process, and the page reads the table. That is what makes a walk
# interrupted by a restart resumable by pressing the button again - the rows it
# committed are still there and are not read a second time.
_SW_RUNS: dict[str, dict[str, Any]] = {}
_sw_lock = threading.Lock()
SW_RUNS_KEEP = 8
# Deployers committed per batch. Not an RPC batch size - chain.py decides those.
# This is how much work is thrown away if the walk dies mid-flight.
SW_COMMIT_EVERY = 100


def _sw_run(address: str) -> dict[str, Any] | None:
    with _sw_lock:
        return _SW_RUNS.get(address)


def _db_retry(fn, *args, retries=5, **kwargs):
    """Call fn with retries on SQLite busy/locked errors."""
    for i in range(retries):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            low = str(e).lower()
            if ("locked" in low or "busy" in low) and i < retries - 1:
                time.sleep(0.4 * (i + 1))
            else:
                raise


def _sw_public(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """A run as the page sees it, without the thread's control flags.

    The flags are the only keys with a leading underscore, the same rule
    `_x_public` follows, and `state` is what the page acts on: a stop that has
    been asked for but not yet honoured is still `running`, and the page should
    keep polling through that rather than draw a finished bar.
    """
    return None if not run else {k: v for k, v in run.items()
                                 if not k.startswith("_")}


def _sw_deployers(address: str) -> list[str]:
    """The deployers of the launches this wallet was first into.

    Deliberately the snipes and not whatever the table is filtered to. The
    figures above the table are computed over the snipes, so a walk that
    followed the "lost races" checkbox would make the coverage number move when
    a filter moved - and the cache is shared between wallets anyway, so the
    question "what do we know about this deployer" has one answer per address
    and not one per view.
    """
    rows = store.sniper_snipes(address)
    firsts = [r for r in rows if r.get("first")]
    if len(firsts) > C.SW_DEPLOYER_LIMIT:
        firsts = firsts[:C.SW_DEPLOYER_LIMIT]
    return list(dict.fromkeys(r["deployer"] for r in firsts if r.get("deployer")))


def _sw_rows(batch: Sequence[str], nonces: dict[str, int | None],
             codes: dict[str, int | None], bals: dict[str, int | None],
             block: int | None) -> list[dict[str, Any]]:
    """One row per address, from the three readings.

    Kept apart from the walk so the classification can be tested without a node.
    Only the two readings that decide *what the address is* can spoil a row:

      * no bytecode reading - `error`. Without it we cannot tell a contract from
        a wallet, and every other column depends on knowing which it is.
      * bytecode, so - `contract`, with `code` 1. A contract does have a nonce,
        but it counts the contracts it created rather than transactions it sent,
        so the row is complete without one and the page prints `contract` where
        the number would go.
      * no bytecode and no transaction count - `error`. A wallet whose count did
        not come back is a hole, not a reading: `ok` means every claim the row
        makes it can back up, and this one could not. Left `error` rather than
        `ok` so the next walk asks again.
      * otherwise - `ok`, `code` 0, and whatever the nonce was, including 0.
        Zero is a real answer: an account that has never sent anything.

    A failed balance does not spoil the row, and that is deliberate. It is a
    value rather than a classification, so a missing one renders as missing
    instead of as a zero balance, and re-reading an address we already know the
    shape of just to price it again is the expensive half of the walk.
    """
    out = []
    for a in batch:
        nc, size, bal = nonces.get(a), codes.get(a), bals.get(a)
        row: dict[str, Any] = {"address": a, "block": block, "balance_wei": bal}
        if size is None:
            row["state"] = "error"
            row["error"] = "the node did not answer for the bytecode"
        elif size > 0:
            row["state"] = "contract"
            row["code"] = 1
            row["nonce"] = nc
        elif nc is None:
            row["state"] = "error"
            row["error"] = "the node did not answer for the transaction count"
        else:
            row["state"] = "ok"
            row["code"] = 0
            row["nonce"] = nc
        out.append(row)
    return out


def _run_chain_walk(address: str, addrs: list[str]) -> None:
    """Read nonce, bytecode and balance for a list of deployers.

    Committed in batches rather than at the end: a walk that dies on its
    thousandth address keeps the nine hundred it already paid for, and a stop is
    honoured between batches rather than mid-request, so the readings that were
    bought are never thrown away.
    """
    run = _sw_run(address)
    if not run:
        return
    try:
        run["block"] = chain.block_number()
    except Exception as e:
        # Not fatal: the readings are still worth having, they just cannot say
        # which block they are true at, and the row prints that honestly.
        run["block"] = None
        log.debug("chain walk %s: no block number: %s", address, str(e)[:80])
    state, error = "ok", ""
    try:
        for i in range(0, len(addrs), SW_COMMIT_EVERY):
            if run.get("_stop"):
                state = "stopped"
                break
            batch = addrs[i:i + SW_COMMIT_EVERY]
            # The other chains start before the local read rather than after it.
            # Their calls go to different hosts, so running them in sequence
            # buys nothing but a batch that costs the sum of two waits instead
            # of the longer of them - and the external reads are two of the
            # four calls each address costs, which is not a rounding error.
            #
            # A refusal is written as `error` and not skipped: no row at all
            # means "never asked" and the page draws it that way, so leaving a
            # failed read unwritten would turn a chain we could not reach into a
            # chain we have not tried - and the next press would ask again
            # forever without ever saying why.
            ext_rows: list[dict[str, Any]] = []
            ext_lock = threading.Lock()

            def _ext_pass(chain_id: int, _batch: list[str] = batch) -> None:
                # Bound as a default because the loop rebinds `batch` on the
                # next iteration, and a thread still running must not read the
                # batch after the one it was started for.
                try:
                    got = chain.transaction_counts_ext(chain_id, _batch)
                except Exception as e:
                    # Caught here and not left to the try around the loop: this
                    # is a thread, where an exception goes to stderr and takes
                    # the reading with it, and the loop's handler would never
                    # see it. The addresses come back as `error` rows below.
                    log.debug("chain walk %s: ext %s failed: %s",
                              address, chain_id, str(e)[:80])
                    got = {}
                stamp = int(time.time())
                filled = [{
                    "address": a, "chain_id": chain_id, "nonce": got.get(a),
                    "state": "ok" if got.get(a) is not None else "error",
                    "block": None, "fetched_at": stamp,
                    "error": "" if got.get(a) is not None
                    else "the node did not answer",
                } for a in _batch]
                with ext_lock:
                    ext_rows.extend(filled)

            ext_threads = [threading.Thread(target=_ext_pass, args=(cid,),
                                            daemon=True)
                           for cid, _, _ in C.SW_EXT_CHAINS]
            for t in ext_threads:
                t.start()
            rows = _sw_rows(batch,
                            chain.transaction_counts(batch),
                            chain.code_sizes(batch),
                            chain.balances(batch),
                            run.get("block"))
            _db_retry(store.deployer_chain_put, rows)
            for t in ext_threads:
                t.join()
            if ext_rows:
                _db_retry(store.deployer_chain_ext_put, ext_rows)
            with _sw_lock:
                run["done"] += len(rows)
                run["read"] += sum(1 for r in rows if r["state"] != "error")
                run["contracts"] += sum(1 for r in rows
                                        if r["state"] == "contract")
                run["errors"] += sum(1 for r in rows if r["state"] == "error")
                run["ext_errors"] += sum(1 for r in ext_rows
                                         if r["state"] == "error")
                run["updated_at"] = int(time.time())
    except Exception as e:  # a bug of ours, not the node's - worth a traceback
        state, error = "error", "%s: %s" % (type(e).__name__, str(e)[:200])
        log.exception("chain walk %s failed", address)
    with _sw_lock:
        run["state"] = state
        run["error"] = error
        run["finished_at"] = int(time.time())
        run["updated_at"] = run["finished_at"]
        done = dict(run)
    log.info("chain walk %s: %d/%d read, %d contracts, %d errors, %s in %ds",
             address, done["read"], done["total"], done["contracts"],
             done["errors"], state, done["finished_at"] - done["started_at"])


@app.post("/api/snipers/{address}/chain")
def api_sniper_chain_start(address: str) -> dict[str, Any]:
    """Start reading the chain for this wallet's deployers, or join the run.

    Joining rather than refusing: two presses are one walk, and the second one
    gets the progress the first already has. `started` says which of the two
    happened, so the page can tell "I started it" from "it was already going".
    """
    row = store.sniper_get(address)
    canon = (row or {}).get("address") or address
    if not row and not store.sniper_snipes(canon):
        raise HTTPException(404, "no such sniper")

    with _sw_lock:
        live = _SW_RUNS.get(canon)
        if live and live["state"] == "running":
            return {"started": False, "run": _sw_public(live)}

    addrs = _sw_deployers(canon)
    now = int(time.time())
    cached = store.deployer_chain_get(addrs)
    ext_cached = store.deployer_chain_ext_get(addrs)
    # An address is wanted when either half of its picture is out of date, and
    # not only when the local half is. The external chains were added after
    # `deployer_chain` was already full, so an address whose nonce was read
    # months ago has no external rows at all - filtering on the local state
    # alone would call it done and its Ethereum nonce would stay unknown
    # forever, with the button reporting nothing left to read.
    want = [a for a in addrs
            if _chain_state(cached.get(a), now) != "read"
            or any(_ext_state(ext_cached.get(a), cid, now) != "read"
                   for cid, _, _ in C.SW_EXT_CHAINS)]
    # Two reads per address on this chain - the nonce and the bytecode - plus
    # one per configured external chain, plus a share of the multicall used for
    # the balances. The external reads run on their own hosts at their own
    # concurrency, so dividing them by this chain's rate overstates the total a
    # little; an ETA that is too optimistic is worse than one that is too long,
    # which is the same reason `SW_RPC_PER_SEC` is a measured floor.
    calls = (len(want) * (2 + len(C.SW_EXT_CHAINS))
             + (len(want) + C.MULTICALL_CHUNK - 1) // C.MULTICALL_CHUNK)

    run = {
        "address": canon, "state": "running", "error": "",
        "total": len(want), "done": 0, "read": 0, "contracts": 0, "errors": 0,
        "ext_errors": 0, "block": None, "calls": calls,
        "per_sec": round(C.SW_RPC_PER_SEC, 1),
        "eta_sec": int(calls / C.SW_RPC_PER_SEC) if calls else 0,
        "started_at": now, "updated_at": now, "finished_at": None,
        "_stop": False,
    }
    with _sw_lock:
        _SW_RUNS[canon] = run
        if len(_SW_RUNS) > SW_RUNS_KEEP:
            idle = [k for k, v in _SW_RUNS.items() if v["state"] != "running"]
            for k in idle[:max(0, len(_SW_RUNS) - SW_RUNS_KEEP)]:
                _SW_RUNS.pop(k, None)

    if want:
        threading.Thread(target=_run_chain_walk, args=(canon, want),
                         name="sw-chain", daemon=True).start()
    else:
        with _sw_lock:
            run["state"] = "ok"
            run["finished_at"] = now
    return {"started": True, "run": _sw_public(run)}


@app.get("/api/snipers/{address}/chain")
def api_sniper_chain(address: str) -> dict[str, Any]:
    """The live walk for this wallet, or null. Cheap: it holds no lock on the
    database and reads no cache, so the page can poll it while the walk runs."""
    row = store.sniper_get(address)
    canon = (row or {}).get("address") or address
    return {"now": int(time.time()), "address": canon,
            "run": _sw_public(_sw_run(canon))}


@app.post("/api/snipers/{address}/chain/stop")
def api_sniper_chain_stop(address: str) -> dict[str, Any]:
    """Ask the walk to stop. It stops between batches, so the answer is what is
    still running and not what has already finished."""
    row = store.sniper_get(address)
    canon = (row or {}).get("address") or address
    with _sw_lock:
        run = _SW_RUNS.get(canon)
        if not run:
            raise HTTPException(404, "no walk for this wallet")
        run["_stop"] = True
        out = _sw_public(run)
    return {"stopped": True, "run": out}


# ---------------------------------------------------------- the x profiles walk
# One background pass over the handles a sniper's tokens claim. Each profile
# is one GraphQL request against X, and the client has no pacing of its own, so
# a hundred of them in a second is a guaranteed 429. The walk spaces them out
# and commits each answer the moment it lands.
#
# The run lives in `_X_RUNS` alongside the followings walks, with `kind:
# "profiles"` to tell them apart. The page polls `GET /api/x/jobs/{run_id}`
# for progress, the same way the followings page does.
#
# A handle that does not exist is written as `state="missing"` and never asked
# again. That is the same rule `x_lookups_put` follows for a single lookup,
# and without it a handful of typos in token descriptions would be a permanent
# tax on the account's rate limit.
def _run_x_profiles(run_id: str, address: str, handles: list[str],
                    max_count: int) -> None:
    run = _x_run(run_id)
    if not run:
        return
    state, error = "ok", ""
    try:
        pace = max(0.0, C.SW_X_PACE)
        seen = 0

        def on_result(h: str, prof: dict[str, Any] | None,
                      err: Exception | None) -> None:
            nonlocal seen
            seen += 1
            with _x_lock:
                run["walked"] = seen
                run["updated_at"] = int(time.time())
            if err:
                # Network error - do not mark the handle missing, because it
                # might still exist and a retry on a fresh session would work.
                return

            def _write():
                if prof:
                    store.x_user_put(prof)
                look_state = "ok" if prof else "missing"
                look_uid = prof.get("id") if prof else None
                store.x_lookups_put([{"handle": h, "state": look_state,
                                      "user_id": look_uid}])
            _db_retry(_write)

        # The handles already known from a previous walk are skipped, and the
        # slice is taken AFTER the skip rather than before it. Taking the first
        # `max_count` and then dropping the known ones would spend the whole
        # budget re-reading the same head of the list on every press: once that
        # head is known, further presses would do nothing at all and the tail
        # would never be reached. A handle in `x_lookups` as "ok" or "missing"
        # is a fact we already have; one in "error" or absent is a hole to fill.
        known = store.x_lookups_get(handles)
        todo = [h for h in handles
                if not known.get(h) or known[h]["state"] not in ("ok", "missing")]
        todo = todo[:max_count]
        with _x_lock:
            run["total"] = len(todo)

        try:
            xsource.profiles(todo, pace=pace, on_result=on_result,
                             should_stop=lambda: bool(run.get("_stop")))
        except xsource.XUnavailable as e:
            # The walk stopped because X is down, not because every handle was
            # tried. The handles it did answer for are already committed.
            state, error = "error", str(e)
        else:
            # `profiles` returns early when the stop flag goes up, and a run
            # that was asked to stop and did is `stopped`, not `ok`: the page
            # prints different words for the two, and a partial walk that
            # claimed to be finished would be a lie about the coverage.
            if run.get("_stop"):
                state, error = "stopped", "stopped by hand"
    except Exception as e:
        state, error = "error", "%s: %s" % (type(e).__name__, str(e)[:200])
        log.exception("x profiles walk %s failed", run_id)
    with _x_lock:
        run["state"] = state
        run["error"] = error
        run["finished_at"] = int(time.time())
        run["updated_at"] = run["finished_at"]
    log.info("x profiles %s: %d handles, %s in %ds",
             run_id, seen, state,
             run["finished_at"] - run["started_at"])


@app.post("/api/snipers/{address}/x")
def api_sniper_x_start(address: str) -> dict[str, Any]:
    """Start walking the X profiles for the handles this wallet's tokens claim.

    Returns the run so the page can show the price before the user commits.
    A second press while one is going returns the same run - it is one walk,
    and the handles it has already covered are not asked a second time.
    """
    row = store.sniper_get(address)
    canon = (row or {}).get("address") or address
    if not row and not store.sniper_snipes(canon):
        raise HTTPException(404, "no such sniper")

    handles = store.handles_for_tokens(
        [r["address"] for r in store.sniper_snipes(canon) if r.get("first")])
    unique = list(dict.fromkeys(h for h in handles.values() if h))

    run_key = "sw_x_" + canon
    with _x_lock:
        live = [r for r in _X_RUNS.values()
                if r.get("kind") == "profiles" and r.get("address") == canon
                and r["state"] == "running"]
        if live:
            return {"started": False, "run": _x_public(live[0])}

        run_id = uuid.uuid4().hex[:12]
        run = {
            "id": run_id, "kind": "profiles", "address": canon,
            "state": "running", "walked": 0, "total": len(unique),
            "error": "", "started_at": int(time.time()),
            "updated_at": int(time.time()), "finished_at": None,
            "_stop": False,
        }
        _X_RUNS[run_id] = run

    if unique:
        threading.Thread(target=_run_x_profiles,
                         args=(run_id, canon, unique, C.SW_X_MAX),
                         name="sw-x-profiles", daemon=True).start()
    else:
        with _x_lock:
            run["state"] = "ok"
            run["finished_at"] = int(time.time())
    return {"started": True, "run": _x_public(run)}


@app.get("/api/x/jobs/{run_id}")
def api_x_jobs(run_id: str) -> dict[str, Any]:
    """Progress of any X walk - followings or profiles.

    The page polls this. It is cheap: it holds no database lock and reads no
    cache, so the walk and the poll do not contend.
    """
    run = _x_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return {"now": int(time.time()), "run": _x_public(run)}


@app.post("/api/x/jobs/{run_id}/stop")
def api_x_jobs_stop(run_id: str) -> dict[str, Any]:
    """Ask an X walk to stop. It stops between profiles, so the answer is what
    is still running and not what has already finished."""
    with _x_lock:
        run = _X_RUNS.get(run_id)
        if not run:
            raise HTTPException(404, "no such run")
        run["_stop"] = True
        out = _x_public(run)
    return {"stopped": True, "run": out}


# ------------------------------------------------------------------ copy plans
_COPY_DEFAULTS = {
    "name": "", "symbol": "", "description": "", "logo": "", "twitter": "",
    "telegram": "", "discord": "", "website": "", "farcaster": "",
    "creator_fee_recipient": C.NATIVE_QUOTE, "creator_tax_bps": 200,
    "buyback_enabled": False, "launch_config_id": C.LAUNCH_CONFIG_ID,
    "pair_token": C.NATIVE_QUOTE, "initial_buy_quote": 0.0, "salt": "",
    # Which entry point carries the launch. The factory's call takes the fee as
    # its whole value and reverts on anything more, so the router is the only
    # way to be a buyer in your own launch - and with a buy of 0 the router does
    # nothing the factory does not, so the factory stays the default.
    "entry": "factory", "buyer": "", "slippage_bps": C.DEFAULT_SLIPPAGE_BPS,
}


def _prefill(address: str) -> dict[str, Any]:
    """Everything a copy form starts from, taken off the source token."""
    rows = store.list_tokens(limit=1, q=address)
    if not rows:
        raise HTTPException(404, "unknown token")
    r = rows[0]
    out = dict(_COPY_DEFAULTS)
    for f in ("name", "symbol", "description", "logo", "twitter", "telegram",
              "discord", "website", "farcaster"):
        out[f] = r[f] or ""
    # The source's fee recipient is the source's deployer, which is rarely who
    # is doing the copying, so leave it pointing at the source and let the UI
    # substitute the connected wallet.
    out["creator_fee_recipient"] = r["deployer"] or C.NATIVE_QUOTE
    out["pair_token"] = r["pair_token"] or C.NATIVE_QUOTE
    return out


def _clean_fields(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(_COPY_DEFAULTS)
    for k in _COPY_DEFAULTS:
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    out["creator_tax_bps"] = int(out.get("creator_tax_bps") or 0)
    out["launch_config_id"] = int(out.get("launch_config_id") or 0)
    out["slippage_bps"] = int(out.get("slippage_bps") or 0)
    try:
        out["initial_buy_quote"] = float(out.get("initial_buy_quote") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "initial_buy_quote must be a number")
    out["buyback_enabled"] = bool(out.get("buyback_enabled"))
    return out


@app.get("/api/copy")
def api_copy_list() -> dict[str, Any]:
    return {"plans": store.copy_list()}


@app.get("/api/copy/{plan_id}")
def api_copy_get(plan_id: int) -> dict[str, Any]:
    plan = store.copy_get(plan_id)
    if not plan:
        raise HTTPException(404, "no such plan")
    return {"plan": plan}


@app.post("/api/copy")
def api_copy_create(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    fields = _clean_fields(body.get("fields") or {})
    src = body.get("source_address")
    if src:
        fields = {**_prefill(src), **{k: v for k, v in
                                      (body.get("fields") or {}).items()
                                      if v not in (None, "")}}
    plan_id = store.copy_save(None, fields, body.get("extra") or [],
                             body.get("notes") or "", src,
                             (store.list_tokens(limit=1, q=src)[0]["symbol"]
                              if src and store.list_tokens(limit=1, q=src)
                              else None))
    return {"id": plan_id, "plan": store.copy_get(plan_id)}


@app.put("/api/copy/{plan_id}")
def api_copy_update(plan_id: int,
                    body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    plan = store.copy_get(plan_id)
    if not plan:
        raise HTTPException(404, "no such plan")
    fields = _clean_fields({**plan["fields"], **(body.get("fields") or {})})
    store.copy_save(plan_id, fields,
                    body.get("extra", plan["extra"]),
                    body.get("notes", plan["notes"]),
                    plan["source_address"], plan["source_symbol"])
    if body.get("status"):
        store.copy_status(plan_id, body["status"], body.get("tx_hash"),
                          body.get("error"))
    return {"plan": store.copy_get(plan_id)}


@app.delete("/api/copy/{plan_id}")
def api_copy_delete(plan_id: int) -> dict[str, Any]:
    if not store.copy_delete(plan_id):
        raise HTTPException(404, "no such plan")
    return {"ok": True}


@app.post("/api/copy/{plan_id}/status")
def api_copy_status(plan_id: int,
                    body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if not store.copy_get(plan_id):
        raise HTTPException(404, "no such plan")
    store.copy_status(plan_id, body.get("status") or "draft",
                      body.get("tx_hash"), body.get("error"))
    return {"plan": store.copy_get(plan_id)}


# ------------------------------------------------------------------- uploads
# Where a logo goes. The launch tuple carries the logo as a string, so an
# attached file has to live somewhere the world can fetch it before the token
# can point at it, and this app cannot be that place: it listens on 127.0.0.1.
# catbox is the host real launches on this chain already use
# (files.catbox.moe shows up in the indexed logos), needs no account and no key,
# and was confirmed reachable from here before any of this was written.
#
# The trade is explicit: the file leaves this machine and lands on a public
# host under a URL nobody can delete. That is what the tab tells the user
# before it uploads, and it is why the upload is a separate button rather than
# something that happens when the file is picked.
CATBOX_API = "https://catbox.moe/user/api.php"
UPLOAD_MAX_BYTES = 8 * 1024 * 1024
UPLOAD_TYPES = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp",
                "BMP": "bmp"}



def _pin_image(blob: bytes, ext: str, name: str) -> str:
    """Pin one image and return the uri that names it.

    The cid, not a gateway url. A gateway url names one server, and the
    server behind a logo uri cannot be changed once the token is launched -
    a cid names the file itself and every gateway can serve it, so the logo
    outlives whichever one was convenient on the day.
    """
    sess = requests.Session()
    # The machine-wide proxy intercepts even loopback, and this is an
    # outbound call to a host that does not need it.
    sess.trust_env = False
    r = sess.post(C.PINATA_API,
                  headers={"Authorization": "Bearer " + C.PINATA_JWT},
                  files={"file": ("logo." + ext, blob, "image/" + ext)},
                  data={"network": "public", "name": name[:60]},
                  timeout=90)
    if r.status_code != 200:
        raise RuntimeError("pinata http %d: %s"
                           % (r.status_code, (r.text or "")[:120]))
    cid = ((r.json() or {}).get("data") or {}).get("cid") or ""
    if not cid:
        raise RuntimeError("pinata returned no cid: %s" % (r.text or "")[:120])
    return C.IPFS_URI_PREFIX + cid


@app.post("/api/upload")
def api_upload(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Pin an image and return the uri that names it."""
    raw = str(body.get("data") or "")
    if not raw:
        raise HTTPException(400, "no image data")
    # A data url is what a FileReader hands back, and it is what the browser
    # sends. Everything before the comma is the declaration, which is not
    # trusted - the format is decided below by opening the bytes.
    if "," in raw and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "the image data is not valid base64")
    if not blob:
        raise HTTPException(400, "the image is empty")
    if len(blob) > UPLOAD_MAX_BYTES:
        raise HTTPException(413, "the image is %.1f MB; the limit is %d MB"
                            % (len(blob) / 1e6, UPLOAD_MAX_BYTES // 1024 // 1024))

    # Opening it is the check. A file that claims to be a png in its name and
    # its data url but is not one fails here rather than on the host.
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(blob))
        im.verify()
        im = Image.open(io.BytesIO(blob))
        fmt = (im.format or "").upper()
        size = im.size
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, "that is not an image this can read: %s"
                            % str(e)[:100])
    if fmt not in UPLOAD_TYPES:
        raise HTTPException(400, "unsupported image format %s; send %s"
                            % (fmt or "?", ", ".join(sorted(UPLOAD_TYPES))))

    # Re-encoded rather than passed through, so what gets published is pixels
    # and nothing else: a phone photo carries where it was taken, and a public
    # host is the last place that should end up. Animated formats are the one
    # exception, because flattening a gif to png would throw the animation away.
    if fmt == "GIF":
        out, ext = blob, "gif"
    else:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(blob))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            out, ext = buf.getvalue(), "png"
        except Exception as e:
            raise HTTPException(400, "could not re-encode the image: %s"
                                % str(e)[:100])

    # Pinned first. The fallback is not a preference: without a key, or with
    # one that has run out, an anonymous host is the difference between a
    # logo that lasts as long as a gateway does and no logo at all.
    where, note = "ipfs", ""
    if C.PINATA_JWT:
        try:
            url = _pin_image(out, ext, body.get("name") or "pons logo")
            _keep_pin(url, out, ext)
        except Exception as e:
            log.warning("pin failed, falling back to the anonymous host: %s",
                        str(e)[:200])
            where, note = "host", " (pin failed: %s)" % str(e)[:120]
    else:
        where, note = "host", " (no pinning key)"

    if where == "host":
        try:
            sess = requests.Session()
            sess.trust_env = False
            r = sess.post(CATBOX_API, data={"reqtype": "fileupload"},
                          files={"fileToUpload": ("logo." + ext, out,
                                                  "image/" + ext)},
                          timeout=60)
        except Exception as e:
            raise HTTPException(502, "could not reach the image host: %s"
                                % str(e)[:140])
        url = (r.text or "").strip()
        if r.status_code != 200 or not url.startswith("http"):
            raise HTTPException(502, "the image host refused it (http %d): %s"
                                % (r.status_code, url[:140]))

    return {"url": url, "where": where, "note": note, "bytes": len(out),
            "format": ext, "width": size[0], "height": size[1],
            "original_bytes": len(blob)}


# ------------------------------------------------------------------ launching
@app.get("/api/launch/config")
def api_launch_config(config_id: int = C.LAUNCH_CONFIG_ID) -> dict[str, Any]:
    try:
        cfg = chain.launch_config(config_id)
    except Exception as e:
        raise HTTPException(502, "factory read failed: %s" % str(e)[:120])
    fee = chain.launch_fee()
    cfg.update({
        "config_id": config_id,
        "launch_fee": fee / 1e18,
        "launch_fee_wei": str(fee),
        "native_quote": C.NATIVE_QUOTE,
        "launch_enabled": chain.launch_enabled(),
    })
    return cfg


@app.get("/api/launch/preview")
def api_launch_preview(config_id: int = C.LAUNCH_CONFIG_ID,
                       pair_token: str = C.NATIVE_QUOTE) -> dict[str, Any]:
    try:
        expected = chain.preview_economics(config_id, pair_token)
    except Exception as e:
        raise HTTPException(502, "preview failed: %s" % str(e)[:120])
    return {
        "config_id": config_id,
        "pair_token": chain.w3.to_checksum_address(pair_token),
        "expected_economics": expected,
        "launch_fee_wei": str(chain.launch_fee()),
    }


@app.post("/api/launch/calldata")
def api_launch_calldata(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Assemble an unsigned launch transaction for the browser wallet.

    This endpoint builds calldata and nothing else: no key is held here, and
    the value it returns is what the wallet will be asked to send.
    """
    raw = body.get("fields") or body
    fields = _clean_fields(raw)
    try:
        out = chain.launch_calldata(fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, "could not build the transaction: %s"
                            % str(e)[:140])

    warnings: list[str] = []
    sym = (fields.get("symbol") or "").strip()
    if sym and store.known_symbols(sym):
        warnings.append("a token with the symbol %s already launched on this "
                        "chain" % sym)
    if not (fields.get("name") or "").strip():
        warnings.append("the name is empty")
    if not sym:
        warnings.append("the symbol is empty")
    try:
        cfg = chain.launch_config(out["config_id"])
        if not cfg.get("enabled"):
            warnings.append("this launch config is disabled on the factory")
    except Exception:
        warnings.append("could not re-read the launch config to confirm it")
    if out["creator_fee_recipient"] == C.NATIVE_QUOTE:
        warnings.append("no creator fee recipient set, so fees would be burnt")

    value = out["value_wei"]
    fee = out["launch_fee_wei"]
    if out["entry"] == "router" and out["min_tokens_out"] == 0:
        warnings.append("the buy has no floor under it, so it fills at whatever "
                        "the pool gives; a slippage of 0 means no protection")

    return {
        "to": out["to"], "data": out["data"], "value": str(value),
        "value_eth": value / 1e18,
        "launch_fee_wei": str(fee), "launch_fee_eth": fee / 1e18,
        "initial_buy_eth": out["buy_wei"] / 1e18,
        "entry": out["entry"], "buyer": out["buyer"],
        # Strings, not numbers. Both run to 1e27 and JSON has no integer past
        # 2^53, so a browser reading them as numbers would keep the magnitude
        # and lose the low digits - which on the floor under a real buy is the
        # one digit that decides whether the transaction lands.
        "expected_tokens": str(out["expected_tokens"]),
        "min_tokens_out": str(out["min_tokens_out"]),
        "slippage_bps": out["slippage_bps"],
        "config_id": out["config_id"], "pair_token": out["pair_token"],
        "salt": out["salt"], "expected_economics": out["expected_economics"],
        "creator_fee_recipient": out["creator_fee_recipient"],
        "warnings": warnings,
    }


# ------------------------------------------------ launching from a stored key
# The second place in this process where a key becomes a signature, and the
# first one a page can reach: `POST /api/bridge/sweep` needs a plan and a
# destination somebody typed on the page, while this one signs the raw bytes it
# is handed. That difference is the whole reason for the whitelist below - an
# endpoint that signs arbitrary calldata with a stored key is not a launch
# button, it is a wallet, and it is reachable from every page on this machine.
#
# It is deliberately not a builder. `chain.launch_calldata` draws a fresh salt
# on every call and the token address is a function of that salt, so assembling
# the transaction again here would deploy a different token at a different
# address than the one the confirmation panel printed - a substitution between
# what was shown and what is signed, which is precisely the thing no amount of
# re-checking inside a rebuild could catch.
_SEND_LOCK = threading.Lock()
_SEND_NONCE: dict[str, int] = {}


def _launch_selector(to: str) -> str:
    """The one selector `to` may be called with, or "" for neither.

    Address and selector are read as a pair rather than as two lists that each
    have to allow the call, because the combination that matters is the wrong
    one: the router with the factory's selector is not a near miss, it is a call
    nobody means to make, and two independent checks would both wave it through.
    """
    if to.lower() == C.FACTORY.lower():
        return chain.LAUNCH_SEL
    if to.lower() == C.ROUTER.lower():
        return chain.LAUNCH_AND_BUY_SEL
    return ""


def _send_chain(frm: str, to: str, value: int, data: str) -> dict[str, Any]:
    """What the node has to say before this launch can be signed.

    A block, for the fee and for the ceiling it has to fit under; the estimate,
    which is the cost of the transaction that would actually be mined and not a
    number kept anywhere; and the pending nonce. All of it goes through the same
    gate and the same semaphore as every other read in the app.

    The estimate is never replaced by a guess. A launch short of gas burns its
    fee on a transaction that reverts, so a node that will not estimate is
    answered with a refusal rather than with a number.
    """
    w3 = chain.w3
    latest = chain._rpc(w3.eth.get_block, "latest")
    priority = int(chain._rpc(lambda: w3.eth.max_priority_fee) or 0)
    try:
        estimate = int(chain._rpc(w3.eth.estimate_gas, {
            "from": frm, "to": to, "value": value, "data": data}))
    except Exception as e:
        raise HTTPException(502, "the node would not estimate this launch: %s"
                            % str(e)[:140])
    gas = estimate * C.GAS_HEADROOM_BPS // 10_000
    limit = int(latest.get("gasLimit") or 0)
    return {
        "base": int(latest.get("baseFeePerGas") or 0),
        "priority": priority,
        # The headroom answers the curve moving between the block this was
        # estimated against and the block it lands in. The block's own limit is
        # the ceiling on it: past that a transaction is not generous, it is
        # invalid.
        "gas": min(gas, limit) if limit else gas,
        "pending": int(chain._rpc(w3.eth.get_transaction_count, frm, "pending")),
    }


@app.post("/api/launch/send")
def api_launch_send(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Sign and broadcast a launch with a stored key.

    The bytes are taken exactly as they arrive. Everything decided here is
    whether they may be signed at all and at what fee; the guards are ordered so
    that the cheapest refusal comes first and the key is the last thing opened.
    """
    frm = _addr(body.get("from"), "from")
    _need_vault()
    if not store.keywallet_get(frm):
        raise HTTPException(404, "that wallet is not in the stored list")

    to = _addr(body.get("to"), "to")
    sel = _launch_selector(to)
    if not sel:
        raise HTTPException(400, "this endpoint signs launches only, and %s is "
                            "not the factory or the router" % to[:10])
    data = str(body.get("data") or "")
    if not data.startswith("0x") or len(data) < 10 or len(data) % 2:
        raise HTTPException(400, "data is not hex calldata")
    try:
        int(data[2:], 16)
    except ValueError:
        raise HTTPException(400, "data is not hex calldata")
    if data[:10].lower() != sel.lower():
        raise HTTPException(400, "%s with %s is not a call this wallet will "
                            "sign" % (to[:10], data[:10]))

    # The same ceiling the form already refuses to build past, plus a second
    # launch fee: the fee that was current when the page built this is not
    # necessarily the fee now, and a page that built at the old one must not be
    # refused over a difference it could not have known about. One fee would not
    # be headroom at all - the page already spent that one.
    try:
        value = int(str(body.get("value") or "0"))
    except ValueError:
        raise HTTPException(400, "value is not a whole number of wei")
    ceiling = 2 * chain.launch_fee() + int(C.MAX_INITIAL_BUY_QUOTE * 1e18)
    if value < 0 or value > ceiling:
        raise HTTPException(400, "value is above what a launch can cost "
                            "(%d wei)" % ceiling)

    # The balance is deliberately not read. An overspend is refused by the node,
    # and its refusal says more about why than a second opinion here could -
    # which is exactly how the wallet path behaves today. Another read through
    # the indexer's gate, for a message the node already gives, is not worth the
    # slot in the middle of a send.
    secret = _key_io(store.keywallet_secret, frm)
    if not secret:
        raise HTTPException(404, "that wallet is not in the stored list")

    info = _send_chain(frm, to, value, data)
    tx = {
        "chain_id": C.CHAIN_ID, "from": frm, "to": to, "value": value,
        "data": data, "gas": info["gas"],
        "max_priority_fee_per_gas": info["priority"],
        # Over the fee that is the floor for a transaction to be includable at
        # all - base plus priority, as relay's own cap is over relay's quote.
        # The margin is what lets it still be includable when the base fee has
        # moved by the time it is mined.
        "fee_cap": bridge.fee_cap(info["base"] + info["priority"]),
    }

    # Two presses inside one block would read the same pending nonce and the
    # second transaction would replace the first, which for a launch means
    # paying the fee twice for one token. The lock makes the read, the signature
    # and the broadcast one step; the map makes the second press take the next
    # number instead of the same one.
    with _SEND_LOCK:
        nonce = info["pending"]
        remembered = _SEND_NONCE.get(frm.lower())
        if remembered is not None and nonce <= remembered:
            nonce = remembered + 1
        _SEND_NONCE[frm.lower()] = nonce
        tx["nonce"] = nonce
        try:
            tx_hash = bridge.sign_tx(secret, tx)
        except Exception as e:
            # A nonce that was never spent must not be remembered: every later
            # press from this wallet would skip a number and then wait for a
            # transaction that does not exist.
            _SEND_NONCE.pop(frm.lower(), None)
            if isinstance(e, bridge.RelayError):
                raise HTTPException(502, str(e))
            raise
    # Addresses and the hash. Never the key, never the raw transaction, and not
    # the calldata either - README.md says what may not be written down.
    log.info("launch signed: %s -> %s tx %s", frm, to, tx_hash)
    return {"tx_hash": tx_hash, "nonce": nonce, "gas": info["gas"],
            "from": frm, "to": to, "value_wei": str(value)}


# ------------------------------------------------------------------ wallet
@app.get("/api/wallet/{address}")
def api_wallet(address: str) -> dict[str, Any]:
    try:
        addr = chain.w3.to_checksum_address(address)
    except Exception:
        raise HTTPException(400, "not an address")

    balance = chain.balance(addr)
    eth_usd = _f(store.kv_get("eth_usd"))
    launched = store.wallet_launched(addr)
    early = store.wallet_early_buys(addr)
    back = _f(store.kv_get("trades_back_block"))
    hits = store.sniper_hits([addr])

    launched_out = []
    for r in launched:
        indexed = back is None or float(r["launch_block"]) >= back
        v = snipe.verdict(r, r, None, hits.get(addr, 0), None, indexed=indexed)
        buy, sell, total = _row_volumes(r)
        rate = _usd_rate(r)
        launched_out.append({
            "address": r["address"], "symbol": r["symbol"], "name": r["name"],
            "logo": r["logo"], "launch_ts": r["launch_ts"],
            "age_seconds": (int(time.time()) - r["launch_ts"])
            if r["launch_ts"] else None,
            "mcap_usd": r["mcap_usd"], "progress_pct": r["progress_pct"],
            "graduated": bool(r["graduated"]), "price_quote": r["price_quote"],
            "quote_symbol": r["quote_symbol"] or "ETH",
            "volume_quote": total,
            "volume_usd": total * rate if rate else None,
            "snipe_label": v["label"], "snipe_score": v["score"],
        })

    # An early buy is judged with the same rules as the Snipers tab, using the
    # buy itself as the "first buy" so the label means the same thing.
    early_out = []
    for r in early:
        v = snipe.verdict(
            {"launch_block": r["launch_block"], "deployer": r["deployer"],
             "launch_tx": r["launch_tx"],
             "graduation_threshold": r.get("graduation_threshold")},
            {"first_buy_block": r["block"], "first_buy_ts": r["ts"],
             "first_buyer": r.get("buyer"), "first_buy_quote": r["quote"],
             "first_buy_tx": r["tx"]},
            [{"block": r["block"], "log_index": 0, "buyer": r.get("buyer"),
              "quote": r["quote"], "ts": r["ts"]}],
            hits.get(addr, 0), 1, indexed=True)
        early_out.append({
            "address": r["address"], "symbol": r["symbol"],
            "launch_ts": r["launch_ts"], "block": r["block"],
            "age_seconds": (int(time.time()) - r["launch_ts"])
            if r["launch_ts"] else None,
            "first_buy_quote": (v["first_buy_quote"] or 0)
            / float(10 ** (r["quote_decimals"] or 18)),
            # Without this the column added ETH-denominated and
            # USDG-denominated figures into one unlabelled column, where the
            # smaller number was routinely the larger buy.
            "quote_symbol": r["quote_symbol"] or "ETH",
            "delta": v["delta"], "snipe_label": v["label"],
            "snipe_score": v["score"],
        })

    total = None
    try:
        total = chain.deployer_token_count(addr)
    except Exception:
        pass

    return {
        "address": addr,
        "eth_balance": balance / 1e18 if balance is not None else None,
        "eth_usd": eth_usd,
        "balance_usd": (balance / 1e18 * eth_usd)
        if (balance is not None and eth_usd) else None,
        "launched_total": total,
        # The real count, and separately how many of them this response holds,
        # so the panel can say "100 of 440" instead of implying 100 is all.
        "early_buy_count": store.wallet_early_buy_count(addr),
        "early_buys_listed": len(early_out),
        "launched": launched_out,
        "early_buys": early_out,
    }


@app.get("/api/wallets")
def api_wallets() -> dict[str, Any]:
    return {"wallets": store.wallet_list()}


@app.post("/api/wallets")
def api_wallet_add(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        addr = chain.w3.to_checksum_address(body.get("address") or "")
    except Exception:
        raise HTTPException(400, "not an address")
    store.wallet_add(addr, (body.get("label") or "").strip() or None)
    return {"wallets": store.wallet_list()}


@app.delete("/api/wallets/{address}")
def api_wallet_remove(address: str) -> dict[str, Any]:
    try:
        addr = chain.w3.to_checksum_address(address)
    except Exception:
        raise HTTPException(400, "not an address")
    if not store.wallet_remove(addr):
        raise HTTPException(404, "not watched")
    return {"ok": True}


# -------------------------------------------------------------- key wallets
# The Wallets tab. A key added here is stored so it can be shown again and so
# the server can sign the sweep back out of it - which is the only place in
# this project where a private key is used for anything. The address is derived
# from the key on the way in, and everything else keys off the address: no
# other endpoint accepts, returns or logs a key.
#
# The key is stored encrypted and the vault it is encrypted under has to be
# open to read or add one, so three of the routes below can answer 423. The
# passphrase is checked once, when it is given, and lives in the server's memory
# from then until it is locked or goes idle; it is never written, never returned
# and never logged, and neither is any part of a key.
def _need_vault() -> None:
    """Refuse anything that reads or writes a key while the vault is shut.

    Not applied to renaming or forgetting a wallet, which deliberately keeps
    working while locked: forgetting a key is always allowed to be easier than
    keeping it.
    """
    if not keysafe.unlocked():
        raise HTTPException(423, "the vault is locked - unlock it first")


def _key_io(fn, *args):
    """Run a call that opens a key, and answer with the code the page expects.

    The guard above and the read that follows it are not one atomic step: the
    idle lock fires from whichever request comes next, so a vault can shut in
    the gap and the read raises `Locked` after the check said it was open. That
    is the same answer as the guard's - 423 - and not a 500, which is what an
    uncaught exception here would look like from the page. `WrapError` is the
    other one: a blob that will not open, which is a stored value this
    passphrase does not fit.
    """
    try:
        return fn(*args)
    except keysafe.Locked:
        raise HTTPException(423, "the vault is locked - unlock it first")
    except keysafe.WrapError as e:
        raise HTTPException(409, str(e))


def _kw_wei(row: dict[str, Any] | None, col: str) -> int | None:
    """One stored reading as a number, or None when there is no reading.

    The column is TEXT - see the schema in store.py: 9.2 ETH is the signed
    64-bit ceiling in wei, so a balance above it would break silently in an
    INTEGER column - and the wire keeps the shape it already had, a number or
    null, because changing the format the page parses is not this task.

    A NULL comes back as None and never as 0: "this wallet holds nothing" and
    "nobody has asked the node" are different facts about it, and the page
    draws them differently.
    """
    raw = (row or {}).get(col)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _keywallet_chain_state(addrs: Sequence[str], cache: dict[str, dict[str, Any]],
                           now: int, running: bool = False) -> dict[str, Any]:
    """What the page should say under the table about the numbers above it.

    Pure: no lock, no network, no clock of its own. `now` is the server's clock
    and the ages are computed from it here rather than on the page, because a
    browser whose clock is a minute fast would print a negative age.

    `due` is decided here and not by the caller, so pressing refresh early is
    one local request that reads nothing: the page asks, the server says no and
    says when, and no arithmetic happens in the browser at all.

    `next_at` is `tried_at + ttl` over the newest attempt and is None when
    nothing has ever been tried - a table with no readings would otherwise have
    no clock to wait on and the page would post forever.
    """
    ttl = C.KEY_CHAIN_TTL
    rows = [cache[a] for a in addrs if a in cache]
    tried = [r["tried_at"] for r in rows if r.get("tried_at") is not None]
    at = max(tried) if tried else None
    # The attempt the caption speaks for. With the table's one read per wallet
    # every row shares a `tried_at` and therefore agrees with every other, so
    # picking the newest is not a tie-break between opinions - it is how the
    # caption stops being red the moment a read works, instead of carrying a
    # refusal from some earlier attempt on one row forever.
    last = max(rows, key=lambda r: r.get("tried_at") or 0) if rows else {}

    def half(key: str) -> dict[str, Any]:
        stamps = [r[key + "_at"] for r in rows if r.get(key + "_at") is not None]
        stamp = max(stamps) if stamps else None
        return {"at": stamp,
                "age": None if stamp is None else max(0, now - stamp),
                "error": last.get(key + "_err") or ""}

    next_at = None if at is None else at + ttl
    return {
        "ttl": ttl,
        "now": now,
        "running": bool(running),
        "due": bool(addrs) and not running and (next_at is None or now >= next_at),
        "next_at": next_at,
        # When the last read was attempted, which is what "the numbers below are
        # N old" means while one is in flight: the stamps in the halves are the
        # previous reading's, and printing those without saying so is the lie
        # this whole tab is being cured of.
        "at": at,
        "age": None if at is None else max(0, now - at),
        "rh": half("rh"),
        "arb": half("arb"),
    }


# The one reading in flight. The shape is copied from `_X_RUNS`/`_SW_RUNS`, and
# the distinction those comments draw is the one that matters here too: this
# dict is progress, not state. The numbers live in `key_wallet_chain` and
# outlive the process, so a restart loses the run and the page asks again -
# which costs one wait and not one reading that was already paid for.
#
# No stop flag and nothing hidden from the page: this is not a long walk, it is
# two calls. The key is a constant because the list is one - a second wallet is
# a second address in the same two calls, not a second reading.
_KW_CHAIN: dict[str, dict[str, Any]] = {}
_kw_chain_lock = threading.Lock()
KW_CHAIN_KEY = "list"


def _kw_run() -> dict[str, Any] | None:
    with _kw_chain_lock:
        return _KW_CHAIN.get(KW_CHAIN_KEY)


def _kw_running() -> bool:
    run = _kw_run()
    return bool(run and run["state"] == "running")


def _keywallet_list() -> dict[str, Any]:
    """The Wallets tab's rows, from SQLite and the cache and nothing else.

    The rows are a database read, and the two balances beside them are the last
    thing the chain said about those addresses. That is the whole fix: this used
    to make one live RPC call per stored wallet plus a batched call to the other
    chain *inside the request that draws the table*, through the same gate and
    the same eight slots the indexer holds. Measured on the live dashboard it
    took 638 to 10152 ms, and twice in eight page loads it never came back
    inside the page's 15 s budget - so `kw.wallets` stayed empty and the tab
    printed "no wallets saved yet" over wallets that were in the database the
    whole time.

    The numbers do not go away, they move: `chain` carries when they were read,
    how old they are, and why they are not newer, so the page can say that
    honestly instead of showing nothing. All six callers of this get it for
    free, which matters for the four that redraw the same table after a rename,
    an add or an unlock - they would otherwise wipe the caption under it.
    """
    rows = store.keywallet_list()
    addrs = [r["address"] for r in rows]
    launched = store.deployer_counts(addrs)
    early = store.early_buy_counts(addrs)
    cache = store.keywallet_chain_get(addrs)
    now = int(time.time())
    return {
        "locked": not keysafe.unlocked(),
        "chain": _keywallet_chain_state(addrs, cache, now, running=_kw_running()),
        "wallets": [{
            "address": r["address"],
            "label": r["label"],
            # The mask the key was stored with, not one built from the key here:
            # drawing the table must not need the vault open, and this is what lets
            # it draw with the vault shut.
            "secret_mask": r["mask"],
            "added_at": r["added_at"],
            # wei, not ether: the point of this tab is a balance that gets spent to
            # the last wei, and a float with eighteen decimals cannot say that
            "rh_wei": _kw_wei(cache.get(r["address"]), "rh_wei"),
            "arb_wei": _kw_wei(cache.get(r["address"]), "arb_wei"),
            "launched": launched.get(r["address"], 0),
            "early_buys": early.get(r["address"], 0),
        } for r in rows]}


def _run_keywallet_chain(addrs: list[str]) -> None:
    """Read both balances for the stored wallets, once, and store what came back.

    Two halves, two hosts, in parallel on purpose: run in sequence the wait is
    the sum of two answers rather than the longer of them, and the difference is
    not theoretical - a robinhood answer parked on a 429 for up to
    `RPC_PENALTY_MAX` would hold the Arbitrum number for two minutes for no
    reason at all.

    Each half catches its own exception, so one node being down is a sentence on
    its column and not a reason to lose the other chain's numbers. The store
    call happens in `finally`, unconditionally, even when both halves threw:
    that is what stamps `tried_at`, and without it a read that failed would be
    asked again on every tick of the page, which is the hammer the TTL exists to
    prevent. It is also why the write comes before the end marker - a page that
    saw the run finish while the numbers were still unwritten would ask again.
    """
    run = _kw_run()
    if not run:
        return
    rh: dict[str, int | None] = {}
    arb: dict[str, int] = {}
    rh_error = arb_error = ""
    state, error = "ok", ""

    def _rh_pass() -> None:
        nonlocal rh, rh_error
        try:
            rh = chain.balances(addrs)
        except Exception as e:
            # `chain.balances` already turns a failed batch into `None` per
            # address, so reaching here means something above it broke. The
            # sentence is written for every row it covers rather than each row
            # inventing its own reason for the same silence.
            rh, rh_error = {}, "the Robinhood node did not answer: %s" % type(e).__name__
            log.debug("key chain: rh half failed: %s", str(e)[:120])

    def _arb_pass() -> None:
        nonlocal arb, arb_error
        try:
            arb = bridge.arb_balances(addrs)
        except Exception as e:
            # `arb_balances` returns `{}` rather than raising, so this is the
            # same kind of surprise as the half above.
            arb, arb_error = {}, "the Arbitrum node did not answer: %s" % type(e).__name__
            log.debug("key chain: arb half failed: %s", str(e)[:120])

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_rh_pass), pool.submit(_arb_pass)]
            for f in futures:
                # Both halves swallow their own exceptions, so this is only
                # reached by a bug of ours - and a bug that silently ate the
                # numbers would be worse than one that shows up in the run.
                f.result()
    except Exception as e:
        state, error = "error", "%s: %s" % (type(e).__name__, str(e)[:200])
        log.exception("key chain read failed")
    finally:
        try:
            _db_retry(store.keywallet_chain_put, addrs, rh, arb,
                      rh_error=rh_error, arb_error=arb_error)
        except Exception as e:
            # `_db_retry` raises on its last attempt, and a database that will
            # not take the write must not leave the run looking alive forever.
            state = "error"
            error = error or "could not store the readings: %s" % type(e).__name__
            log.exception("key chain: could not store the readings")
        finally:
            with _kw_chain_lock:
                run["state"] = state
                run["error"] = error
                run["rh_error"], run["arb_error"] = rh_error, arb_error
                run["updated_at"] = run["finished_at"] = int(time.time())
    log.info("key chain read: %d wallets, rh %d/%d, arb %d/%d, %s",
             len(addrs), sum(1 for v in rh.values() if v is not None), len(addrs),
             len(arb), len(addrs), state)


@app.get("/api/keywallets")
def api_keywallets() -> dict[str, Any]:
    return _keywallet_list()


@app.post("/api/keywallets/chain")
def api_keywallet_chain() -> dict[str, Any]:
    """Ask for fresh balances for the stored wallets, if it is time to.

    Deliberately without `_need_vault`, and that is the point of the route:
    nothing in it opens a key. These are public balances of public addresses,
    and this is what lets the table draw *and* price itself with the vault shut
    - the same reason the mask is stored rather than computed from the key.

    Pressing early is free: `due` is decided here from the same clock the page
    was told about a moment ago, so the answer to an impatient press is the
    state and a time, not a promise to read. `started` says which of the two
    happened, so the page can tell "I started it" from "it was already going".
    """
    addrs = [r["address"] for r in store.keywallet_list()]
    cache = store.keywallet_chain_get(addrs)
    now = int(time.time())
    state = _keywallet_chain_state(addrs, cache, now, running=_kw_running())
    started = False
    if state["due"]:
        with _kw_chain_lock:
            live = _KW_CHAIN.get(KW_CHAIN_KEY)
            if not (live and live["state"] == "running"):
                _KW_CHAIN[KW_CHAIN_KEY] = {
                    "state": "running", "error": "", "rh_error": "", "arb_error": "",
                    "total": len(addrs), "started_at": now, "updated_at": now,
                    "finished_at": None,
                }
                started = True
    if started:
        # The run goes into the registry before the thread, because the thread
        # looks itself up there first thing and would return immediately if the
        # insert and the start were the other way round.
        threading.Thread(target=_run_keywallet_chain, args=(addrs,),
                         daemon=True).start()
    # Recomputed after the decision rather than before it: a page that has just
    # started a read should be told it is running, and one that pressed while
    # another page's read was in flight should be told that instead of being
    # left believing its own press is about to do something.
    return {"started": started,
            "chain": _keywallet_chain_state(addrs, cache, int(time.time()),
                                            running=_kw_running())}


@app.post("/api/keywallets/unlock")
def api_keywallet_unlock(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Open the vault with the passphrase the keys were stored under.

    With something already stored this costs one scrypt run, which is the point
    of it: a wrong passphrase is refused here after about a second rather than
    discovered later on a key that will not open. With nothing stored the
    passphrase is being set, and the page asks for it twice before it gets here.
    """
    phrase = str(body.get("passphrase") or "")
    if not phrase:
        raise HTTPException(400, "no passphrase given")
    blobs = store.keywallet_blobs()
    try:
        fresh = keysafe.unlock(phrase, blobs)
    except keysafe.WrapError as e:
        # 401 rather than 400: the request was well formed and the answer is
        # that this passphrase is not the one.
        raise HTTPException(401, str(e))
    repaired = store.keywallet_repair()
    log.info("vault unlocked: %d keys, %d repaired", len(blobs), repaired)
    return {"unlocked": True, "fresh": fresh, "repaired": repaired,
            **_keywallet_list()}


@app.post("/api/keywallets/lock")
def api_keywallet_lock() -> dict[str, Any]:
    """Shut the vault. The passphrase is gone from memory; nothing else changes."""
    keysafe.lock()
    log.info("vault locked")
    return _keywallet_list()


@app.post("/api/keywallets")
def api_keywallet_add(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _need_vault()
    raw = str(body.get("key") or "").strip()
    if not raw:
        raise HTTPException(400, "no key given")
    if not raw.startswith("0x"):
        raw = "0x" + raw
    try:
        acct = chain.w3.eth.account.from_key(raw)
    except Exception:
        # Deliberately one message for every way a key can be wrong. The
        # exception's own text is about length or hex and would be echoed into
        # the page, and nothing here should be echoing key material at all.
        raise HTTPException(400, "that is not a private key")
    addr = chain.w3.to_checksum_address(acct.address)
    label = (body.get("label") or "").strip() or None
    _key_io(store.keywallet_add, addr, label, raw)
    log.info("key wallet added: %s", addr)
    return _keywallet_list()


@app.patch("/api/keywallets/{address}")
def api_keywallet_label(address: str,
                        body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        addr = chain.w3.to_checksum_address(address)
    except Exception:
        raise HTTPException(400, "not an address")
    if not store.keywallet_label(addr, (body.get("label") or "").strip() or None):
        raise HTTPException(404, "not a stored wallet")
    return _keywallet_list()


@app.delete("/api/keywallets/{address}")
def api_keywallet_remove(address: str) -> dict[str, Any]:
    try:
        addr = chain.w3.to_checksum_address(address)
    except Exception:
        raise HTTPException(400, "not an address")
    if not store.keywallet_remove(addr):
        raise HTTPException(404, "not a stored wallet")
    log.info("key wallet removed: %s", addr)
    return _keywallet_list()


@app.get("/api/keywallets/{address}/secret")
def api_keywallet_secret(address: str) -> dict[str, Any]:
    """The key itself, for the reveal button and nothing else.

    Opening it costs about a second of scrypt, which is the reason the list
    beside it does not: the table draws from the stored masks and only this
    route ever pays for a key.
    """
    _need_vault()
    try:
        addr = chain.w3.to_checksum_address(address)
    except Exception:
        raise HTTPException(400, "not an address")
    # The vault opened on one key and not on this one when this raises 409,
    # which means this row was stored under a different passphrase - possible
    # only in a database that has been through a restore.
    secret = _key_io(store.keywallet_secret, addr)
    if not secret:
        raise HTTPException(404, "not a stored wallet")
    return {"address": addr, "secret": secret}


# ------------------------------------------------------------------ bridge
# Two directions, one shape: a leg up to Arbitrum and a leg back to Robinhood,
# both as relay deposits, so the wallet that ends up paid is never sent to
# directly by the wallet that paid. `out` is signed by the browser wallet;
# `back` is signed here, by a key off this machine's own database.
def _addr(value: Any, field: str) -> str:
    try:
        return chain.w3.to_checksum_address(str(value or ""))
    except Exception:
        raise HTTPException(400, f"{field} is not an address")


def _leg_pointer(direction: str, frm: str, to: str,
                 leg: int) -> tuple[int, int, str, str]:
    """(origin, destination, payer, recipient) for one leg of the route.

    Leg one always pays the same address it started from: the middle hop is
    bookkeeping, and the point of the trip is where the second leg lands.

    `return` is the single leg for money already sitting in Arbitrum - the
    address is both ends of it, which is the one case the other two directions
    refuse.
    """
    if direction == "return":
        return C.ARB_CHAIN_ID, C.CHAIN_ID, frm, to
    if leg == 1:
        return C.CHAIN_ID, C.ARB_CHAIN_ID, frm, frm
    return C.ARB_CHAIN_ID, C.CHAIN_ID, frm, to


def _public_leg(leg: dict[str, Any]) -> dict[str, Any]:
    """A leg as the page needs it: amounts in wei and in ether, and the
    transaction only when a browser wallet is the one that has to sign."""
    return {
        "origin_chain_id": leg["origin_chain_id"],
        "dest_chain_id": leg["dest_chain_id"],
        "user": leg["user"],
        "recipient": leg["recipient"],
        "amount": leg["amount"],
        "amount_eth": leg["amount"] / 1e18,
        "value": leg["value"],
        "to": leg["to"],
        "data": leg["data"],
        "gas": leg["gas"],
        "max_fee_per_gas": leg["max_fee_per_gas"],
        # The cap the amount was solved from, which the wallet has to be told
        # to use: sending a transaction with a fee above this is spending more
        # than the balance was told to reserve, and the node refuses it.
        "fee_cap": leg["fee_cap"],
        "gas_cost": leg["gas_cost"],
        "gas_cost_eth": leg["gas_cost"] / 1e18,
        "balance_before": leg["balance_before"],
        "balance_before_eth": leg["balance_before"] / 1e18,
        "left_estimate": leg["left_estimate"],
        "relayer_fee": leg["relayer_fee"],
        "relayer_fee_eth": leg["relayer_fee"] / 1e18,
        "out_estimate": leg["out_estimate"],
        "out_estimate_eth": leg["out_estimate"] / 1e18,
        "out_min_eth": leg["out_min"] / 1e18,
        "seconds": leg["seconds"],
        "request_id": leg["request_id"],
        "settled": leg.get("settled", True),
    }


@app.post("/api/bridge/plan")
def api_bridge_plan(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    direction = str(body.get("direction") or "out")
    if direction not in ("out", "back", "return"):
        raise HTTPException(400, "direction is out, back or return")
    leg_no = int(body.get("leg") or 1)
    if leg_no not in (1, 2):
        raise HTTPException(400, "leg is 1 or 2")
    frm = _addr(body.get("from"), "from")
    to = _addr(body.get("to"), "to")

    if direction == "return":
        # Nothing to refuse here: this direction exists for the case the other
        # two reject, which is an address collecting its own Arbitrum balance.
        pass
    elif direction == "back":
        stored = store.keywallet_get(frm)
        if not stored:
            raise HTTPException(404, "that wallet is not in the stored list")
        if to.lower() == frm.lower():
            raise HTTPException(400, "the destination is the paying wallet itself")
    elif to.lower() == frm.lower():
        raise HTTPException(400, "the destination is the connected wallet itself")

    origin, dest, payer, recipient = _leg_pointer(direction, frm, to, leg_no)
    try:
        leg = bridge.plan(origin, dest, payer, recipient)
    except bridge.RelayError as e:
        raise HTTPException(400, str(e))
    return {"direction": direction, "signer": "server" if direction == "back"
            else "browser", "leg": _public_leg(leg)}


@app.get("/api/bridge/balances")
def api_bridge_balances(address: str = "") -> dict[str, Any]:
    """Both chains' balances for one address, which is what the runner reads
    while it waits for a leg to land. The Wallets list carries the same numbers
    for the stored keys, but the connected wallet is not one of them."""
    addr = _addr(address, "address")
    return {"address": addr,
            "rh_wei": bridge.balance_of(C.CHAIN_ID, addr),
            "arb_wei": bridge.balance_of(C.ARB_CHAIN_ID, addr)}


@app.get("/api/bridge/status")
def api_bridge_status(requestId: str = "") -> dict[str, Any]:
    """relay's own status for a request, so the page does not need CORS and
    does not need to know the relay host."""
    if not requestId:
        raise HTTPException(400, "no requestId")
    try:
        return bridge.status(requestId)
    except bridge.RelayError as e:
        raise HTTPException(502, str(e))


# ------------------------------------------------- the sweep, signed here
# A run is a dict in memory rather than a table. It is not a record of
# anything: the chain holds the outcome, every step is retryable by pressing
# the button again, and a restart losing the log of a finished run costs
# nothing. What it must not do is outlive the process pretending to be a state
# the database can resume from.
_SWEEPS: dict[str, dict[str, Any]] = {}
_sweep_lock = threading.Lock()
SWEEP_KEEP = 40


def _sweep(run_id: str) -> dict[str, Any] | None:
    with _sweep_lock:
        return _SWEEPS.get(run_id)


def _sweep_note(run: dict[str, Any], leg: int, state: str,
                detail: str = "", tx: str | None = None) -> None:
    with _sweep_lock:
        step = next((s for s in run["steps"] if s["leg"] == leg), None)
        if step is None:
            step = {"leg": leg, "state": state, "detail": "", "tx": None}
            run["steps"].append(step)
        step["state"] = state
        if detail:
            step["detail"] = detail
        if tx:
            step["tx"] = tx
        run["updated_at"] = int(time.time())


def _sweep_leg(run: dict[str, Any], secret: str, leg_no: int, frm: str,
               to: str) -> str:
    """Run one leg. Returns its final state, which the caller acts on."""
    origin, dest, payer, recipient = _leg_pointer("back", frm, to, leg_no)
    _sweep_note(run, leg_no, "quoting", "asking relay for a route")
    last = None
    # Two attempts, and the second one is the point: the amount is the balance
    # minus the gas of the quote, so a quote whose fee has already moved is a
    # quote that would either be rejected by the node or leave more behind than
    # planned. Re-quoting costs nothing and a stale quote costs the trip.
    for attempt in (1, 2):
        leg = bridge.plan(origin, dest, payer, recipient)
        last = leg
        _sweep_note(run, leg_no, "signing",
                    f"sending {leg['amount'] / 1e18:.8f} ETH from chain {origin}")
        try:
            tx = bridge.sign_send(secret, leg)
            break
        except bridge.RelayError as e:
            if attempt == 2 or "fee moved" not in str(e):
                raise
            _sweep_note(run, leg_no, "quoting", "the fee moved, quoting again")
    else:
        raise bridge.RelayError("could not build a sendable transaction")

    _sweep_note(run, leg_no, "sent", f"tx {tx}", tx)
    bridge.wait_receipt(origin, tx)
    _sweep_note(run, leg_no, "bridging", "waiting for relay to fill it")
    filled = bridge.wait_fill(last["request_id"])
    if filled.get("timed_out"):
        # Not a failure: the deposit is in and the solver is still working, so
        # the money is on its way. The run stops waiting here rather than
        # starting a second leg on a balance that has not landed.
        _sweep_note(run, leg_no, "pending",
                    "relay has not filled it yet; it is still in flight")
        return "pending"
    _sweep_note(run, leg_no, "filled",
                f"{last['out_estimate'] / 1e18:.8f} ETH out")
    return "filled"


def _run_sweep(run_id: str, secret: str, frm: str, to: str) -> None:
    run = _sweep(run_id)
    outcome = "done"
    try:
        if _sweep_leg(run, secret, 1, frm, to) == "pending":
            outcome = "pending"
        else:
            # The second leg needs the middle balance to exist. A filled intent
            # means the solver paid, so this waits on a node catching up rather
            # than on the bridge.
            deadline = time.time() + 90
            bal = 0
            while time.time() < deadline:
                bal = bridge.balance_of(C.ARB_CHAIN_ID, frm) or 0
                if bal > 0:
                    break
                time.sleep(3)
            if bal <= 0:
                raise bridge.RelayError(
                    "the middle hop reported filled but the Arbitrum balance "
                    "is still zero; nothing was sent on to Robinhood")
            if _sweep_leg(run, secret, 2, frm, to) == "pending":
                outcome = "pending"
    except Exception as e:
        log.warning("sweep %s failed: %s", run_id, str(e)[:200])
        with _sweep_lock:
            run["state"] = "failed"
            run["error"] = str(e)
        outcome = "failed"
    finally:
        final = {
            "rh_wei": bridge.balance_of(C.CHAIN_ID, frm),
            "arb_wei": bridge.balance_of(C.ARB_CHAIN_ID, frm),
            "to_rh_wei": bridge.balance_of(C.CHAIN_ID, to),
        }
        with _sweep_lock:
            if run["state"] == "running":
                run["state"] = outcome
            run["final"] = final
            run["finished_at"] = int(time.time())
        # The Wallets tab's cached numbers for this address are now a guess: the
        # money moved, and the cache has no way to know by how much or whether
        # the second leg landed. So the reading is dropped rather than kept or
        # invented, which puts the row back to "never read" - the table draws a
        # dash for one tick, the next tick is due, and the numbers come back in
        # about the time two batched calls take.
        try:
            _db_retry(store.keywallet_chain_drop, [frm])
        except Exception as e:
            log.debug("sweep %s: could not drop the cached balances: %s",
                      run_id, str(e)[:120])


@app.post("/api/bridge/sweep")
def api_bridge_sweep(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Start pulling a stored wallet's balance back to another address.

    Signed here with the key that wallet was added with, so this endpoint is
    the one place in the app that spends without a wallet prompt. It is a
    POST with an explicit from and to, and it does nothing until the page has
    shown the plan it will execute.
    """
    frm = _addr(body.get("from"), "from")
    to = _addr(body.get("to"), "to")
    if to.lower() == frm.lower():
        raise HTTPException(400, "the destination is the paying wallet itself")
    _need_vault()
    stored = store.keywallet_get(frm)
    if not stored:
        raise HTTPException(404, "that wallet is not in the stored list")
    # Opened once, here, and handed to the thread as the one key it needs: the
    # run signs up to two legs and re-quotes between them, and none of that
    # should cost another scrypt run - nor should it need the vault to still be
    # open, which is what lets the idle lock fire mid-sweep without breaking it.
    secret = _key_io(store.keywallet_secret, frm)

    with _sweep_lock:
        live = [r for r in _SWEEPS.values() if r["state"] == "running"]
        if live:
            raise HTTPException(409, "a sweep is already running")
        if len(_SWEEPS) >= SWEEP_KEEP:
            for k in sorted(_SWEEPS, key=lambda k: _SWEEPS[k]["started_at"])[:-10]:
                _SWEEPS.pop(k, None)
        run_id = uuid.uuid4().hex[:12]
        _SWEEPS[run_id] = {
            "id": run_id, "state": "running", "from": frm, "to": to,
            "steps": [], "error": None, "final": {},
            "started_at": int(time.time()), "updated_at": int(time.time()),
        }
    log.info("sweep %s: %s -> %s", run_id, frm, to)
    threading.Thread(target=_run_sweep,
                     args=(run_id, secret, frm, to),
                     daemon=True).start()
    return _sweep(run_id)


@app.get("/api/bridge/sweep/{run_id}")
def api_bridge_sweep_get(run_id: str) -> dict[str, Any]:
    run = _sweep(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return run


# ------------------------------------------------------------------ images
@app.get("/img")
def img(u: str, w: int = 0) -> Response:
    """Cached proxy for ipfs:// and http(s) images so the grid never stalls."""
    # Ours first, and before the uri is even resolved: a logo this machine
    # pinned is a file on this disk, and reading it off the disk cannot be
    # rate limited by anyone.
    local = _local_pin(u)
    if local is not None:
        return _file_response(local, local.suffix)

    hit = imgcache.cached(u, w)
    if hit is not None:
        return _file_response(hit, hit.suffix)

    urls = imgcache.resolve(u)
    if not urls:
        raise HTTPException(400, "unsupported uri")

    try:
        data, _ctype, ext = imgcache.fetch(urls)
    except imgcache.TooLarge:
        raise HTTPException(413, "too large")
    except LookupError:
        raise HTTPException(502, "fetch failed")

    p = imgcache.put(u, w, data, ext)
    return _file_response(p, p.suffix)


def _file_response(path: Path, ext: str) -> Response:
    """The image's bytes, rather than a FileResponse pointed at the file.

    FileResponse takes its Content-Length from the file's size when it is
    built and then streams whatever is on disk when it is read, and between
    those two moments the file can be replaced by the request that is fetching
    the same logo. The response then declares one length and sends another,
    and h11 abandons the connection with "too much data for declared
    Content-Length" - four of those in the first twenty-five minutes of this
    run, each one a logo that never appeared on the page.

    Bytes read in one go cannot disagree with themselves. The cache holds
    images up to IMAGE_MAX_MB, so this is a small buffer, not a copy of the
    cache. `imgcache.put` renames into place for the same reason, so that the
    bytes read here are always a whole image.

    The read is retried, because on Windows it can be refused for a moment: a
    file being renamed into place by the request that is fetching the same logo
    cannot be opened while that rename is in flight, and OneDrive's sync can
    hold a file it is reading. Measured, not guessed: the concurrency probe
    `_recon/img_race.py` produced this as a 500 with a PermissionError traceback
    on a logo that a second later reads fine. Three attempts twenty milliseconds
    apart, then it is reported as a miss - a 404 for one avatar is a missing
    avatar, and a 500 is a traceback in the log and the whole page's request
    failing rather than that one picture's.
    """
    last: OSError | None = None
    for attempt in range(3):
        try:
            data = path.read_bytes()
            break
        except OSError as exc:
            last = exc
            time.sleep(0.02 * (attempt + 1))
    else:
        raise HTTPException(404, "the cached image is not readable right now: %s"
                            % last)
    mt = mimetypes.guess_type(f"x{ext}")[0] or "image/png"
    return Response(content=data, media_type=mt,
                    headers={"Cache-Control": "public, max-age=86400"})


def _cid_of(uri: str) -> str:
    """The cid in an ipfs uri or a bare cid, or "" when this is neither.

    Ascii alphanumeric only, and at least twenty of them. A cid is always
    that, so nothing legitimate is refused - and this value is about to be
    used as a filename, which is not a place to accept characters from a
    query string on trust.
    """
    cid = uri[len("ipfs://"):] if uri.startswith("ipfs://") else uri
    cid = cid.lstrip("/")
    if len(cid) < 20 or not cid.isascii() or not cid.isalnum():
        return ""
    return cid


def _local_pin(uri: str) -> Path | None:
    """The file we pinned for this uri, when it is one of ours."""
    cid = _cid_of(uri)
    if not cid:
        return None
    for p in C.PINS_DIR.glob(f"{cid}.*"):
        if p.stat().st_size > 0:
            return p
    return None


def _keep_pin(uri: str, blob: bytes, ext: str) -> None:
    """Keep the bytes we just pinned, so serving them needs no network."""
    cid = _cid_of(uri)
    if not cid:
        return
    try:
        (C.PINS_DIR / f"{cid}.{ext}").write_bytes(blob)
    except OSError as e:
        # The pin succeeded and the uri is good; only the local copy for
        # serving is missing, and the gateway path still covers that.
        log.warning("no local copy of %s: %s", cid, str(e)[:120])


# ------------------------------------------------------------------ static
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(str(STATIC / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=C.HOST, port=C.PORT, log_level="info")
