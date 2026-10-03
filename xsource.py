"""The X side of the followings tab: one profile, and a page-by-page walk of who somebody follows.

This is the only module in the project that talks to X, and it is kept separate
from store.py for that reason - everything here can fail because somebody else's
service is down, rate limiting us, or has changed its answer shape, and none of
that should be entangled with the database.

The client itself is not ours. It lives in the neighbouring bot project and is
imported from there rather than copied: it is a couple of thousand lines that
are still being maintained over there, and a copy here would drift within weeks
and be debugged twice. The path is one config value (`PONS_XCLIENT_DIR`).

What is deliberately NOT here:
  * no database. The walk hands each page to a callback and the caller decides
    what to do with it. That is what makes the batched display possible - the
    page is committed by the caller the moment it arrives, instead of at the end
    of a walk that may take a minute.
  * no token in any log line, ever. Only the name of the variable it came from
    is ever mentioned. The borrowed client is not so careful with `ct0` and logs
    its first sixteen characters, so that line is redacted on the way to our
    handlers - see `_quiet_ct0`, which is attached before the session is built
    rather than after, since building it is what logs.

Rate limiting is handled by the client's own `_page_with_limit`, which waits on
`x-rate-limit-reset` and retries the same request. The finished collector over
there (`_collect_all_v11`) does not use it and dies on the first 429 with
everything it had collected, which is why the walk here is built on the raw page
method instead.

Measured before this was written, on @poly_enjoyer (815 following):
`friends/list.json` returns 200 users in 0.62 s, `total_count` in the response is
None, and the per-user objects are still in the old flat shape that
`xprofile.parse_list_user` reads.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from typing import Any, Callable, Iterable

import config as C
import xprofile

log = logging.getLogger("pons.xsource")

# One client for the whole process. The session holds cookies and the ct0 token
# it fetched, and a second one would fetch its own - so this is not a pool but a
# single shared thing, and it is locked rather than duplicated. X's limits are
# per account, so more clients from one token would not be faster anyway.
_lock = threading.RLock()
_reader: Any = None
_token_from = ""
_redacted = False

# `xclient.client` logs the first sixteen characters of `ct0` at INFO when it
# acquires a session (client.py:89, `f"{prefix}ct0 acquired [grey70]{ct0[:16]}..."`).
# That line did not reach this project's log until this module started the
# session, and now it does. The client is somebody else's file and is not edited
# here, so the message is rewritten on its way to our handlers instead: the
# diagnostic survives with its length and the credential does not.
#
# A filter rather than a level change, because silencing the logger would also
# drop anything it chooses to say at WARNING later, and a module that fails to
# refresh a session is exactly the thing worth hearing about.
_CT0 = re.compile(r"(ct0 acquired\s*(?:\[[^\]]*\])?)([0-9a-fA-F]{6,})")


class _RedactCt0(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg
        if isinstance(msg, str) and "ct0 acquired" in msg:
            record.msg = _CT0.sub(r"\1[redacted]", msg)
        return True


def _quiet_ct0() -> None:
    """Attach the redaction once, before anything can acquire a session."""
    global _redacted
    if _redacted:
        return
    logging.getLogger("twitter.client").addFilter(_RedactCt0())
    _redacted = True


class XUnavailable(RuntimeError):
    """X could not be reached, or refused us, and the reason is worth showing.

    Kept as its own class so a caller can tell "X says no" apart from a bug of
    ours: the first is a message on the page, the second is a traceback in the
    log. This exists because the tab has to keep working when X does not - the
    local launch index it already answers from needs no network at all.
    """


def _bot_env() -> dict[str, str]:
    """The bot's env file as a dict, or {} if it is not there.

    Read rather than inherited: the dashboard is started from its own directory
    with its own environment, and the auth token lives in the bot's .env because
    that is where the bot reads it from. Values are never logged.
    """
    path = C.X_ENV_FILE
    if not path or not os.path.exists(path):
        return {}
    try:
        from dotenv import dotenv_values
        return {k: (v or "") for k, v in (dotenv_values(path) or {}).items()}
    except Exception as e:  # pragma: no cover - a broken env file is a setup problem
        log.warning("x: cannot read %s: %s", os.path.basename(path), str(e)[:120])
        return {}


def _credentials() -> tuple[str, str | None, str]:
    """(token, proxy, where the token came from).

    The variable's name is returned so the log can say which one was used -
    there are three in play between the two projects and "it works" is not a
    useful thing to read when it stops.
    """
    env = _bot_env()
    token = C.X_TOKEN or (env.get(C.X_TOKEN_ENV) or "").strip()
    where = "PONS_X_TOKEN" if C.X_TOKEN else C.X_TOKEN_ENV
    if not token:
        # The bot keeps a rotation of parsing accounts; the first one is the same
        # one its own parsing starts with.
        accounts = [a.strip().lstrip("@") for a in (env.get("PARSE_ACCOUNTS") or "").split(",")]
        label = accounts[0] if accounts and accounts[0] else ""
        if label:
            token = (env.get(("AUTH_TOKEN_" + label.upper()).replace("-", "_"))
                     or env.get("PARSE_TOKEN_" + label.upper()) or "").strip()
            where = "PARSE_ACCOUNTS[0]=%s" % label
    proxy = (env.get("PROXY") or "").strip() or None
    return token, proxy, where


def available() -> tuple[bool, str]:
    """Whether a walk can be attempted, and why not if it cannot.

    Called before a button is offered rather than after it is pressed: a missing
    token is a setup fact, and the page can say so instead of starting a walk
    that fails on its first request.
    """
    if not C.XCLIENT_DIR:
        return False, "X is not configured: set PONS_XCLIENT_DIR to the folder that has the xclient package"
    if not os.path.isdir(C.XCLIENT_DIR):
        return False, "x client not found at %s (PONS_XCLIENT_DIR)" % C.XCLIENT_DIR
    token, _proxy, where = _credentials()
    if not token:
        return False, "no auth token: set PONS_X_TOKEN or %s in the bot's .env" % C.X_TOKEN_ENV
    return True, where


def _client():
    """The shared reader, built on first use.

    Built lazily so that importing this module never touches the network - the
    server imports it at startup and has to come up whether X is reachable or
    not. `ensure_ct0` is called here rather than per request because it is a
    network round trip of its own.
    """
    global _reader, _token_from
    with _lock:
        if _reader is not None:
            return _reader
        token, proxy, where = _credentials()
        if not token:
            raise XUnavailable(
                "no X auth token: set PONS_X_TOKEN or %s in %s"
                % (C.X_TOKEN_ENV, os.path.basename(C.X_ENV_FILE)))
        if C.XCLIENT_DIR and C.XCLIENT_DIR not in sys.path:
            sys.path.insert(0, C.XCLIENT_DIR)
        try:
            from xclient.client import TwitterClient
            from xclient.reader import TwitterReader
        except Exception as e:
            raise XUnavailable("cannot import the xclient from %s: %s"
                               % (C.XCLIENT_DIR, str(e)[:160]))
        cl = TwitterClient(token, proxy=proxy, timeout=int(C.X_TIMEOUT))
        # Before ensure_ct0, because that call is what logs the token.
        _quiet_ct0()
        try:
            cl.ensure_ct0()
        except Exception as e:
            # The token is not in this message and must not be: it comes from
            # the client's own error, which names the status, not the secret.
            raise XUnavailable("x rejected the session: %s" % str(e)[:160])
        _reader = TwitterReader(cl)
        _token_from = where
        log.info("x: session ready (token from %s, proxy %s)",
                 where, "yes" if proxy else "no")
        return _reader


def reset() -> None:
    """Forget the client. The next call builds a new one.

    For the case the session goes bad mid-run - a rotated token, a stale ct0 -
    where retrying with the same object would keep failing.
    """
    global _reader
    with _lock:
        _reader = None


def profile(handle: str) -> dict[str, Any] | None:
    """One account's profile, in the flat shape `xprofile` returns.

    A separate request from the followings walk because X answers it from a
    different endpoint, and because the profile is what the card draws even when
    the walk is never started.
    """
    name = (handle or "").strip().lstrip("@")
    if not name:
        return None
    with _lock:
        r = _client()
        try:
            raw = r.get_user_by_screen_name_raw(name)
        except Exception as e:
            raise XUnavailable(_reason(e, "profile of @%s" % name))
    info = xprofile.parse_profile(raw)
    if info is None:
        # A well-formed answer with nothing in it: X does this for an account
        # that does not exist. Distinguishing it from a network failure is the
        # difference between "no such account" and "X is down".
        raise XUnavailable("x returned no profile for @%s" % name)
    return info


def profiles(handles: Sequence[str], pace: float = 0.5,
             on_result: Callable[[str, dict[str, Any] | None,
                                  Exception | None], None] | None = None,
             should_stop: Callable[[], bool] | None = None,
             ) -> dict[str, dict[str, Any] | None]:
    """Look up a list of profiles, one at a time, with a pause between them.

    Returns `{handle: profile_or_None}`. A `None` value means the handle does
    not exist on X - a well-formed empty answer from `parse_profile`. Network
    errors are raised as `XUnavailable` and are NOT written to `x_lookups`,
    because a transient failure is not the same fact as a missing account.

    `on_result(handle, profile, error)` is called after each handle, whether
    it succeeded, was missing, or errored. It is the caller's chance to write
    the row to the database without this module having to import `store`.

    A rate limit is waited out rather than raised. `profile()` is one GraphQL
    request with no backoff of its own, and X answers a long walk with a 429
    long before the list is done - the reset it names in the error is how long
    it wants. Sleeping to that timestamp and retrying the same handle is what
    turns a walk that always dies at a quarter of the list into one that
    finishes, and it is the same rule `followings` follows through the client's
    own `_page_with_limit`. A single wait is capped at `PONS_SW_X_WAIT` and the
    total time spent asleep at `PONS_SW_X_WAIT_BUDGET`; past that the walk stops
    on the handle boundary with everything it has, and pressing the button again
    carries on from there. The total matters as much as the single wait: X can
    hand out a fresh reset every time one expires, and a walk that kept sleeping
    would hold the button down for as long as it cared to.

    The pace is deliberate: a hundred requests in one second is a guaranteed
    429, and half a second is what the followings walk has spent between pages
    without being limited.
    """
    out: dict[str, dict[str, Any] | None] = {}
    items = list(dict.fromkeys(h for h in handles if h))
    budget = float(getattr(C, "SW_X_WAIT_BUDGET", 900.0))
    slept = 0.0
    for i, h in enumerate(items):
        if should_stop and should_stop():
            break
        while True:
            try:
                info = profile(h)
            except XUnavailable as e:
                msg = str(e)
                if "no profile" in msg:
                    # A well-formed answer with nothing in it: the account does
                    # not exist. Written as missing and not raised, because the
                    # caller should keep walking the rest of the list.
                    out[h] = None
                    if on_result:
                        on_result(h, None, None)
                    break
                wait = _limit_wait(msg)
                if wait and slept + wait <= budget:
                    # X named the moment it will answer again. Sleep to it and
                    # ask for the same handle once more.
                    log.info("x: rate limited, waiting %ds at %s", wait, h)
                    time.sleep(wait)
                    slept += wait
                    continue
                # A real error, or a limit we have already spent the whole
                # budget waiting on: session, changed shape, or a refusal that
                # named no reset. The handles already walked are committed; the
                # rest will be retried when the page presses the button again.
                out[h] = None
                if on_result:
                    on_result(h, None, e)
                raise
            else:
                out[h] = info
                if on_result:
                    on_result(h, info, None)
                break
        if i < len(items) - 1 and pace > 0:
            time.sleep(pace)
    return out


_RESET = re.compile(r"reset=(\d{9,})")


def _limit_wait(msg: str) -> int:
    """Seconds to sleep for a rate limit, or 0 if this is not one.

    Read from the timestamp X puts in its own refusal rather than from a fixed
    backoff, because the window is not ours to guess: the header says when the
    next request is allowed, and sleeping less than that is one wasted round
    trip and one more 429. Capped by `PONS_SW_X_WAIT`, and never negative - a
    reset already in the past means the window has reopened, so the retry is
    immediate.
    """
    if "rate limit" not in msg.lower() and "429" not in msg:
        return 0
    m = _RESET.search(msg)
    if not m:
        # A 429 with no timestamp: a short fixed wait is better than a raise,
        # and the loop above re-reads the limit on the next refusal.
        return 30
    left = int(m.group(1)) - int(time.time()) + 3
    cap = int(getattr(C, "SW_X_WAIT", 900.0))
    return max(1, min(left, cap))


def user_id(handle: str) -> str:
    """The numeric id behind a handle, which is what the list endpoints take."""
    with _lock:
        r = _client()
        try:
            return str(r.resolve_user_id((handle or "").strip().lstrip("@")))
        except Exception as e:
            raise XUnavailable(_reason(e, "id of @%s" % handle))


def followings(
    owner_id: str,
    *,
    pages: int = 0,
    cursor: str = "",
    on_page: Callable[[list[dict[str, Any]], str], None],
    should_stop: Callable[[], bool] | None = None,
    deadline: float = 0.0,
) -> dict[str, Any]:
    """Walk somebody's followings, handing each page to `on_page` as it lands.

    Returns a summary - not the users, which the callback has already been given
    and has presumably written down. Returning them as well would mean holding a
    five-thousand-person list in memory for no reason, which is the thing the
    streaming design exists to avoid.

    `on_page(users, cursor)` is called once per page, in arrival order, and is
    where the caller commits. That is the whole point: the delay between a page
    arriving from X and it being visible on the page is one commit, not the
    length of the walk.

    Stopping is deliberate rather than exceptional. `should_stop` is checked
    between pages - the stop button - and `deadline` is an absolute time (from
    `time.time()`) after which the walk returns anyway. Both end the walk with
    `state="stopped"` and the counts of what was actually fetched, so a partial
    list cannot be mistaken for a complete one: the caller has `total` from the
    profile to compare against.

    A 429 is waited out by the client and the same page retried, so a rate limit
    costs time and not data. If the wait would exceed `PONS_X_MAX_WAIT` the walk
    stops on the page boundary with its cursor, and resuming from that cursor
    (`cursor=`) carries on rather than starting over.
    """
    if not owner_id:
        raise XUnavailable("no owner id")
    r = _client()
    count = max(1, min(int(C.X_PAGE), 200))
    limit = int(pages) if pages else int(C.X_MAX_PAGES)

    users = 0
    done = 0
    state = "ok"
    error = ""
    last_cursor = cursor
    t0 = time.time()

    while done < limit:
        if should_stop and should_stop():
            state, error = "stopped", "stopped by hand"
            break
        if deadline and time.time() > deadline:
            state, error = "stopped", "time limit reached"
            break

        cur = last_cursor or "-1"
        try:
            with _lock:
                raw = r._page_with_limit(
                    lambda: r._users_list_v11_raw("following", owner_id, count, cur),
                    wait_on_limit=True, max_wait=int(C.X_MAX_WAIT), on_wait=None)
        except Exception as e:
            # Everything fetched so far is already committed by on_page, so this
            # loses the rest of the walk and nothing else.
            state, error = "error", _reason(e, "followings of %s" % owner_id)
            break

        page, nxt = xprofile.parse_users_page(raw)
        done += 1
        if page:
            users += len(page)
            on_page(page, nxt)
        last_cursor = nxt

        # X signals the end with a "0" cursor and, sometimes, with an empty page
        # while still handing out a cursor. Both mean the same thing here.
        if not page or not nxt or nxt == "0":
            state = "ok"
            break
    else:
        # The page limit was reached, which is not an error but is also not the
        # end of the list - the caller compares users against total and says so.
        state = "ok"
        error = "page limit reached"

    return {
        "pages": done,
        "users": users,
        "state": state,
        "error": error,
        "cursor": last_cursor,
        "seconds": round(time.time() - t0, 2),
        "token_from": _token_from,
    }


def _reason(e: Exception, what: str) -> str:
    """A short, safe sentence for a failure from somebody else's client.

    Built from the exception's class and text only. A token cannot appear here:
    the client never puts one in a message, and nothing in this module formats
    one - which is checked by reading every log line, not by trusting it.
    """
    name = type(e).__name__
    text = " ".join(str(e).split())[:200]
    # The one shape worth naming: a status code the client raised on, which is
    # the difference between "X is having a bad day" and "we are blocked".
    code = getattr(getattr(e, "response", None), "status_code", None)
    if code:
        return "%s: %s (http %s)" % (name, text or what, code)
    return "%s: %s" % (name, text or what)


def probe() -> dict[str, Any]:
    """One profile lookup, for the verify script and for checking setup.

    Deliberately the smallest thing that proves the whole chain works: config
    path, env, client, network, parsing. Returns the profile, never the token.
    """
    ok, why = available()
    if not ok:
        return {"ok": False, "why": why}
    try:
        info = profile("poly_enjoyer")
    except XUnavailable as e:
        return {"ok": False, "why": str(e), "token_from": why}
    return {"ok": True, "token_from": why,
            "handle": info["handle"], "followers": info["followers"],
            "following": info["following"],
            "has_avatar": bool(info["avatar"]), "has_banner": bool(info["banner"]),
            "bio_len": len(info["bio"] or "")}
