"""The two X profile shapes, and the table that answers "who launched under this".

Both halves of this are things that fail quietly if they are wrong. A parser
reading the wrong container returns a profile of nulls, which looks like an
account with no followers rather than like a bug. A side table that falls behind
answers "nothing was launched under this handle" for every handle it has not
reached, which is exactly what somebody deciding whether to launch wants to hear
and exactly what is not true.

So the cases below are the ones where being wrong is invisible:

1. The new profile shape, which no longer has the `legacy` object the project's
   own parser reads - every field it wants comes back None.
2. The old shape, which the list endpoint still returns, parsed into the same
   flat dict.
3. The triggers that keep token_handles current, including the two ways they
   must NOT write: prose in the twitter column, and a reserved x path.
4. Paging a followings list while it is still being written, which is the case
   the key (owner_id, ord) exists for and which silently repeats and skips
   people under the obvious key.
5. A walk that stopped early, which must not read as a complete list.
6. The two counts a followings list can be ordered by. They arrive from a second
   query over the same rows as the first, which is the shape that drifts: the
   number in the row and the number the row was sorted by are supposed to be one
   value, and nothing but a test says they still are. Plus the tie-breaking,
   because an order that is not total repeats and skips people as the list is
   scrolled - the same fault as case 4, arriving by another road.

No network: the JSON below is what X returned, copied in. No database of the
dashboard's is touched - the path is pointed at a temporary file before store is
imported, the way test_trades.py does it.
"""
import os
import sys
import tempfile

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config as C

# Before the first connection, which is lazy: store.conn() reads this.
C.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pons-test-x-"), "test.db")

import handles
import store
import xprofile

store.init()

BOT_DIR = C.XCLIENT_DIR  # the neighbouring bot project, for the one import below

bad = 0


def check(name, ok, extra=""):
    global bad
    print("%-5s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   <- " + str(extra)))
    if not ok:
        bad += 1


# ------------------------------------------------ the two shapes of a profile

# Copied from the live response for @poly_enjoyer, with the long tail of
# containers X also sends trimmed. The point of interest is what is NOT here:
# there is no `legacy`.
NEW_SHAPE = {
    "data": {"user": {"result": {
        "__typename": "User",
        "rest_id": "1540806109744840704",
        "is_blue_verified": False,
        "profile_image_shape": "Circle",
        "core": {"created_at": "Sat Jun 25 21:16:20 +0000 2022",
                 "name": "OMEGA", "screen_name": "poly_enjoyer"},
        "avatar": {"image_url":
                   "https://pbs.twimg.com/profile_images/2033934087249915904/MBRZTdAK_normal.jpg"},
        "banner": {"image_url":
                   "https://pbs.twimg.com/profile_banners/1540806109744840704/1773762651"},
        "profile_bio": {
            "description": "building in AI/web3 | breaking dev news",
            "entities": {"description": {}, "url": {"urls": [{
                "display_url": "polymarket.com/?via=omega",
                "expanded_url": "https://polymarket.com?via=omega",
                "url": "https://t.co/qkRcr9l7vI"}]}}},
        "relationship_counts": {"followers": 1943, "following": 815},
        "verification": {"verified": False},
        "location": {"location": "EVM"},
        "website": {"url": "https://t.co/qkRcr9l7vI"},
        "privacy": {"protected": False},
        "tweet_counts": {"media_tweets": 199, "tweets": 5604},
    }}}
}

old = xprofile.parse_profile(NEW_SHAPE)
check("new shape: parses at all", old is not None, old)
check("new shape: id", old and old["id"] == "1540806109744840704", old)
check("new shape: handle lowercased", old and old["handle"] == "poly_enjoyer", old)
check("new shape: followers from relationship_counts",
      old and old["followers"] == 1943, old and old["followers"])
check("new shape: following from relationship_counts",
      old and old["following"] == 815, old and old["following"])
check("new shape: statuses from tweet_counts",
      old and old["statuses"] == 5604, old and old["statuses"])
check("new shape: bio from profile_bio",
      old and old["bio"] == "building in AI/web3 | breaking dev news", old and old["bio"])
check("new shape: avatar", old and old["avatar"].endswith("_normal.jpg"), old)
check("new shape: banner", old and "profile_banners" in (old["banner"] or ""), old)
check("new shape: website is the expanded url, not the t.co redirect",
      old and old["website"] == "https://polymarket.com?via=omega", old and old["website"])
