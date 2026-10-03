"""A synthetic database, so the dashboard can be looked at with no node.

The screenshots and the recording in `docs/` were taken against the output of
this script, and that is the whole reason it exists: a real database holds real
wallets, real launches and a real trading history, and none of that belongs in
a public repository or in a picture of one. Everything below is invented, and
it is invented in a way that is visible at a glance - every address carries a
readable tag (`0x7000...` tokens, `0xc0de...` curves, `0xdead...` deployers,
`0xbeef...` buyers, `0xcafe...` the watched wallets)
so that nothing here can be mistaken for a chain fact by anybody who looks.

It is deterministic: the same run produces the same database, seed 7, no clock
reads outside `now`. A demo that changes every time is a demo that cannot be
compared against the previous picture of it.

    python tools/seed_demo.py                 # writes data/pons.db, or refuses
    python tools/seed_demo.py --out /tmp/d.db # somewhere else
    python tools/seed_demo.py --force         # overwrite a database that has rows

The curve numbers are not made up. Each token gets a real reserve, and the
price, the market cap and the progress are then computed by `store.derive()`
- the same function the indexer's own path uses - so the market caps on screen
obey the same constant-product rule the chain does and the picture is a
possible one. A token whose numbers contradict each other is the sort of thing
that reads as a bug in the dashboard.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import store  # noqa: E402  (the path has to be set first)
import keysafe  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_utils import to_checksum_address  # noqa: E402

TOKENS = 48
ETH_USD = 3000.0

# 1e27, the supply every curve on this launchpad starts with.
SUPPLY = 10 ** 27
PHANTOM = 1_680_000_000_000_000_000           # 1.68 quote, virtual
THRESHOLD = 4_200_000_000_000_000_000         # 4.2 quote, graduation
NATIVE_QUOTE = "0x0000000000000000000000000000000000000000"
USDG_QUOTE = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"

# Amounts are stored the way the chain reports them - base units, so a quote
# amount is wei and a token amount is 1e18ths of a token - because that is what
# every reader in this project divides by. A number written in whole units here
# comes out on screen as 1e-18 of itself, which is how a token with nine ETH of
# volume renders as "0.000000000000000009".
ONE = 10 ** 18
# What one base unit of a fresh curve costs, in wei of quote. Only used to turn
# a quote amount into a token amount for the rows that carry both.
PRICE_WEI = 2e-8

NAMES = [
    ("Hooded Crow", "CROW"), ("Ticker Tape", "TAPE"), ("Maple Index", "MAPLE"),
    ("Night Bus", "NBUS"), ("Copper Wire", "COPR"), ("Paper Kite", "KITE"),
    ("Salt Flats", "SALT"), ("Velvet Rope", "VELV"), ("Tin Roof", "TINR"),
    ("Loose Change", "CHNG"), ("Iron Pelican", "PELI"), ("Static Bloom", "STBL"),
    ("Dry Creek", "DRYC"), ("Brass Lamp", "BRSS"), ("Wet Paint", "WETP"),
    ("Long Weekend", "LWD"), ("Green Room", "GRNM"), ("Slow Radio", "SLOW"),
    ("Old Growth", "OLDG"), ("Cold Brew", "CLDB"), ("Neon Sign", "NEON"),
    ("Quiet Hours", "QUIET"), ("Red Shift", "REDS"), ("Blue Hour", "BLUH"),
    ("Cardboard Box", "CBOX"), ("Fog Line", "FOGL"), ("Gravel Pit", "GRVL"),
    ("Paper Trail", "PTRL"), ("Wild Mint", "MINT"), ("Deep Field", "DEEP"),
    ("Rust Belt", "RUST"), ("Flat White", "FLAT"), ("Open Tab", "OTAB"),
    ("Glass Onion", "ONION"), ("Match Strike", "MTCH"), ("Low Tide", "TIDE"),
    ("Bright Loft", "LOFT"), ("Short Wave", "SHRT"), ("Dust Devil", "DUST"),
    ("Sunday Paper", "SUNP"), ("Borrowed Time", "BRRW"), ("Half Pipe", "HALF"),
    ("Second Wind", "WIND"), ("Iron Filings", "FIL"), ("Marble Arch", "MRBL"),
    ("Chalk Line", "CHLK"), ("Bear Market", "BEAR"), ("Tiny Dancer", "TINY"),
]

BLURBS = [
    "A coin for the walk home.",
    "Community token, no roadmap, no promises.",
    "Launched on a whim. Holding anyway.",
    "For the people who read the docs.",
    "Two friends, one curve, zero plan.",
    "Slow money, loud community.",
    "Made at 3am, shipped at 4.",
    "Fair launch. No presale, no allocation.",
]

HANDLES = [
    "hoodcrow", "tickertape", "mapleindex", "nightbus", "copperwire",
    "paperkite", "saltflats", "velvetrope", "tinroof", "loosechange",
    "ironpelican", "staticbloom",
]

# Most handles belong to one launch. Two are shared on purpose, because the
# checker's whole job is telling "the account launched once" from "the handle
# is being farmed", and a dataset where every handle has one claim and one
# deployer cannot show the difference. The deployer cycles every nine tokens,
# so these indexes land on four different wallets: hoodcrow comes back as four
# launches from four deployers, which is the answer the tab exists to give.
SHARED = {
    1: "hoodcrow", 3: "hoodcrow", 5: "hoodcrow", 7: "hoodcrow",
    2: "tickertape", 8: "tickertape",
}

WALLETS = [
    ("Main", to_checksum_address("0xcafe" + "%036x" % 1)),
    ("Rotation A", to_checksum_address("0xcafe" + "%036x" % 2)),
    ("Rotation B", to_checksum_address("0xcafe" + "%036x" % 3)),
    ("Cold", to_checksum_address("0xcafe" + "%036x" % 4)),
]

# Three of those wallets launch a token of their own. Without it the Wallets
# tab is four addresses with nothing underneath them, and the wallet lookup it
# opens answers every question with an empty table - which is a screen that
# says nothing about whether the tab works.
WALLET_LAUNCHES = (9, 18, 27)
WALLET_ADDRS = [w[1] for w in WALLETS]

# Two launches belong to the vault keys further down, for the same reason and
# against a column that has the same failure mode: a key whose address has
# never launched anything draws a zero, and a column of zeroes on the one tab
# that exists to manage keys reads as a tab that does not work.
KEY_LAUNCHES = {12: 0, 30: 1}   # token index -> which key in KEY_WALLETS

# Two keys for the vault, and the passphrase that opens them. Both are printed
# in the seeder on purpose: the addresses hold nothing, so the only thing a
# reader can do with them is unlock the demo and watch the tab work.
DEMO_PASSPHRASE = "demo-passphrase"
KEY_WALLETS = [("Main hot", 0xD0D0DEAD00000000000000000000000000000000000000000000000000000001),
               ("Rotation key", 0xD0D0DEAD00000000000000000000000000000000000000000000000000000002)]
# Derived from the keys, not written down beside them, because the address the
# Wallets tab matches a launch against is the one the server derives from the
# stored key. An address typed in by hand would disagree with it the first time
# anything about the derivation changed, and the column would go quietly to 0.
KEY_ADDRS = [Account.from_key("0x" + "%064x" % n).address for _, n in KEY_WALLETS]

COPY_PLANS = [
    ("active", "CROW"),
    ("active", "SALT"),
    ("paused", "NEON"),
]


def tagged(tag: str, n: int) -> str:
    """A 20-byte address that says what it is, in the casing the app stores.

    Four readable bytes then the counter zero-padded to the rest, so a
    screenshot says `0x7000...0021` and nobody has to wonder whose wallet
    that is.

    The checksum is not decoration. Every address the indexer writes comes off
    the chain in EIP-55, and the lookups compare addresses as strings, so a
    table of lowercase addresses answers `wallet/0xcafe...` with an empty list
    however much is stored under it. A seed that skips the checksum is a seed
    that produces a dashboard where half the tabs are empty for a reason that
    is not visible on screen. A few letters come back uppercased and the tag
    still reads.
    """
    return to_checksum_address("0x" + tag + "%036x" % n)


def curve_state(real: int) -> tuple[int, int]:
    """(sellable, reserved) for a real reserve, on the curve's own k.

    virtualQuote * virtualToken = phantomQuote * totalSupply is the invariant
    the launchpad keeps, so a real reserve fixes how many tokens have left the
    curve and nothing else has to be guessed.
    """
    virtual_tokens = (PHANTOM * SUPPLY) // (PHANTOM + real)
    sold = SUPPLY - virtual_tokens
    return SUPPLY - sold, 0


def build(out: Path) -> None:
    rnd = random.Random(7)
    now = int(time.time())
    out.parent.mkdir(parents=True, exist_ok=True)
    # Start from nothing. Writing into a database that already holds a demo
    # means every insert lands on top of the previous one, and the first
    # repeated quote asset is a UNIQUE violation three hundred lines in - which
    # is how --force read the one time it was used. The -wal and -shm are the
    # same database, so they go with it.
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(out) + suffix)
        if p.exists():
            p.unlink()
    conn = sqlite3.connect(out)
    # The schema is defined once, in store.py, and this reuses it rather than
    # repeating it: a seeder with its own copy of the DDL is a seeder that
    # stops matching the app the first time a column moves.
    store.init_schema_on(conn)

    block = 41_000_000
    tokens, trades_rows, bucket_rows, point_rows = [], [], [], []
    early_rows, buyer_rows, sniper_rows, position_rows = [], [], [], []
    hits: dict[str, int] = {}

    for i in range(1, TOKENS + 1):
        name, symbol = NAMES[(i - 1) % len(NAMES)]
        address = tagged("7000", i)
        curve = tagged("c0de", i)
        deployer = tagged("dead", 1 + (i % 9))
        if i in WALLET_LAUNCHES:
            deployer = WALLETS[WALLET_LAUNCHES.index(i)][1]
        elif i in KEY_LAUNCHES:
            deployer = KEY_ADDRS[KEY_LAUNCHES[i]]
        age = int(60 * (2 + (i * 37) % 1740))          # 2 minutes to 29 hours
        ts = now - age
        block += rnd.randint(90, 900)

        # Where on the curve. The first six are through it, the next eight are
        # close, the rest are spread down to a token that just opened.
        if i <= 6:
            real = THRESHOLD
        elif i <= 14:
            real = int(THRESHOLD * rnd.uniform(0.72, 0.98))
        elif i <= 28:
            real = int(THRESHOLD * rnd.uniform(0.28, 0.68))
        else:
            real = int(THRESHOLD * rnd.uniform(0.02, 0.25))

        graduated = 1 if i <= 6 else 0
        sellable, reserved = curve_state(real)
        if graduated:
            # A graduated curve is swept: the reserves go to the pool and the
            # curve holds nothing. `derive` reports 100% and no price for it,
            # which is what the row has to say too.
            sellable, reserved, real = 0, 0, 0

        quote_is_usdg = i % 7 == 0
        row = {
            "address": address, "curve": curve, "deployer": deployer,
            "pair_token": USDG_QUOTE if quote_is_usdg else NATIVE_QUOTE,
            "launch_block": block, "log_index": rnd.randint(0, 40),
            "launch_ts": ts, "name": name, "symbol": symbol,
            "description": BLURBS[i % len(BLURBS)],
            "logo": "",
            "twitter": ("https://x.com/%s" % (SHARED.get(i)
                        or HANDLES[(i - 1) % len(HANDLES)])
                        if i <= 12 else ""),
            "telegram": "", "discord": "", "website": "", "farcaster": "",
            "total_supply": str(SUPPLY), "decimals": 18,
            "phantom_quote": str(PHANTOM),
            "real_quote_reserve": str(real),
            "graduation_threshold": str(THRESHOLD),
            "sellable_tokens": str(sellable), "reserved_tokens": str(reserved),
            "graduated": graduated,
            "ready_to_graduate": 1 if not graduated and real / THRESHOLD > 0.995 else 0,
            "is_native_quote": 0 if quote_is_usdg else 1,
            "quote_address": USDG_QUOTE if quote_is_usdg else NATIVE_QUOTE,
            "quote_symbol": "USDG" if quote_is_usdg else "ETH",
            "quote_decimals": 18,
            "meta_ok": 1, "curve_ok": 1,
            "creator_tax_bps": rnd.choice([0, 50, 100, 200]),
            "creator_tax_balance": str(rnd.randint(0, 3) * 10 ** 17),
            "creator_fees_paid": str(rnd.randint(0, 40) * 10 ** 16),
            "enriched_at": ts + 30, "updated_at": now,
            "launch_tx": "0x" + "%064x" % (i * 7919),
            # Filled by derive() below. Declared here as well because a
            # graduated curve has nothing left to derive - its reserves were
            # swept into the pool - and a column that is missing from the
            # first row is a column missing from every INSERT built off it.
            "price_quote": None, "mcap_quote": None, "progress_pct": None,
        }
        derived = store.derive(row)
        row.update(derived)
        if graduated:
            # The whole curve ran. derive() returns early on a swept curve
            # because there is no price to compute, and progress is the one
            # number that still means something.
            row["progress_pct"] = 100.0
        row["mcap_usd"] = (row.get("mcap_quote") or 0) * ETH_USD or None
        tokens.append(row)

        # Trades, and a first buyer for most of them. The first buy decides the
        # sniping verdict, so its size and who made it are what the tabs argue
        # about; everything after it is noise the volume column adds up.
        # Every token traded, deliberately. The table sorts newest first and a
        # token with no trades has an empty sniping cell, so leaving a handful
        # untraded would put blank cells at the top of every picture of it.
        traded = i <= TOKENS
        if traded:
            buys = rnd.randint(12, 260)
            sells = rnd.randint(0, buys // 2 + 4)
            buy_vol = rnd.uniform(0.4, 9.0) * ONE
            sell_vol = buy_vol * rnd.uniform(0.05, 0.6)
            first_block = block + rnd.randint(0, 2)
            buyer = tagged("beef", rnd.randint(1, 60))
            first_quote = rnd.uniform(0.05, 1.4) * ONE
            trades_rows.append((
                address, curve, buys, sells, buy_vol, sell_vol,
                rnd.randint(6, 120), first_block, ts + rnd.randint(1, 6),
                buyer, first_quote,
                first_quote / PRICE_WEI,
                "0x" + "%064x" % (i * 104729),
                block + rnd.randint(60, 4000), now - rnd.randint(3, age),
                1 if rnd.random() < 0.55 else 0, now,
            ))
            hits[buyer] = hits.get(buyer, 0) + 1

            # Five-minute buckets for the volume table, one-minute points for
            # the candle chart. Both keyed the way trades.py keys them, which
            # is the timestamp divided by the bucket size - jitter inside the
            # bucket so the rows do not look stamped, but the key itself is
            # the bucket and cannot repeat.
            #
            # Spread over the token's whole life rather than bunched at its
            # launch, and most of them still trading in the last half hour:
            # the volume tab opens on a one-hour window, and a token whose
            # every bucket is ten hours old is a token that window cannot show.
            step = max(300, (now - ts) // rnd.randint(6, 18))
            for b in range((now - ts) // step + 1):
                bk = ts // 300 + b * (step // 300)
                bts = bk * 300 + rnd.randint(0, 299)
                if bts > now:
                    break
                bb = rnd.uniform(0.05, 1.6) * ONE
                bucket_rows.append((
                    address, bk, rnd.randint(1, 22), rnd.randint(0, 9),
                    bb, bb * rnd.uniform(0.1, 0.8),
                ))
            if rnd.random() < 0.75:
                bk = (now - rnd.randint(60, 1500)) // 300
                bb = rnd.uniform(0.05, 1.6) * ONE
                bucket_rows.append((
                    address, bk, rnd.randint(1, 22), rnd.randint(0, 9),
                    bb, bb * rnd.uniform(0.1, 0.8),
                ))
            price = 2e-8
            for b in range(rnd.randint(10, 40)):
                pk = ts // 60 + b * max(1, (now - ts) // 60 // 40)
                pts = pk * 60 + rnd.randint(0, 59)
                if pts > now:
                    break
                price *= rnd.uniform(0.985, 1.02)
                quote = rnd.uniform(0.01, 0.4) * ONE
                point_rows.append((
                    address, pk, pts, quote, quote / PRICE_WEI,
                    rnd.randint(0, 6), rnd.randint(0, 3),
                ))

            # Early buys: the wallets that were in before the crowd, which is
            # what the Sniped tab lists and what the sniper badges count. Each
            # one also gets a position, because that row is what the Snipers
            # tab prices: without it every wallet there shows a profit of zero
            # and a column that says nothing.
            if rnd.random() < 0.7:
                for k in range(rnd.randint(1, 3)):
                    # A watched wallet turns up in the early buys often enough
                    # that the Wallet tab has a history to show, and the
                    # sniper wallets stay the rest of the pool.
                    if k and rnd.random() < 0.3:
                        who = rnd.choice(WALLET_ADDRS)
                    else:
                        who = buyer if k == 0 else tagged(
                            "beef", rnd.randint(1, 60))
                    # The quote amount is the one to think in: a sniper spends
                    # a fraction of a coin to a couple of coins, and the token
                    # count falls out of the price. Sizing the token count
                    # instead gives a wallet that bought four tokens for six
                    # cents and a profit column that rounds to zero.
                    spent = rnd.uniform(0.05, 1.5) * ONE
                    bought = spent / PRICE_WEI
                    early_rows.append((
                        address, first_block + rnd.randint(0, 3), k, ts + k + 1,
                        who, spent, bought,
                        "0x" + "%064x" % (i * 31337 + k),
                    ))
                    buyer_rows.append((address, who))
                    sold = bought * rnd.choice([0.0, 0.4, 0.85, 1.0, 1.4])
                    position_rows.append((
                        address, who, bought, sold, spent,
                        # Sold at a price that moved, so the profit column has
                        # both winners and losers in it.
                        sold * PRICE_WEI * rnd.uniform(0.35, 2.8),
                        ts + k + 1, now - rnd.randint(30, max(31, age)),
                    ))

        # Nothing writes token_handles here on purpose: the table is filled by
        # the trigger on tokens.twitter, so going through the app's own path is
        # also the check that the trigger and this seed agree on what a handle is.

    # Sniper wallets, built from the hits the loop just counted, so the Snipers
    # tab and the badges on the launch rows cannot disagree.
    for who, n in sorted(hits.items(), key=lambda kv: -kv[1])[:18]:
        sniper_rows.append((who, n, now - rnd.randint(60, 7200)))

    conn.executemany(
        "INSERT INTO quote_assets(address,symbol,name,decimals,kind,usd_price,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        [(NATIVE_QUOTE, "ETH", "Ether", 18, "native", ETH_USD, now),
         (USDG_QUOTE, "USDG", "Global Dollar", 18, "stable", 1.0, now)],
    )
    conn.executemany("INSERT INTO kv(key,value,updated_at) VALUES(?,?,?)",
                     [("eth_usd", str(ETH_USD), now),
                      ("latest_block", str(block), now)])
    conn.executemany("INSERT INTO blocks(number,ts) VALUES(?,?)",
                     [(block - n, now - n) for n in range(0, 600, 2)])

    cols = list(tokens[0])
    conn.executemany(
        "INSERT INTO tokens(%s) VALUES(%s)"
        % (",".join(cols), ",".join("?" * len(cols))),
        [tuple(t[c] for c in cols) for t in tokens],
    )
    conn.executemany(
        "INSERT INTO trades(address,curve,buys,sells,buy_volume,sell_volume,"
        "buyers,first_buy_block,first_buy_ts,first_buyer,first_buy_quote,"
        "first_buy_tokens,first_buy_tx,last_trade_block,last_trade_ts,"
        "snipe_counted,updated_at) VALUES(" + ",".join("?" * 17) + ")",
        trades_rows,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO trade_buckets(address,bucket,buys,sells,buy_volume,"
        "sell_volume) VALUES(?,?,?,?,?,?)", bucket_rows,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO price_points(address,bucket,ts,quote,tokens,"
        "buys,sells)"
        " VALUES(?,?,?,?,?,?,?)", point_rows,
    )
    conn.executemany(
        "INSERT INTO early_buys(address,block,log_index,ts,buyer,quote,tokens,"
        "tx) VALUES(?,?,?,?,?,?,?,?)", early_rows,
    )
    conn.executemany("INSERT OR REPLACE INTO trade_buyers(address,buyer) VALUES(?,?)",
                     buyer_rows)
    conn.executemany("INSERT INTO sniper_wallets(address,hits,last_ts)"
                     " VALUES(?,?,?)", sniper_rows)
    conn.executemany(
        "INSERT OR REPLACE INTO wallet_trades(address,wallet,bought,sold,"
        "spent,received,first_ts,last_ts) VALUES(?,?,?,?,?,?,?,?)", position_rows,
    )

    # A few deployers with a history, which is what the deployer column reads.
    conn.executemany(
        "INSERT INTO deployer_chain(address,nonce,balance_wei,code,state,"
        "block,fetched_at,error) VALUES(?,?,?,?,?,?,?,?)",
        [(tagged("dead", d), rnd.randint(1, 400),
          str(rnd.randint(1, 90) * 10 ** 17), 0, "ok", block, now, None)
         for d in range(1, 10)],
    )

    # Wallets: the labels, and what each one did across the tokens above.
    conn.executemany("INSERT INTO wallets(address,label,added_at) VALUES(?,?,?)",
                     [(a, l, now - 86400) for l, a in WALLETS])

    # The key vault, with two keys in it, wrapped the way the app wraps them.
    # The Wallets tab is the one screen in this project that handles a private
    # key, and a demo database that leaves it empty photographs the tab saying
    # "no wallets saved yet" - which is the state of a fresh install and not of
    # a vault that works. Both secrets are published right here, in the seed,
    # and both addresses hold nothing: there is no reason for anybody to want
    # them, and nothing is lost by the world having them.
    key_rows = []
    for label, n in KEY_WALLETS:
        secret = "0x" + "%064x" % n
        key_rows.append((Account.from_key(secret).address, label,
                         keysafe.wrap(secret, DEMO_PASSPHRASE),
                         keysafe.mask(secret), now - 86400))
    conn.executemany(
        "INSERT INTO key_wallets(address,label,secret,mask,added_at) "
        "VALUES(?,?,?,?,?)", key_rows)

    wt = []
    for l, a in WALLETS:
        # One row per token, keyed (address, wallet) - so the tokens a wallet
        # traded are sampled without replacement rather than drawn at random,
        # which would collide and lose rows to the constraint.
        for tok in rnd.sample(tokens, rnd.randint(3, 9)):
            spent = rnd.uniform(0.1, 2.5) * ONE
            bought = spent / PRICE_WEI
            sold = bought * rnd.uniform(0.0, 1.1)
            wt.append((a, tok["address"], bought, sold, spent,
                       sold * PRICE_WEI * rnd.uniform(0.4, 2.4),
                       now - rnd.randint(3600, 90000),
                       now - rnd.randint(60, 3600)))
    conn.executemany(
        "INSERT INTO wallet_trades(address,wallet,bought,sold,spent,received,"
        "first_ts,last_ts) VALUES(?,?,?,?,?,?,?,?)", wt,
    )

    # The X side: a handful of profiles, one finished walk, and the edges it
    # left behind, so the Handles tab has something to draw.
    conn.executemany(
        "INSERT INTO x_users(id,handle,name,bio,followers,following,statuses,"
        "listed,location,website,avatar,banner,verified,blue,protected,"
        "created_ts,fetched_at) VALUES(" + ",".join("?" * 17) + ")",
        [("900000%06d" % n, h, h.replace("", " ").title(), "onchain since 2021",
          rnd.randint(400, 90000), rnd.randint(80, 3000), rnd.randint(100, 20000),
          rnd.randint(0, 40), "", "", "", "", 0, 1 if rnd.random() < 0.6 else 0,
          0, now - rnd.randint(1, 4) * 86400, now - 900)
         for n, h in enumerate(HANDLES, start=1)],
    )
    conn.execute(
        "INSERT INTO x_lists(owner_id,handle,state,cursor,pages,users,total,"
        "started_at,done_at,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("900000000001", "demo", "done", "", 6, len(HANDLES), len(HANDLES),
         now - 900, now - 840, None),
    )
    conn.executemany(
        "INSERT INTO x_follows(owner_id,ord,target_id,seen_at) VALUES(?,?,?,?)",
        [("900000000001", n, "900000%06d" % n, now - 850)
         for n in range(1, len(HANDLES) + 1)],
    )
    conn.executemany(
        "INSERT INTO x_lookups(handle,state,user_id,error,checked_at)"
        " VALUES(?,?,?,?,?)",
        [(h, "ok", "900000%06d" % n, None, now - 600)
         for n, h in enumerate(HANDLES, start=1)],
    )

    # Copy plans: what the Copy tab is for. The source address is looked up in
    # the tokens above rather than invented, because the row draws what the
    # source was and an address that belongs to no token in the index draws as
    # a bare dash - a plan that appears to have been copied from nothing.
    by_symbol = {t["symbol"]: t for t in tokens}
    conn.executemany(
        "INSERT INTO copy_plans(id,created_at,updated_at,status,source_address,"
        "source_symbol,fields,extra,notes,tx_hash,error)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [(n, now - 3600 * (4 - n), now - 120, st,
          by_symbol[sym]["address"], sym,
          '{"size":"0.25","slippage":"12"}', "{}", "demo plan", None, None)
         for n, (st, sym) in enumerate(COPY_PLANS, start=1)],
    )

    conn.commit()
    conn.close()

    print("wrote %s" % out)
    print("  %d tokens, %d with trades, %d graduated"
          % (len(tokens), len(trades_rows), sum(t["graduated"] for t in tokens)))
    print("  %d buckets, %d price points, %d early buys, %d sniper wallets"
          % (len(bucket_rows), len(point_rows), len(early_rows), len(sniper_rows)))
    print("  every address is synthetic and tagged as such")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "data" / "pons.db"),
                    help="database to write (default: this project's data/pons.db)")
    ap.add_argument("--force", action="store_true",
                    help="write even if the database already holds tokens")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    if out.exists() and not args.force:
        try:
            n = sqlite3.connect(out).execute(
                "SELECT count(*) FROM tokens").fetchone()[0]
        except sqlite3.Error:
            n = 0
        if n:
            print("%s already holds %d tokens. This script only ever writes a "
                  "demo database, so it will not touch that one.\n"
                  "Pass --force if you really mean to overwrite it, or --out to "
                  "write somewhere else." % (out, n))
            return 1
    build(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
