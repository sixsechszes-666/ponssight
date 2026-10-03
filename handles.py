"""Is this X handle already claimed by a launch?

The twitter field is free text copied out of the token contract, so the same
account is stored in every shape a person can paste: a bare handle, an
@handle, a twitter.com url, an x.com url with a query string still on it.
Matching the column against what was typed finds a fraction of the claims and
misses the rest silently, and for this question that is the worst answer
available: "no launch claims this handle" is exactly what a copier wants to
hear and exactly what a partial match says by accident.

So the stored value is normalised down to the handle it points at and the
comparison happens there. The reserved-path set is the other half of it:
x.com/i, /search, /home and the rest look exactly like handles once the url is
stripped, and a checker that answers "147 launches claim @i" is reporting a url
shape rather than an account.

Both rules were worked out against the live table before this file existed;
what is here is that work with the reporting taken off.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# X's own first-path segments. None of these is an account, and several of them
# (i, home, search) are things people paste by accident or on purpose.
RESERVED = frozenset({
    "i", "search", "home", "intent", "hashtag", "explore", "notifications",
    "messages", "settings", "compose", "share", "login", "signup", "status",
    "about", "tos", "privacy", "help", "download", "communities", "lists",
    "topics", "bookmarks", "jobs", "es", "en", "ja", "zh",
})

_SCHEME = re.compile(r"^https?://", re.I)
_HOST = re.compile(r"^(www\.|mobile\.)?(twitter|x)\.com/", re.I)
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")


def norm(value: Any) -> str | None:
    """The handle a stored twitter field points at, lowercased, or None.

    None means the field is empty, or holds something that is not a handle at
    all - the many entries in this column that are prose describing a social
    rather than a link to one. A caller has to keep that apart from a handle
    that simply matched nothing, because the two answer the question
    differently.
    """
    if not value:
        return None
    s = str(value).strip()
    s = _SCHEME.sub("", s)
    s = _HOST.sub("", s)
    # The first path segment is the handle. Everything after it is a path, a
    # query or a fragment, and all three are common in a pasted link.
    s = s.lstrip("@").split("?")[0].split("#")[0].split("/")[0].strip()
    if not _HANDLE.match(s):
        return None
    return s.lower()


def claims(rows: Iterable[dict[str, Any]], handle: str) -> list[dict[str, Any]]:
    """The rows whose twitter field points at exactly this handle.

    Exact, not a substring: @elonmusk and @elonmusk_fan are different accounts
    and a copier checking the first is not asking about the second.
    """
    return [r for r in rows if norm(r.get("twitter")) == handle]


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the claims add up to.

    The deployer count is the one that carries the judgement. A handle claimed
    by one wallet is that account's own launch. A handle claimed by thirty
    wallets is a brand being farmed, and the launches under it are other
    people's copies of the original, which is a different thing to be looking
    at even though the table is identical.
    """
    deps = {(r.get("deployer") or "").lower() for r in rows}
    deps.discard("")
    ts = [r["launch_ts"] for r in rows if r.get("launch_ts")]
    return {
        "launches": len(rows),
        "deployers": len(deps),
        "symbols": len({(r.get("symbol") or "").lower() for r in rows} - {""}),
        "first_ts": min(ts) if ts else None,
        "last_ts": max(ts) if ts else None,
    }