check("new shape: location", old and old["location"] == "EVM", old)
check("new shape: not protected", old and old["protected"] is False, old)
check("new shape: created_ts",
      old and old["created_ts"] == 1656191780, old and old["created_ts"])

# The failure this whole module exists for. The bot's own parser - which is not
# touched by this work - reads `core` for the name and `legacy` for every count,
# and the answer above has no `legacy` at all. So it returns a user that exists
# and has no followers, no bio and no location, which is exactly the card the
# user asked for coming out blank. Imported from the bot rather than reproduced,
# because a copy of somebody else's parser proves nothing about their parser.
LEGACY_KEYS = ("followers_count", "friends_count", "statuses_count",
               "description", "location", "protected")
try:
    sys.path.insert(0, BOT_DIR)
    from xclient import parsers as bot_parsers
    bot = bot_parsers.parse_user_result(NEW_SHAPE)
    nulls = [k for k in LEGACY_KEYS if bot and bot.get(k) is None]
    check("the bot's own parser returns nulls on the new profile shape",
          bot is not None and len(nulls) >= 4,
          (bot or {}).get("followers_count"))
    check("and it has no avatar or banner field at all",
          not any(k in (bot or {}) for k in ("avatar", "banner", "profile_image_url")),
          sorted(bot or {}))
except ImportError as e:
    # The parser under test belongs to a neighbouring project, pointed at by
    # PONS_XCLIENT_DIR, and this repository does not carry it. Reported as a
    # failure it would be the one red line in a clone whose own cases are all
    # green, and it would read as this project being broken rather than as an
    # optional dependency being absent. Set PONS_XCLIENT_DIR to that project
    # and the two cases below run for real.
    print("skip  the bot's own parser - no xclient on the path (%s)" % e)

# The old shape, from the live friends/list.json page.
OLD_SHAPE = {
    "id": 2093138700477640704, "id_str": "2093138700477640704",
    "screen_name": "YieldFields_RH", "name": "Yield Fields",
    "description": "Coming soon to Robinhood Chain!",
    "followers_count": 22954, "friends_count": 1, "statuses_count": 15,
    "listed_count": 103, "favourites_count": 11,
    "location": "Rolling in the yield",
    "url": "https://t.co/MKICPCo5NX",
    "profile_image_url_https":
        "https://pbs.twimg.com/profile_images/2094453040908562432/9gBCqeh7_normal.jpg",
    "profile_banner_url":
        "https://pbs.twimg.com/profile_banners/2093138700477640704/1788190425",
    "default_profile_image": False,
    "verified": False, "ext_is_blue_verified": True, "protected": False,
    "created_at": "Fri Aug 28 00:49:42 +0000 2026",
    "entities": {"url": {"urls": [{
        "url": "https://t.co/MKICPCo5NX",
        "expanded_url": "https://yieldfields.fun/",
        "display_url": "yieldfields.fun"}]}, "description": {"urls": []}},
}

lu = xprofile.parse_list_user(OLD_SHAPE)
check("old shape: parses", lu is not None, lu)
check("old shape: id from id_str", lu and lu["id"] == "2093138700477640704", lu)
check("old shape: followers", lu and lu["followers"] == 22954, lu and lu["followers"])
check("old shape: following from friends_count",
      lu and lu["following"] == 1, lu and lu["following"])
check("old shape: listed", lu and lu["listed"] == 103, lu and lu["listed"])
check("old shape: website is the expanded url",
      lu and lu["website"] == "https://yieldfields.fun/", lu and lu["website"])
check("old shape: avatar", lu and lu["avatar"].endswith("_normal.jpg"), lu)
check("old shape: banner", lu and "profile_banners" in (lu["banner"] or ""), lu)
check("old shape: blue, not verified",
      lu and lu["blue"] is True and lu["verified"] is False, lu)

# The two shapes must agree on their keys, or a caller has to know which door
# its profile came through - which is the thing this module exists to prevent.
same = set(old) - set(lu)
check("the two shapes produce the same keys", not same, same)

# A page and its cursor.
page_users, cursor = xprofile.parse_users_page(
    {"users": [OLD_SHAPE], "next_cursor_str": "1869777531836883090",
     "total_count": None})
