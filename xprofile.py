"""Reading an X account out of the two shapes X returns it in.

X does not answer one way. The single-profile endpoint (GraphQL
`UserByScreenName`) has moved to a new shape - `core`, `avatar`, `banner`,
`profile_bio`, `relationship_counts`, `verification` - and the `legacy` object
everything used to read is simply gone from it. The list endpoints
(`friends/list.json`) still answer in the old shape, with the counts and the
urls at the top level of the user object.

So the same account arrives in two different arrangements depending on which
door it came through, and both are parsed here into one flat dict. That is the
whole reason this module exists: the alternative is every caller knowing which
endpoint a profile came from, and getting a card full of nulls the day X moves
the other half over.

No network and no database - raw JSON in, a dict out - so this is checked by
unit tests rather than by looking at a page, the way snipe.py is.

What was measured before any of it was written, and is why the new shape is
handled at all: the profile lookup for @poly_enjoyer returns
`legacy` absent, `relationship_counts` = {followers: 1943, following: 815},
`avatar.image_url` and `banner.image_url` present, `profile_bio.description`
holding the bio. Reading `legacy` off that answer gives None for every field a
profile card is made of.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# X writes timestamps as "Sat Jun 25 21:16:20 +0000 2022" in both shapes. A
# value that does not parse is not an error worth raising: the card shows the
# account without an age rather than not at all.
_CREATED = "%a %b %d %H:%M:%S %z %Y"

# Avatars are served as `..._normal.jpg`; the bigger renders are the same path
# with the suffix swapped. X documents three of them and `_normal` is what both
# endpoints hand out.
_AVATAR_SIZE = re.compile(r"_(normal|bigger|mini|x96)\.(jpg|jpeg|png|gif|webp)$",
                          re.I)


def _ts(value: Any) -> int | None:
    """Epoch seconds from an X timestamp, or None."""
    if not value:
        return None
    try:
        return int(datetime.strptime(str(value), _CREATED).timestamp())
    except (ValueError, TypeError):
        return None


def _int(value: Any) -> int | None:
    """A count, or None. X sends real numbers, but a missing field is not a 0 -
    "no data" and "none" are different answers on a profile card."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _first_url(entity: Any) -> str | None:
    """The expanded url out of an `entities` block.

    X stores the website as a t.co redirect and keeps the real destination in
    the entities. The t.co link is not wrong, it is just useless on a card -
    it tells the reader nothing about where it goes until they click it.
    """
    if not isinstance(entity, dict):
        return None
    groups = entity.get("url") or {}
    urls = groups.get("urls") if isinstance(groups, dict) else None
    for u in urls or []:
        if isinstance(u, dict) and u.get("expanded_url"):
            return str(u["expanded_url"])
    return None


def avatar_big(url: str | None) -> str | None:
    """The same avatar at 400x400, for the profile card.

    The list of followings wants the small one - a hundred 400px pictures is
    four megabytes for pictures drawn at 24 - and the card wants the big one.
    Both come from the same url, so this swaps the suffix rather than asking X
    twice.
    """
    if not url:
        return None
    return _AVATAR_SIZE.sub("_400x400.\\2", url)


def parse_profile(raw: dict[str, Any]) -> dict[str, Any] | None:
    """The GraphQL `UserByScreenName` result, flat.

    `raw` is the whole response; the `data.user.result` walk is done here so a
    caller cannot forget it and hand over a response that parses to nothing.
    """
    res = (((raw or {}).get("data") or {}).get("user") or {}).get("result") or {}
    if not res:
        return None

    core = res.get("core") or {}
    bio_block = res.get("profile_bio") or {}
    rel = res.get("relationship_counts") or {}
    counts = res.get("tweet_counts") or {}

    handle = core.get("screen_name") or ""
    uid = res.get("rest_id") or ""
    if not handle or not uid:
        return None

    website = _first_url(bio_block.get("entities")) or (
        (res.get("website") or {}).get("url") if isinstance(res.get("website"), dict) else None)

    return {
        "id": str(uid),
        "handle": handle.lower(),
        "name": core.get("name"),
        "bio": bio_block.get("description"),
        "followers": _int(rel.get("followers")),
        "following": _int(rel.get("following")),
        # The new shape has no statuses_count; the tweet counter is the same
        # number under a different name, and it is the one the card shows.
        "statuses": _int(counts.get("tweets")),
        "listed": None,
        "location": (res.get("location") or {}).get("location")
                    if isinstance(res.get("location"), dict) else None,
        "website": website,
        "avatar": (res.get("avatar") or {}).get("image_url")
                  if isinstance(res.get("avatar"), dict) else None,
        "banner": (res.get("banner") or {}).get("image_url")
                  if isinstance(res.get("banner"), dict) else None,
        "verified": bool((res.get("verification") or {}).get("verified")),
        "blue": bool(res.get("is_blue_verified")),
        "protected": bool((res.get("privacy") or {}).get("protected")),
        "created_ts": _ts(core.get("created_at")),
    }


def parse_list_user(u: dict[str, Any]) -> dict[str, Any] | None:
    """One user object out of a REST 1.1 list page, flat.

    Returns exactly the shape parse_profile does. The old shape is more
    generous than the new one - it carries the banner, the listed count and the
    description for every user in the list - so nothing here is a fallback for
    a missing field; it is simply where those fields live on this side.
    """
    if not u:
        return None
    handle = u.get("screen_name") or ""
    uid = u.get("id_str") or (str(u["id"]) if u.get("id") is not None else "")
    if not handle or not uid:
        return None

    return {
        "id": str(uid),
        "handle": handle.lower(),
        "name": u.get("name"),
        "bio": u.get("description"),
        "followers": _int(u.get("followers_count")),
        "following": _int(u.get("friends_count")),
        "statuses": _int(u.get("statuses_count")),
        "listed": _int(u.get("listed_count")),
        "location": u.get("location"),
        "website": _first_url(u.get("entities")) or u.get("url"),
        "avatar": u.get("profile_image_url_https") or u.get("profile_image_url"),
        "banner": u.get("profile_banner_url"),
        # `verified` is the old blue check and `ext_is_blue_verified` is what
        # it became when the check was sold; both are reported, because a card
        # that conflates them says a paying account is a notable one.
        "verified": bool(u.get("verified")),
        "blue": bool(u.get("ext_is_blue_verified")),
        "protected": bool(u.get("protected")),
        "created_ts": _ts(u.get("created_at")),
        # Not shown, but the difference between "has no picture" and "has not
        # set one" - the egg - and the only place that is stated.
        "default_avatar": bool(u.get("default_profile_image")),
    }


def parse_users_page(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """A REST 1.1 list page -> (users, next cursor).

    `total_count` is deliberately not returned even though the key is in the
    response: measured on a live page, it is None. The number of followings an
    account has comes from its profile, and a caller that trusted this field
    would print "of None" on every list.
    """
    users = []
    for u in (raw or {}).get("users") or []:
        info = parse_list_user(u)
        if info:
            users.append(info)
    cursor = (raw or {}).get("next_cursor_str")
    if cursor is None:
        cursor = str((raw or {}).get("next_cursor", "0"))
    # "0" and "" both mean there is no next page; X uses both.
    return users, (cursor or "0")