check("a page parses to its users", len(page_users) == 1, page_users)
check("a page carries its cursor", cursor == "1869777531836883090", cursor)
check("no next page reads as 0",
      xprofile.parse_users_page({"users": [], "next_cursor": 0})[1] == "0")

# ------------------------------------------------------------- avatar sizes

check("avatar_big swaps _normal for _400x400",
      xprofile.avatar_big("https://pbs.twimg.com/profile_images/1/A_normal.jpg")
      == "https://pbs.twimg.com/profile_images/1/A_400x400.jpg")
check("avatar_big leaves an already-big url alone",
      xprofile.avatar_big("https://pbs.twimg.com/profile_images/1/A_400x400.jpg")
      == "https://pbs.twimg.com/profile_images/1/A_400x400.jpg")
check("avatar_big passes None through", xprofile.avatar_big(None) is None)
check("avatar_big leaves a banner url alone",
      xprofile.avatar_big("https://pbs.twimg.com/profile_banners/1/1773762651")
      == "https://pbs.twimg.com/profile_banners/1/1773762651")

# ----------------------------------------------------------- the token_handles
# Triggers, which is the riskiest new machinery here: they fire on writes made
# from anywhere, so what has to be shown is that they fire on ALL of them and
# on none of the wrong ones.

T = "0x" + "aa" * 20


def token(addr, twitter):
    store.conn().execute(
        "INSERT OR REPLACE INTO tokens(address, curve, launch_block, launch_ts, "
        "twitter) VALUES(?,?,?,?,?)", (addr, "0x" + "cc" * 20, 1, 1_700_000_000,
                                       twitter))
    store.conn().commit()


def handle_of(addr):
    row = store.conn().execute(
        "SELECT handle FROM token_handles WHERE address=?", (addr,)).fetchone()
    return row["handle"] if row else None


print()
token("0x" + "01" * 20, "https://twitter.com/Foo?ref=1")
check("trigger: a url with a query stores the bare handle",
      handle_of("0x" + "01" * 20) == "foo", handle_of("0x" + "01" * 20))
token("0x" + "02" * 20, "@Bar")
check("trigger: an @handle is stored lowercased",
      handle_of("0x" + "02" * 20) == "bar", handle_of("0x" + "02" * 20))
token("0x" + "03" * 20, "follow us on telegram")
check("trigger: prose is not stored as a handle",
      handle_of("0x" + "03" * 20) is None, handle_of("0x" + "03" * 20))
token("0x" + "04" * 20, "https://x.com/i/search")
check("trigger: a reserved path normalises and is stored (the caller decides)",
      handle_of("0x" + "04" * 20) == "i", handle_of("0x" + "04" * 20))
token("0x" + "05" * 20, None)
check("trigger: a null twitter stores nothing",
      handle_of("0x" + "05" * 20) is None, handle_of("0x" + "05" * 20))

# Re-enrichment: the handle a token claims can change, and the old row must not
# survive next to the new one.
store.conn().execute("UPDATE tokens SET twitter=? WHERE address=?",
                     ("https://x.com/NewName", "0x" + "01" * 20))
store.conn().commit()
check("trigger: re-enrichment replaces the handle",
      handle_of("0x" + "01" * 20) == "newname", handle_of("0x" + "01" * 20))
check("trigger: re-enrichment leaves no row behind",
      store.conn().execute("SELECT COUNT(*) FROM token_handles WHERE address=?",
                           ("0x" + "01" * 20,)).fetchone()[0] == 1)

# Clearing the field has to clear the row too, or the token keeps claiming a
# handle it no longer names.
store.conn().execute("UPDATE tokens SET twitter=NULL WHERE address=?",
                     ("0x" + "02" * 20,))
store.conn().commit()
check("trigger: an emptied twitter clears the row",
      handle_of("0x" + "02" * 20) is None, handle_of("0x" + "02" * 20))

store.conn().execute("DELETE FROM tokens WHERE address=?", ("0x" + "04" * 20,))
store.conn().commit()
check("trigger: deleting the token deletes the row",
      handle_of("0x" + "04" * 20) is None, handle_of("0x" + "04" * 20))

# The price loop rewrites mcap_usd on twenty thousand rows a tick. If the
# trigger fired on that, the whole design is wrong - it must not.
n_before = store.conn().execute("SELECT COUNT(*) FROM token_handles").fetchone()[0]
store.conn().execute("UPDATE tokens SET mcap_usd = 1.0")
store.conn().commit()
check("trigger: a price update does not touch token_handles",
      store.conn().execute("SELECT COUNT(*) FROM token_handles").fetchone()[0] == n_before)

# --------------------------------------------------------------- handles_for

store.conn().execute("UPDATE tokens SET twitter=NULL")
store.conn().commit()
for i in range(1, 6):
    token("0x%040x" % (100 + i), "@shared")
token("0x%040x" % 200, "@solo")
for i in range(3):
    store.conn().execute(
        "UPDATE tokens SET mcap_usd=? WHERE address=?",
        (float(i + 1) * 1000.0, "0x%040x" % (100 + i + 1)))
store.conn().commit()

got = store.handles_for(["shared", "@solo", "nobody", "SHARED"])
check("handles_for: counts every launch under a handle",
      got.get("shared", {}).get("launches") == 5, got.get("shared"))
check("handles_for: a handle nobody launched is simply absent",
      "nobody" not in got, sorted(got))
check("handles_for: the handle is normalised on the way in",
      "solo" in got and got["solo"]["launches"] == 1, got)
check("handles_for: the top token is the one with the highest mcap",
      got.get("shared", {}).get("top_mcap_usd") == 3000.0, got.get("shared"))
check("handles_for: the top token's symbol travels with it",
      got.get("shared", {}).get("top_address") == "0x%040x" % 103,
      got.get("shared"))
check("handles_for: an empty ask is an empty answer",
      store.handles_for([]) == {} and store.handles_for([""]) == {})

# The property that lets both lookups normalise their question: every handle in
# the table is something norm produced, and norm does not change its own output.
# Without this, "normalise on the way in" could quietly miss a row that is
# there - which is the same silent-nothing the whole table exists to avoid.
check("norm is idempotent, so a normalised lookup cannot miss a stored row",
      all(handles.norm(handles.norm(x)) == handles.norm(x)
          for x in ["@Foo", "https://x.com/Bar?ref=1", "solo", "follow us on tg",
                    "x.com/baz/status/1", "i", None, ""]))
check("and every stored handle is already in that form",
      all(handles.norm(r["handle"]) == r["handle"]
          for r in store.conn().execute("SELECT handle FROM token_handles")))

check("handle_tokens takes the forms the tab accepts",
      len(store.handle_tokens("@Shared")) == 5
      and len(store.handle_tokens("https://x.com/shared")) == 5,
      len(store.handle_tokens("@Shared")))

check("handle_tokens returns the indexed answer",
      len(store.handle_tokens("shared")) == 5
      and store.handle_tokens("nobody") == [])

# ------------------------------------------------- paging a list being written
# The bug the (owner_id, ord) key exists for. With the obvious key -
# (owner_id, target_id) - a WITHOUT ROWID table clusters on the key, new rows
# land in the middle, and the second page repeats and skips people. Here the
# write between the two reads is exactly what a running walk does.

OWNER = "900000000000000001"
first = [{"id": str(1000 + i), "handle": "u%d" % i, "followers": i}
         for i in range(100)]
store.x_follows_put(OWNER, first)

page1 = store.x_follows_page(OWNER, 0, 100)
check("paging: the first page is what was written",
      [r["target_id"] for r in page1] == [str(1000 + i) for i in range(100)],
      [r["target_id"] for r in page1][:3])

# The walk is still running: 200 more people arrive, then the next page is read.
second = [{"id": str(2000 + i), "handle": "v%d" % i} for i in range(200)]
store.x_follows_put(OWNER, second)

page2 = store.x_follows_page(OWNER, 100, 100)
check("paging: the page after an insert during the walk does not repeat",
      not ({r["target_id"] for r in page1} & {r["target_id"] for r in page2}),
      sorted({r["target_id"] for r in page1} & {r["target_id"] for r in page2})[:5])
check("paging: and does not skip - every row is reachable exactly once",
      [r["target_id"] for r in page1] + [r["target_id"] for r in page2]
      == [str(1000 + i) for i in range(100)] + [str(2000 + i) for i in range(100)],
      [r["target_id"] for r in page2][:3])
check("paging: the walk's count is what is in the table",
      store.x_follows_count(OWNER) == 300, store.x_follows_count(OWNER))

# Re-walking must extend, not reshuffle: the rows already shown keep their
# place, and somebody new lands after them.
store.x_follows_put(OWNER, first + [{"id": "9999", "handle": "new"}])
check("re-walk: the already-shown rows keep their positions",
      [r["target_id"] for r in store.x_follows_page(OWNER, 0, 3)]
      == ["1000", "1001", "1002"],
      [r["target_id"] for r in store.x_follows_page(OWNER, 0, 3)])
check("re-walk: the new person is appended, not inserted",
      store.x_follows_page(OWNER, 300, 5)[0]["target_id"] == "9999",
      store.x_follows_page(OWNER, 300, 5))
check("re-walk: no duplicates were created",
      store.x_follows_count(OWNER) == 301, store.x_follows_count(OWNER))

# A page written by one walk is visible to a reader before the walk ends.
# This is the whole point of the batched display.
OWNER2 = "900000000000000002"
store.x_follows_put(OWNER2, [{"id": "1", "handle": "a"}, {"id": "2", "handle": "b"}])
check("a finished page is readable while the walk runs",
      store.x_follows_count(OWNER2) == 2 and store.x_follows_page(OWNER2, 0, 10),
      store.x_follows_count(OWNER2))

# ------------------------------------------------------ counting for a handle
# Two numbers per handle, and both are sort keys on the Handles tab: launches is
# also the pill drawn in the row, deployers is the line under it. Both are on
# the screen because a list ordered by a number nobody can see reads as
# arbitrary, and this is where they are counted.
#
# The deployer column is filled in here because nothing above ever set it.

for i, who in enumerate(["0x" + "d1" * 20, "0x" + "d1" * 20, "0x" + "d2" * 20,
                         "0x" + "d1" * 20, "0x" + "d3" * 20]):
    store.conn().execute("UPDATE tokens SET deployer=? WHERE address=?",
                         (who, "0x%040x" % (101 + i)))
store.conn().execute("UPDATE tokens SET deployer=? WHERE address=?",
                     ("0x" + "d9" * 20, "0x%040x" % 200))
store.conn().commit()

st = store.handle_stats(["@Shared", "solo", "nobody", "", None])
check("handle_stats: launches and deployers for one handle",
      st.get("shared") == {"launches": 5, "deployers": 3}, st.get("shared"))
check("handle_stats: one launch from one wallet",
      st.get("solo") == {"launches": 1, "deployers": 1}, st.get("solo"))
check("handle_stats: a handle nobody launched is absent, not zero",
      "nobody" not in st, sorted(st))
check("handle_stats: an empty ask is an empty answer",
      store.handle_stats([]) == {} and store.handle_stats([""]) == {},
      store.handle_stats([""]))

# Why the launches count is asked for in a second query at all, and the danger
# of doing it: two queries over the same rows answer the same question, and two
# queries over the same rows drift. So they are compared here rather than
# assumed equal - this is the test that would catch the two FROM clauses
# diverging, which is the only way the pill and the sort key can disagree.
asked = ["shared", "solo", "@Shared", "nobody", "newname", ""]
stats, tops = store.handle_stats(asked), store.handles_for(asked)
check("handle_stats and handles_for count the same launches",
      {k: v["launches"] for k, v in stats.items()}
      == {k: v["launches"] for k, v in tops.items()}
      and stats.get("shared", {}).get("launches") == 5, (stats, tops))

# --------------------------------------------------------- ordering the list
# The order is built over every row this account follows and only then cut into
# pages, so what is checked is the whole list: an order that is right for the
# first page and wrong for the third is the failure this exists to prevent.

ORD = "900000000000000009"
store.x_follows_put(ORD, [
    {"id": "7001", "handle": "shared", "followers": 10},
    {"id": "7002", "handle": "solo", "followers": 900},
    {"id": "7003", "handle": "nobody", "followers": 0},
    {"id": "7004", "handle": "shared", "followers": 5000},
    {"id": "7005", "handle": "quiet", "followers": None},
])


def ids_of(owner, sort="", sort2=""):
    """The list as the page would draw it: the order first, then the rows."""
    return [r["target_id"] for r in
            store.x_follows_rows_by_ord(
                owner, store.x_follows_order(owner, sort, sort2))]


check("ordering: no key at all is the order they arrived in",
      ids_of(ORD) == ["7001", "7002", "7003", "7004", "7005"], ids_of(ORD))
check("ordering: followers puts the largest audience first",
      ids_of(ORD, "followers_desc") == ["7004", "7002", "7001", "7003", "7005"],
      ids_of(ORD, "followers_desc"))
check("ordering: and the smallest first when it is reversed",
      ids_of(ORD, "followers_asc") == ["7003", "7005", "7001", "7002", "7004"],
      ids_of(ORD, "followers_asc"))
check("ordering: launches puts the handle claimed by most first",
      ids_of(ORD, "launches_desc") == ["7001", "7004", "7002", "7003", "7005"],
      ids_of(ORD, "launches_desc"))
check("ordering: deployers counts wallets, not launches",
      ids_of(ORD, "deployers_desc") == ["7001", "7004", "7002", "7003", "7005"],
      ids_of(ORD, "deployers_desc"))

# Two keys, and the second one only decides where the first one ties. Both rows
# for "shared" have five launches; which of them is first is the audience's
# question, and reversing the direction of the second key answers it the other
# way without touching the first.
two_down = ids_of(ORD, "launches_desc", "followers_desc")
two_up = ids_of(ORD, "launches_desc", "followers_asc")
check("two keys: the second one breaks the first one's ties",
      two_down == ["7004", "7001", "7002", "7003", "7005"], two_down)
check("two keys: which is a different list from the first key alone",
      two_down != ids_of(ORD, "launches_desc"), two_down)
check("two keys: reversing the second key leaves the first one alone",
      two_up == ["7001", "7004", "7002", "7003", "7005"], two_up)

check("ordering: the same question twice gives the same list",
      store.x_follows_order(ORD, "launches_desc")
      == store.x_follows_order(ORD, "launches_desc"),
      store.x_follows_order(ORD, "launches_desc"))

# Rows that tie on every key still have to come back in one fixed order.
# Without that, the hundredth row of one request is not the hundredth of the
# next, so scrolling past it repeats some people and skips others - the same
# failure the (owner_id, ord) key exists to prevent, arriving by another road.
tied = ids_of(ORD, "launches_desc")[-2:]
check("ordering: rows tied on every key keep the order they arrived in",
      tied == ["7003", "7005"], tied)
check("ordering: an unknown key is arrival order, not an error",
      ids_of(ORD, "nonsense", "alsononsense") == ids_of(ORD),
      ids_of(ORD, "nonsense", "alsononsense"))
check("ordering: an unknown key does not discard the one next to it",
      ids_of(ORD, "nonsense", "followers_desc") == ids_of(ORD, "followers_desc"),
      ids_of(ORD, "nonsense", "followers_desc"))

# ord carries no meaning on its own: both lists below have a row at ord 0, and
# each has to be given its own.
check("rows_by_ord returns what was asked for, in the order it was asked for",
      [r["target_id"] for r in store.x_follows_rows_by_ord(ORD, [3, 0, 4])]
      == ["7004", "7001", "7005"],
      store.x_follows_rows_by_ord(ORD, [3, 0, 4]))
check("rows_by_ord: an ord this owner does not have is left out, not filled in",
      [r["target_id"] for r in store.x_follows_rows_by_ord(ORD, [0, 800, 4])]
      == ["7001", "7005"],
      store.x_follows_rows_by_ord(ORD, [0, 800, 4]))
check("rows_by_ord: ord 0 belongs to the owner it was asked about",
      store.x_follows_rows_by_ord(ORD, [0])[0]["target_id"] == "7001"
      and store.x_follows_rows_by_ord(OWNER, [0])[0]["target_id"] == "1000",
      store.x_follows_rows_by_ord(OWNER, [0]))
check("rows_by_ord: an empty ask is an empty answer",
      store.x_follows_rows_by_ord(ORD, []) == [])

# Somebody the walk recorded whose profile never arrived. The two go in as one
# commit, so this is a row from before that was true - and it stays in the list
# either way, because dropping a row the database holds is the one thing this
# table must not do. It sorts as zero on every key, which is what it is.
store.conn().execute("INSERT INTO x_follows(owner_id, ord, target_id, seen_at) "
                     "VALUES(?,?,?,?)", (ORD, 900, "ghost", 1_700_000_000))
store.conn().commit()
ghost = ids_of(ORD, "followers_desc")
check("ordering: a follow with no stored profile sorts as zero, and stays",
      ghost[-1] == "ghost" and len(ghost) == 6, ghost)

# ------------------------------------------------------- a walk that stopped
# The honesty case: half a list must not read as a whole one.

store.x_list_put(OWNER, handle="someone", state="running", pages=2, users=200,
                 total=5000, started_at=1_700_000_000)
running = store.x_list_get(OWNER)
check("x_lists: a running walk says how far it has got",
      running and running["users"] == 200 and running["total"] == 5000
      and running["state"] == "running", running)
check("x_lists: 200 of 5000 reads as incomplete",
      running and running["users"] < running["total"])

store.x_list_put(OWNER, state="error", error="429 after 300")
stopped = store.x_list_get(OWNER)
check("x_lists: a stopped walk keeps its partial count",
      stopped and stopped["state"] == "error" and stopped["users"] == 200, stopped)
check("x_lists: and its reason", stopped and stopped["error"] == "429 after 300", stopped)

store.x_list_put(OWNER, state="ok", done_at=1_700_000_100, users=5000, total=5000)
check("x_lists: a finished walk has nothing left over",
      store.x_list_get(OWNER)["users"] == store.x_list_get(OWNER)["total"],
      store.x_list_get(OWNER))

# A walk is a thread of the server process, so one that was running when the
# process went away is never marked ended by anyone. The row keeps saying
# "running", and on the page that is a list which will never grow and sort
# controls waiting for an ending that cannot arrive. Startup is the one moment
# the answer is certain.
store.x_list_put(OWNER, state="running", users=3000, total=5000)
store.x_list_put(OWNER2, handle="other", state="running", users=10, total=99)
stuck = store.x_lists_unstick(now=1_700_000_200)
check("x_lists_unstick: every walk left running is counted",
      stuck == 2, stuck)
check("x_lists_unstick: and marked stopped, not ok",
      store.x_list_get(OWNER)["state"] == "stopped"
      and store.x_list_get(OWNER2)["state"] == "stopped",
      (store.x_list_get(OWNER), store.x_list_get(OWNER2)))
check("x_lists_unstick: with the reason recorded rather than the row quietly closed",
      "restarted" in (store.x_list_get(OWNER)["error"] or "")
      and store.x_list_get(OWNER)["done_at"] == 1_700_000_200,
      store.x_list_get(OWNER))
check("x_lists_unstick: the partial count survives, so it still reads as partial",
      store.x_list_get(OWNER)["users"] == 3000
      and store.x_list_get(OWNER)["users"] < store.x_list_get(OWNER)["total"],
      store.x_list_get(OWNER))
check("x_lists_unstick: a second run has nothing to do",
      store.x_lists_unstick() == 0, store.x_lists_unstick())
store.x_list_put(OWNER, state="ok", error="", users=5000, total=5000)
check("x_lists_unstick: a walk that ended leaves nothing to unstick",
      store.x_lists_unstick() == 0 and store.x_list_get(OWNER)["state"] == "ok",
      store.x_list_get(OWNER))

# --------------------------------------------------------------- stored users

store.x_user_put({"id": "555", "handle": "MixedCase", "name": "N", "followers": 7,
                  "avatar": "https://x/a_normal.jpg", "blue": True})
u = store.x_user_get("mixedcase")
check("x_user_get finds a handle regardless of case", u and u["id"] == "555", u)
check("x_user_get lowercases what it stores", u and u["handle"] == "mixedcase", u)
check("x_user_get by id", store.x_user_get(user_id="555")["name"] == "N")
check("x_user_get on an unknown handle is None",
      store.x_user_get("nobody-at-all") is None)
store.x_user_put({"id": "555", "handle": "mixedcase", "name": "New", "followers": 8})
check("x_user_put replaces rather than accumulating",
      store.x_user_get("mixedcase")["followers"] == 8
      and store.conn().execute("SELECT COUNT(*) FROM x_users WHERE id='555'"
                               ).fetchone()[0] == 1)

print()
print("FAILURES: %d" % bad if bad else "all x cases passed")
sys.exit(1 if bad else 0)
