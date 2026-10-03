"""SQLite storage for indexed tokens."""
from __future__ import annotations

import logging
import os
import sqlite3
import statistics
import threading
import time
from typing import Any, Iterable, Sequence

import config as C
import handles
import keysafe

log = logging.getLogger("store")

_local = threading.local()

# Every connection conn() has handed out, so that a transaction left open on
# one of them can be reached from a thread that does not own it. sqlite3
# refuses weak references to a Connection, so this holds them outright; the
# set stays small (one per thread) and is emptied whenever they are replaced.
_conns: set[sqlite3.Connection] = set()
_conns_lock = threading.Lock()
# Bumped when every connection is replaced. A thread compares its own copy of
# this against it on the way back into conn() and builds a fresh connection
# when the two disagree, which is how a connection closed from somewhere else
# gets noticed without reaching into another thread's state.
_gen = 0
# When each connection was first seen holding a transaction, so that a sweep
# can tell a statement that is still running from one that was abandoned. A
# transaction that has been open for five minutes in this app is not working -
# the app cuts its long writes into bounded chunks precisely so none of them
# runs long - and a transaction left open by a failed statement stays open
# forever. Keyed by the connection itself; a Connection hashes by identity and
# cannot be weak-referenced, and `_conns` holds it anyway.
_tx_seen: dict[sqlite3.Connection, float] = {}
# When the request path last swept. It runs on every request, so it is
# throttled to one pass per `WAL_SWEEP_SEC` whatever the request rate.
_swept_at = 0.0

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS tokens (
    address             TEXT PRIMARY KEY,
    curve               TEXT NOT NULL,
    deployer            TEXT,
    pair_token          TEXT,
    launch_block        INTEGER NOT NULL,
    log_index           INTEGER NOT NULL DEFAULT 0,
    launch_ts           INTEGER,

    name                TEXT,
    symbol              TEXT,
    description         TEXT,
    logo                TEXT,
    twitter             TEXT,
    telegram            TEXT,
    discord             TEXT,
    website             TEXT,
    farcaster           TEXT,
    total_supply        TEXT,
    decimals            INTEGER,

    phantom_quote       TEXT,
    real_quote_reserve  TEXT,
    graduation_threshold TEXT,
    sellable_tokens     TEXT,
    reserved_tokens     TEXT,
    graduated           INTEGER DEFAULT 0,
    ready_to_graduate   INTEGER DEFAULT 0,
    is_native_quote     INTEGER DEFAULT 0,

    quote_address       TEXT,
    quote_symbol        TEXT,
    quote_decimals      INTEGER,

    price_quote         REAL,
    mcap_quote          REAL,
    mcap_usd            REAL,
    progress_pct        REAL,

    meta_ok             INTEGER DEFAULT 0,
    curve_ok            INTEGER DEFAULT 0,

    creator_tax_bps     INTEGER,
    creator_tax_balance TEXT,
    creator_fees_paid   TEXT,
    enriched_at         INTEGER,
    updated_at          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_launch_ts   ON tokens(launch_ts DESC);
CREATE INDEX IF NOT EXISTS idx_launch_blk  ON tokens(launch_block DESC);
CREATE INDEX IF NOT EXISTS idx_meta_ok     ON tokens(meta_ok);
CREATE INDEX IF NOT EXISTS idx_mcap        ON tokens(mcap_usd DESC);
CREATE INDEX IF NOT EXISTS idx_progress    ON tokens(progress_pct DESC);
-- The dashboard asks for the graduated count on every polling tick, and a
-- count over a column with no index is a scan of the whole table - half a
-- second on the rows this has now, several times a minute, for a number that
-- changes when a curve completes.
CREATE INDEX IF NOT EXISTS idx_graduated   ON tokens(graduated);

CREATE TABLE IF NOT EXISTS quote_assets (
    address     TEXT PRIMARY KEY,
    symbol      TEXT,
    name        TEXT,
    decimals    INTEGER,
    kind        TEXT,          -- native | usd | stock | other
    usd_price   REAL,
    updated_at  INTEGER
);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER
);

-- block number -> timestamp cache, so backfill never re-asks for a block
CREATE TABLE IF NOT EXISTS blocks (
    number INTEGER PRIMARY KEY,
    ts     INTEGER NOT NULL
);

-- Running per-token trade totals over everything indexed so far. Volumes are
-- floats in quote base units: they are a display metric, and the wei-level
-- precision a float drops is far below anything that matters here.
CREATE TABLE IF NOT EXISTS trades (
    address           TEXT PRIMARY KEY,
    curve             TEXT,
    buys              INTEGER DEFAULT 0,
    sells             INTEGER DEFAULT 0,
    buy_volume        REAL DEFAULT 0,
    sell_volume       REAL DEFAULT 0,
    buyers            INTEGER DEFAULT 0,
    first_buy_block   INTEGER,
    first_buy_ts      INTEGER,
    first_buyer       TEXT,
    first_buy_quote   REAL,
    first_buy_tokens  REAL,
    first_buy_tx      TEXT,
    last_trade_block  INTEGER,
    last_trade_ts     INTEGER,
    snipe_counted     INTEGER DEFAULT 0,
    updated_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trades_last    ON trades(last_trade_ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_buyvol  ON trades(buy_volume DESC);

-- Volume by time slice, so the UI can ask for 5m / 1h / 24h without ever
-- rescanning logs again. Pruned as it falls out of the widest window.
CREATE TABLE IF NOT EXISTS trade_buckets (
    address     TEXT,
    bucket      INTEGER,
    buys        INTEGER DEFAULT 0,
    sells       INTEGER DEFAULT 0,
    buy_volume  REAL DEFAULT 0,
    sell_volume REAL DEFAULT 0,
    PRIMARY KEY (address, bucket)
);
CREATE INDEX IF NOT EXISTS idx_bucket ON trade_buckets(bucket);

-- Every buy inside a token's launch window. This is where a snipe lives, and
-- keeping only this slice means the snipe check costs a bounded table rather
-- than one row per trade the chain has ever seen.
CREATE TABLE IF NOT EXISTS early_buys (
    address   TEXT,
    block     INTEGER,
    log_index INTEGER,
    ts        INTEGER,
    buyer     TEXT,
    quote     REAL,
    tokens    REAL,
    tx        TEXT,
    PRIMARY KEY (address, block, log_index)
);
CREATE INDEX IF NOT EXISTS idx_early_buyer ON early_buys(buyer);
CREATE INDEX IF NOT EXISTS idx_early_block ON early_buys(address, block);

-- How many launches a wallet was the first outside buyer of. One lucky early
-- buy is a person; forty of them is a bot.
CREATE TABLE IF NOT EXISTS sniper_wallets (
    address  TEXT PRIMARY KEY,
    hits     INTEGER DEFAULT 0,
    last_ts  INTEGER
);

-- What is known about the wallet that launched a token. None of these columns
-- can be derived from `tokens`: the index knows WHO deployed, not what kind of
-- wallet it is. Every row costs a request through the same gate the indexer
-- lives on, so it is written once and read many times - deployers are shared
-- between snipers, and reading one sniper warms the cache for the others.
--
-- balance_wei is TEXT rather than INTEGER on purpose: 9.2 ETH is the signed
-- 64-bit ceiling in wei, and anything above it breaks silently in an INTEGER.
--
-- `block` matters more than `fetched_at`: a nonce and a balance are a slice of
-- state, and "0.4 ETH as of block N" is honest where "0.4 ETH, read 5 minutes
-- ago" is not.
--
-- `code` is 1 when the address has bytecode. A deployer can be a contract
-- (Multicall3 is the deployer of 243 tokens for one sniper, with nonce 1 and a
-- zero balance), and a contract has no nonce in the sense a wallet does.
--
-- `state` is ok | nocode | error. A row with no state means "never looked",
-- which is not the same thing as "looked and it failed".
CREATE TABLE IF NOT EXISTS deployer_chain (
    address     TEXT PRIMARY KEY,
    nonce       INTEGER,
    balance_wei TEXT,
    code        INTEGER,
    state       TEXT,
    block       INTEGER,
    fetched_at  INTEGER,
    error       TEXT
);

-- A deployer's nonce on a chain we do not index. One row per (address, chain)
-- rather than a column per chain, because the pair is what a reading is about
-- and a column each would put every new chain behind a migration - the same
-- reason `x_lookups` is a table of its own rather than more columns on
-- `x_users`.
--
-- `chain_id` is half the key and not decoration: the same address has a
-- different nonce on each chain, and a row that lost its chain would be a
-- number with nothing to say what it counts. The profile prints the three side
-- by side precisely because they disagree.
--
-- `state` is per chain, not per address. Ethereum answering while Arbitrum
-- refuses is a real outcome, and drawing both the same way would turn "we do
-- not know" into "there is nothing there". `ok` is a reading, `error` is a
-- refusal the next walk retries, and no row at all is "never asked".
CREATE TABLE IF NOT EXISTS deployer_chain_ext (
    address    TEXT,
    chain_id   INTEGER,
    nonce      INTEGER,
    state      TEXT,
    block      INTEGER,
    fetched_at INTEGER,
    error      TEXT,
    PRIMARY KEY (address, chain_id)
);

-- What was asked of X. The key is the handle and not the id because this is the
-- direction the sniper page asks in: a token names a handle, and we have no id
-- for it. `x_users` stays keyed by id (a screen name changes, an id does not),
-- and this holds what sits between the two: whether we asked, how it ended, and
-- when. Without state='missing' a handle that does not exist is asked for on
-- every single visit.
CREATE TABLE IF NOT EXISTS x_lookups (
    handle     TEXT PRIMARY KEY,
    state      TEXT,
    user_id    TEXT,
    error      TEXT,
    checked_at INTEGER
);

-- Distinct buyer per token, for an honest audience count.
CREATE TABLE IF NOT EXISTS trade_buyers (
    address TEXT,
    buyer   TEXT,
    PRIMARY KEY (address, buyer)
);

-- A drafted copy of a token, fully editable before it is launched.
CREATE TABLE IF NOT EXISTS copy_plans (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     INTEGER,
    updated_at     INTEGER,
    status         TEXT DEFAULT 'draft',
    source_address TEXT,
    source_symbol  TEXT,
    fields         TEXT,
    extra          TEXT,
    notes          TEXT,
    tx_hash        TEXT,
    error          TEXT
);

-- Wallets worth watching, usually ones the snipe check flagged.
CREATE TABLE IF NOT EXISTS wallets (
    address  TEXT PRIMARY KEY,
    label    TEXT,
    added_at INTEGER
);

-- Wallets whose private key this machine holds, added on the Wallets tab.
-- Deliberately not the `wallets` table above: that one is a watchlist of other
-- people's addresses and is safe to throw away, and a table that mixes the two
-- makes "remove from the watchlist" one bug away from deleting a key.
--
-- The key is not kept in the clear. `secret` holds a Web3 keystore: the key
-- under a passphrase that lives only in the server's memory, because this file
-- sits in a folder that syncs and gets copied, and a copy of it should not be a
-- copy of the keys. The earlier note here argued the other way - that a
-- passphrase would be needed on every page load - which was wrong about the
-- cost: it is asked for once per server start, not per request, and what it
-- protects is the file rather than the screen. `keysafe.py` states the limits
-- of that honestly, and they are real: while the vault is open, anything
-- running as this user can read the keys.
--
-- The address is derived from the key on insert and stored, because it is what
-- everything else in the app is keyed by. The mask is stored for the same
-- reason and one more: the query behind the table must not have to open a
-- keystore to draw a row.
CREATE TABLE IF NOT EXISTS key_wallets (
    address  TEXT PRIMARY KEY,
    label    TEXT,
    secret   TEXT NOT NULL,
    mask     TEXT,
    added_at INTEGER
);

-- What the chain last said about a stored key's balance, kept so that the tab
-- that draws the rows never has to ask.
--
-- Deliberately not columns on `key_wallets`. That table holds private keys, and
-- everything that reads it sits one careless SELECT away from a key in a
-- response; this is public data about an address, and it is written by a thread
-- that must not be able to open a keystore at all. The two facts live in two
-- tables for the same reason `wallets` is not `key_wallets`.
--
-- The values are TEXT by the argument in deployer_chain.balance_wei: 9.2 ETH is
-- the signed 64-bit ceiling in wei, and a balance above it would break in an
-- INTEGER silently. This tab exists to spend a balance to the last wei.
--
-- Each half fails on its own, so each one carries its own clock and its own
-- refusal, and there is deliberately no single fetched_at:
--   * if it meant "the last attempt", a row whose robinhood read failed a second
--     ago would announce its three-day-old Arbitrum number as read a second ago;
--   * if it meant "the last successful read", a failure would leave the row
--     looking like one nobody has ever asked about, and every tick of the page
--     would ask the node again.
-- A NULL in a value column means "this reading gave no number" and is never
-- written as 0: 0 is a reading (chain.balances says so in as many words), and a
-- row of zeroes would draw as an empty wallet instead of an unanswered question.
CREATE TABLE IF NOT EXISTS key_wallet_chain (
    address  TEXT PRIMARY KEY,
    rh_wei   TEXT, rh_at   INTEGER, rh_err  TEXT,
    arb_wei  TEXT, arb_at  INTEGER, arb_err TEXT,
    tried_at INTEGER
);

-- What one wallet did in one token, in raw base units on both sides: tokens
-- bought and sold, quote paid and taken out. The curve mints on a buy and
-- burns on a sell, so bought - sold is the wallet's exact position and
-- received - spent is what it has already realised. Together they answer
-- "who holds this" and "did the sniper actually make money" from one row,
-- which is why this is kept per (token, wallet) rather than recomputed.
CREATE TABLE IF NOT EXISTS wallet_trades (
    address   TEXT,
    wallet    TEXT,
    bought    REAL DEFAULT 0,
    sold      REAL DEFAULT 0,
    spent     REAL DEFAULT 0,
    received  REAL DEFAULT 0,
    first_ts  INTEGER,
    last_ts   INTEGER,
    PRIMARY KEY (address, wallet)
);
CREATE INDEX IF NOT EXISTS idx_wt_holder ON wallet_trades(address, bought DESC);

-- One row per token per CANDLE_BUCKET_SEC: what traded in that slice. The
-- chart's price for the slice is quote/tokens, a volume weighted average
-- rather than a close, because that is what the sums give exactly and a
-- close would need a per-trade table this deliberately does not keep.
CREATE TABLE IF NOT EXISTS price_points (
    address TEXT,
    bucket  INTEGER,
    ts      INTEGER,
    quote   REAL DEFAULT 0,
    tokens  REAL DEFAULT 0,
    buys    INTEGER DEFAULT 0,
    sells   INTEGER DEFAULT 0,
    PRIMARY KEY (address, bucket)
);
CREATE INDEX IF NOT EXISTS idx_pp_bucket ON price_points(bucket);

-- The handle a token's twitter field points at, one row per token, so "who
-- launched under this handle" is an indexed lookup instead of the scan
-- `by_handle` has to do. The column holds free text and the same account is
-- stored in every shape a person can paste, so the value here is the
-- normalised handle and not the raw string - see handles.norm, which is what
-- fills it.
--
-- Keyed on address, not on handle, because one token has one handle and
-- enrichment can REWRITE it: the row has to be replaceable in place rather
-- than accumulated. handle is indexed because it is the direction every
-- question is asked in.
CREATE TABLE IF NOT EXISTS token_handles (
    address TEXT PRIMARY KEY,
    handle  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_th_handle ON token_handles(handle);

-- Maintained by triggers rather than by a call in enrich(). Five functions
-- write to `tokens` and a hook in the one that writes twitter today is a hook
-- the sixth writer will not have. A trigger fires where the column changes and
-- cannot be forgotten.
--
-- `pons_handle` is the python function handles.norm registered on the
-- connection (see conn()); a trigger body is pure SQL and cannot call into
-- python any other way. A connection that forgot to register it does not write
-- junk, it fails with "no such function" - the loud direction.
--
-- `AFTER UPDATE OF twitter` fires only when the column is in the SET list, so
-- the price loop - which rewrites mcap_usd on twenty thousand rows a tick -
-- never wakes it. That is the whole reason this is a trigger and not a hook
-- that recomputes in a loop.
--
-- The WHEN clause is what keeps prose out: this column also holds entries like
-- "follow us on telegram", which normalise to nothing and must not land in the
-- table looking like an account.
-- Dropped and recreated rather than guarded by IF NOT EXISTS: the body is
-- logic, not a schema, so an existing database has to get the current one. The
-- table is derived data and the backfill rebuilds it, so dropping costs nothing.
DROP TRIGGER IF EXISTS trg_th_ins;
DROP TRIGGER IF EXISTS trg_th_upd;
DROP TRIGGER IF EXISTS trg_th_del;

CREATE TRIGGER IF NOT EXISTS trg_th_ins AFTER INSERT ON tokens BEGIN
    INSERT OR REPLACE INTO token_handles(address, handle)
    SELECT NEW.address, pons_handle(NEW.twitter)
    WHERE pons_handle(NEW.twitter) IS NOT NULL;
END;

-- No WHEN clause here, deliberately, and the body deletes before it inserts.
-- "The handle this token claims" is whatever the column says right now,
-- including nothing: an update that clears the column, or replaces a url with
-- prose, has to take the row away. A WHEN clause would gate the whole body, so
-- the clearing update would fire nothing and leave the token claiming a handle
-- it no longer names - which reads on the page as a live claim that is not
-- there. The price loop writes mcap_usd and never mentions twitter, so this
-- trigger is still not woken by it.
CREATE TRIGGER IF NOT EXISTS trg_th_upd AFTER UPDATE OF twitter ON tokens BEGIN
    DELETE FROM token_handles WHERE address = NEW.address;
    INSERT OR REPLACE INTO token_handles(address, handle)
    SELECT NEW.address, pons_handle(NEW.twitter)
    WHERE pons_handle(NEW.twitter) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_th_del AFTER DELETE ON tokens BEGIN
    DELETE FROM token_handles WHERE address = OLD.address;
END;

-- One row per X account ever looked up, keyed by the id X gives it - that is
-- the stable one; a screen name can be changed and the old one reused.
CREATE TABLE IF NOT EXISTS x_users (
    id          TEXT PRIMARY KEY,
    handle      TEXT,
    name        TEXT,
    bio         TEXT,
    followers   INTEGER,
    following   INTEGER,
    statuses    INTEGER,
    listed      INTEGER,
    location    TEXT,
    website     TEXT,
    avatar      TEXT,
    banner      TEXT,
    verified    INTEGER,
    blue        INTEGER,
    protected   INTEGER,
    created_ts  INTEGER,
    fetched_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_xu_handle ON x_users(handle);

-- One edge per (owner follows target). The key is (owner_id, ord) and NOT
-- (owner_id, target_id), and that is the difference between a list that can be
-- read while it is being written and one that cannot: a WITHOUT ROWID table is
-- clustered on its primary key, so keying on target_id stores the rows in
-- target_id order, new pages land in the middle, and a page read as
-- "offset=100..200" straddling an insert either repeats or skips people.
-- `ord` is the arrival counter from X, which makes paging append-only: what has
-- been shown never shifts when the next page arrives.
CREATE TABLE IF NOT EXISTS x_follows (
    owner_id   TEXT,
    ord        INTEGER,
    target_id  TEXT,
    seen_at    INTEGER,
    PRIMARY KEY (owner_id, ord)
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS idx_xf_edge ON x_follows(owner_id, target_id);

-- What has been parsed and whether it finished. The second half is the point:
-- an interrupted walk leaves fewer rows than the account has followings, and
-- without a record of how many there should be, half a list reads exactly like
-- a complete one.
CREATE TABLE IF NOT EXISTS x_lists (
    owner_id    TEXT PRIMARY KEY,
    handle      TEXT,
    state       TEXT,
    cursor      TEXT,
    pages       INTEGER DEFAULT 0,
    users       INTEGER DEFAULT 0,
    total       INTEGER,
    started_at  INTEGER,
    done_at     INTEGER,
    error       TEXT
);
"""


# ------------------------------------------------------------- the file
def current_db() -> str:
    """The file this module opens.

    One name for it, so that the log line in `init()` and the size check that
    reads the write-ahead journal cannot drift from what `conn()` actually
    opens. When this project had a second launchpad it kept its own database
    and this returned whichever file the calling thread had been pointed at;
    that file is gone and there is one database again.
    """
    return C.DB_PATH


def conn() -> sqlite3.Connection:
    """This thread's connection to `current_db()`, built on first use.

    The generation is compared on every call rather than once, because
    `replace_connections()` closes connections from whichever thread happens
    to be running the journal sweep. A connection closed there is closed
    under a thread that is not using it, so the counter is how that thread
    finds out: it sees the number moved, drops the handle and builds a new
    one. Nothing reaches into another thread's `_local` to say so.
    """
    c = getattr(_local, "conn", None)
    if c is not None and getattr(_local, "gen", -1) != _gen:
        _local.conn = None
        c = None
    if c is None:
        c = sqlite3.connect(current_db(), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        # The token_handles triggers call pons_handle() to normalise the
        # twitter column, and a trigger body is pure SQL. Registered here
        # rather than in init() because it has to be on every connection:
        # the indexer writes launches on its own thread and its own
        # connection, and a connection without this fails the insert with
        # "no such function: pons_handle" - the loud direction, which is
        # how this was noticed.
        c.create_function("pons_handle", 1, handles.norm, deterministic=True)
        _local.conn = c
        _local.gen = _gen
        # Registered so that a transaction this thread leaves open can be
        # rolled back by the request path, which cannot see this thread's
        # `_local`. `_conns` is cleared by `replace_connections()`, so a
        # connection dropped above is already out of it.
        with _conns_lock:
            _conns.add(c)
    return c


def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    _migrate(c)
    _backfill_handles(c)
    c.commit()
    log.info("db ready at %s", current_db())


def init_schema_on(c: sqlite3.Connection) -> None:
    """The schema and its migrations, applied to a connection of your own.

    `init()` is this for the process's own connection and the file it points
    at. Kept separate so a tool can build a database of its own -
    `tools/seed_demo.py` is the one that exists - without opening the
    application's connection, and without carrying a second copy of the DDL
    that would stop matching this one the first time a column moved.

    The normalising function is registered here for the same reason `conn()`
    registers it: the `token_handles` triggers call it, and an insert on a
    connection without it fails with "no such function". The row factory is set
    for the same kind of reason - the migrations read columns by name.
    """
    c.row_factory = sqlite3.Row
    c.create_function("pons_handle", 1, handles.norm, deterministic=True)
    c.executescript(SCHEMA)
    _migrate(c)
    c.commit()


def _backfill_handles(c: sqlite3.Connection) -> None:
    """Fill token_handles from the tokens already in the database, once.

    Every token indexed before this table existed has a twitter field and no
    row pointing at its handle, so the table starts empty on a database with
    half a million launches in it. Nothing else fills it: the triggers only see
    what changes from now on.

    Resumable on purpose. This walks 481495 rows and writes 374639 of them,
    which takes a few seconds, and this process is killed for memory often
    enough that an interrupted walk is a real case rather than a theoretical
    one. Progress is the rowid reached, kept in `kv`, so a second start picks up
    where the first stopped instead of redoing it or - worse - leaving the table
    half full and looking finished. The distinction matters because a half-full
    table answers "this handle launched nothing" for every handle that had not
    been reached yet.

    Measured on the live database: 376695 rows carry a non-empty twitter and
    374639 of them normalise to a handle. The other 2056 are prose naming a
    social rather than a link to one, and the WHEN clause keeps them out.
    """
    if kv_get("handles_backfill_done"):
        return
    total = c.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
    if not total:
        # A fresh database has nothing to catch up on, and the triggers keep it
        # current from the first launch onwards.
        kv_set("handles_backfill_done", 1)
        return

    at = int(kv_get("handles_backfill_at") or 0)
    step = max(1, C.WRITE_CHUNK)
    t0 = time.time()
    n = 0
    while True:
        rows = c.execute(
            "SELECT rowid, address, twitter FROM tokens "
            "WHERE rowid > ? ORDER BY rowid LIMIT ?", (at, step)).fetchall()
        if not rows:
            break
        # Straight from python rather than through the trigger: these rows are
        # already in the table, so an UPDATE is not coming for them and the
        # trigger would never fire.
        #
        # Built as a reconcile rather than an append - a row whose twitter does
        # not name a handle is removed, not merely left alone. Without the
        # second statement the table would end up meaning "at least the handles
        # the column ever named", and a token that was enriched once and then
        # had its twitter cleared would keep claiming it forever. The addresses
        # are already in hand, so this costs one statement per chunk.
        pairs = [(r["address"], handles.norm(r["twitter"])) for r in rows]
        c.executemany("INSERT OR REPLACE INTO token_handles(address, handle) "
                      "VALUES(?,?)", [(a, h) for a, h in pairs if h])
        c.executemany("DELETE FROM token_handles WHERE address=?",
                      [(a,) for a, h in pairs if not h])
        at = rows[-1]["rowid"]
        n += len(rows)
        c.execute("INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                  "updated_at=excluded.updated_at",
                  ("handles_backfill_at", str(at), int(time.time())))
        c.commit()
        if n % (step * 20) == 0:
            log.info("handles backfill: %s of %s rows", n, total)
    kept = c.execute("SELECT COUNT(*) FROM token_handles").fetchone()[0]
    kv_set("handles_backfill_at", at)
    kv_set("handles_backfill_done", 1)
    log.info("handles backfill: walked %s tokens in %.1fs, %s handles kept",
             n, time.time() - t0, kept)
    # The walk wrote tens of megabytes of frames for rows that are not new
    # information, so the log is folded back rather than left for the disk to
    # carry until the scheduled checkpoint comes round.
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as e:
        log.warning("handles backfill: checkpoint: %s", str(e)[:120])


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns that older databases predate.

    The tokens table is the only one that grows after the fact, and it holds
    thousands of rows already, so it gets an in-place ALTER rather than a
    rebuild. The key table gets the same treatment for a column that arrived
    with the vault.
    """
    have = {r["name"] for r in c.execute("PRAGMA table_info(tokens)")}
    for col, decl in (("launch_tx", "TEXT"),
                      ("creator_tax_bps", "INTEGER"),
                      ("creator_tax_balance", "TEXT"),
                      ("creator_fees_paid", "TEXT")):
        if col not in have:
            c.execute(f"ALTER TABLE tokens ADD COLUMN {col} {decl}")
            log.info("migrated: tokens.%s added", col)
    c.execute("CREATE INDEX IF NOT EXISTS idx_tokens_curve ON tokens(curve)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tokens_deployer ON tokens(deployer)")
    # Every price tick re-prices one quote's tokens with `WHERE quote_address=?`,
    # and without this that is a full scan of a half-million-row table - 8.7
    # seconds each, ninety seconds apart, for each of sixty-six quote assets.
    # The index carries the column alone and not mcap_usd, because an index that
    # contained the re-priced column would be rewritten by every pass, which is
    # the cost this is here to avoid.
    c.execute("CREATE INDEX IF NOT EXISTS idx_tokens_quote "
              "ON tokens(quote_address)")
    # A row that predates the mask column is filled in by keywallet_repair at
    # the next unlock rather than here, because computing a mask means reading
    # the key it belongs to and that needs the passphrase. Until then the cell
    # draws empty, which is a missing label and not a broken table.
    key_have = {r["name"] for r in c.execute("PRAGMA table_info(key_wallets)")}
    if "mask" not in key_have:
        c.execute("ALTER TABLE key_wallets ADD COLUMN mask TEXT")
        log.info("migrated: key_wallets.mask added")
    # Verdicts reached under the old gate froze the wrong first buyer, so the
    # counters built from them are wrong in the direction that matters: they
    # make busy wallets look quiet. Nothing has to be recomputed by hand -
    # the verdict is derived from early_buys, so putting the tokens back in
    # the queue is enough for the loop to redo them.
    if not c.execute("SELECT 1 FROM kv WHERE key='snipes_v2'").fetchone():
        n = c.execute("UPDATE trades SET snipe_counted=0").rowcount
        c.execute("DELETE FROM sniper_wallets")
        c.execute("INSERT INTO kv(key,value,updated_at) "
                  "VALUES('snipes_v2','1',?)", (int(time.time()),))
        if n:
            log.info("migrated: %s tokens back in the snipe queue", n)


# ------------------------------------------------------------------ kv
def kv_get(key: str, default: Any = None) -> Any:
    row = conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(key: str, value: Any) -> None:
    c = conn()
    c.execute(
        "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (key, str(value), int(time.time())))
    c.commit()


# ------------------------------------------------------------------ tokens
def upsert_launches(rows: Sequence[dict[str, Any]]) -> int:
    """Insert newly seen launches; existing rows are left untouched.

    launch_tx is backfilled onto rows that predate the column, because the
    bundling check needs it and re-indexing the whole window to get it would
    cost far more than one extra update.
    """
    if not rows:
        return 0
    c = conn()
    now = int(time.time())
    n = 0
    for r in rows:
        cur = c.execute(
            "INSERT OR IGNORE INTO tokens "
            "(address,curve,deployer,launch_block,log_index,launch_ts,"
            "launch_tx,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (r["address"], r["curve"], r["deployer"], r["launch_block"],
             r.get("log_index", 0), r.get("launch_ts"), r.get("launch_tx"), now))
        n += cur.rowcount
        if not cur.rowcount and r.get("launch_tx"):
            c.execute("UPDATE tokens SET launch_tx=? "
                      "WHERE address=? AND launch_tx IS NULL",
                      (r["launch_tx"], r["address"]))
    c.commit()
    return n


def first_launch_block() -> int | None:
    """The earliest launch this database holds, which is as far back as a
    sweep could possibly have happened."""
    row = conn().execute(
        "SELECT MIN(launch_block) AS b FROM tokens").fetchone()
    return int(row["b"]) if row and row["b"] is not None else None


def add_creator_fees(deltas: dict[str, int]) -> int:
    """Add one span's creator payouts to the running totals.

    The addition happens in Python rather than in SQL. These are base
    units of an 18-decimal token, so `creator_fees_paid = ... + ?`
    overflows SQLite's integer at about 9.2 ETH and quietly continues as
    a REAL with the last digits of the number rounded off. The column is
    TEXT for the same reason total_supply is.
    """
    if not deltas:
        return 0
    c = conn()
    now = int(time.time())
    addrs = sorted(deltas)
    n = 0
    # SQLite caps the parameters in one statement, so the batch is chunked
    # rather than trusted to stay small.
    for i in range(0, len(addrs), 400):
        part = addrs[i:i + 400]
        marks = ",".join("?" * len(part))
        have = {r["address"]: r["creator_fees_paid"] for r in c.execute(
            f"SELECT address, creator_fees_paid FROM tokens "
            f"WHERE address IN ({marks})", part)}
        rows = [(str(int(have[a] or 0) + deltas[a]), now, a)
                for a in part if a in have]
        if rows:
            c.executemany("UPDATE tokens SET creator_fees_paid=?, "
                          "updated_at=? WHERE address=?", rows)
            n += len(rows)
    c.commit()
    return n


def curve_map() -> dict[str, dict[str, Any]]:
    """Every indexed launch keyed by its curve, lowercased.

    Trade logs name the curve that emitted them, so this is the lookup that
    turns a log into the token it belongs to. The whole thing is a few tens of
    thousands of small tuples, which is cheaper to hold in memory than it is
    to ask the database about each of hundreds of thousands of logs.
    """
    rows = conn().execute(
        "SELECT address, curve, launch_block, deployer, launch_tx FROM tokens"
    ).fetchall()
    return {r["curve"].lower(): dict(r) for r in rows}


def pending_metadata(limit: int = 400) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT address, curve FROM tokens WHERE meta_ok=0 "
        "ORDER BY launch_block DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def refresh_candidates(limit: int = 600) -> list[dict[str, Any]]:
    """The newest tokens - their curve state moves on every block."""
    rows = conn().execute(
        "SELECT address, curve FROM tokens "
        "ORDER BY launch_block DESC, log_index DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def refresh_window(limit: int = 400, offset: int = 0) -> list[dict[str, Any]]:
    """A rotating slice over the rest of the table (offset wraps)."""
    rows = conn().execute(
        "SELECT address, curve FROM tokens "
        "ORDER BY launch_block DESC, log_index DESC LIMIT ? OFFSET ?",
        (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def apply_enrichment(addr: str, d: dict[str, Any],
                     quote: dict[str, Any] | None) -> None:
    """Write metadata + curve state + derived metrics for one token."""
    now = int(time.time())
    sets, vals = [], []

    meta_fields = ("name", "symbol", "description", "logo", "twitter",
                   "telegram", "discord", "website", "farcaster")
    present_meta = [f for f in meta_fields if f in d]
    for f in present_meta:
        sets.append(f"{f}=?")
        vals.append(d[f])

    scalar = ("phantom_quote", "real_quote_reserve", "graduation_threshold",
              "sellable_tokens", "reserved_tokens", "total_supply", "decimals")
    for f in scalar:
        if f in d:
            v = d[f]
            sets.append(f"{f}=?")
            vals.append(str(v) if isinstance(v, int) and f != "decimals" else v)

    for f in ("graduated", "ready_to_graduate", "is_native_quote"):
        if f in d:
            sets.append(f"{f}=?")
            vals.append(1 if d[f] else 0)

    derived = derive(d, (quote or {}).get("decimals"))
    if derived:
        for f in ("price_quote", "mcap_quote", "progress_pct"):
            sets.append(f"{f}=?")
            vals.append(derived.get(f))

    # The creator side of the curve. The balance is what the curve is
    # holding for the creator right now, which is not the same thing as
    # what the token has paid out - the paid total is a sum of sweep
    # events and is written by the fees walk, never here.
    if "creator_tax_balance" in d:
        sets.append("creator_tax_balance=?")
        vals.append(str(d["creator_tax_balance"]))
    if "creator_tax_bps" in d:
        sets.append("creator_tax_bps=?")
        vals.append(int(d["creator_tax_bps"]))

    if "pair_token" in d:
        sets.append("pair_token=?")
        vals.append(d["pair_token"])

    if quote:
        sets.append("quote_address=?")
        vals.append(quote.get("address"))
        sets.append("quote_symbol=?")
        vals.append(quote.get("symbol"))
        sets.append("quote_decimals=?")
        vals.append(quote.get("decimals"))
        rate = quote.get("usd_price")
        mq = derived.get("mcap_quote")
        sets.append("mcap_usd=?")
        vals.append(mq * rate if (rate is not None and mq is not None) else None)

    if present_meta:
        sets.append("meta_ok=1")
    if derived:
        sets.append("curve_ok=1")

    sets.append("enriched_at=?")
    vals.append(now)
    sets.append("updated_at=?")
    vals.append(now)
    vals.append(addr)

    c = conn()
    c.execute(f"UPDATE tokens SET {', '.join(sets)} WHERE address=?", vals)
    c.commit()


def derive(d: dict[str, Any], quote_decimals: int | None = None) -> dict[str, Any]:
    """Bonding-curve maths: virtual reserves, price, mcap, progress.

    virtualQuote = phantomQuote + realQuoteReserve
    virtualToken = totalSupply - tokensSold
    k            = phantomQuote * totalSupply        (constant)

    Everything on the curve is in base units, so the quote's decimals have to
    be divided out before the numbers mean anything to a human:

        price = (virtualQuote / virtualToken)
                * 10**tokenDecimals / 10**quoteDecimals
        mcap  = (virtualQuote / virtualToken) * totalSupply / 10**quoteDecimals
    """
    out: dict[str, Any] = {}
    try:
        sup = int(d["total_supply"])
        ph = int(d["phantom_quote"])
        rq = int(d["real_quote_reserve"])
        res = int(d["reserved_tokens"])
        sell = int(d["sellable_tokens"])
        th = int(d["graduation_threshold"])
        dec = int(d.get("decimals") or 18)
    except (KeyError, TypeError, ValueError):
        return out

    qdec = int(quote_decimals) if quote_decimals is not None else 18

    sold = (sup - res) - sell
    vt = sup - sold
    vq = ph + rq
    if vt <= 0:
        return out

    ratio = vq / vt                       # quote base units per token base unit
    out["progress_pct"] = (100.0 * rq / th) if th else 0.0

    if d.get("graduated"):
        # Once graduated the curve is swept (realQuoteReserve and
        # sellableTokens go to zero) and the token trades in a DEX pool.
        # Anything we could compute here would be a leftover, not a price.
        out["progress_pct"] = 100.0
        out["price_quote"] = None
        out["mcap_quote"] = None
        return out

    price = ratio * (10 ** dec) / (10 ** qdec)
    out["price_quote"] = price
    out["mcap_quote"] = ratio * sup / (10 ** qdec)
    return out


def set_launch_ts(addr: str, ts: int) -> None:
    c = conn()
    c.execute("UPDATE tokens SET launch_ts=? WHERE address=?", (ts, addr))
    c.commit()


def set_launch_ts_many(pairs: list[tuple[int, str]]) -> None:
    """pairs: [(ts, address)]"""
    if not pairs:
        return
    c = conn()
    c.executemany("UPDATE tokens SET launch_ts=? WHERE address=?", pairs)
    c.commit()


def set_launch_tx_many(pairs: list[tuple[str, str]]) -> int:
    """pairs: [(tx_hash, address)] for rows that never had one."""
    if not pairs:
        return 0
    c = conn()
    cur = c.executemany(
        "UPDATE tokens SET launch_tx=? WHERE address=? AND launch_tx IS NULL",
        pairs)
    c.commit()
    return cur.rowcount


def missing_launch_tx_count() -> int:
    return conn().execute(
        "SELECT COUNT(*) FROM tokens WHERE launch_tx IS NULL").fetchone()[0]


def missing_timestamps(limit: int = 500) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT address, launch_block FROM tokens WHERE launch_ts IS NULL "
        "ORDER BY launch_block DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def missing_timestamps_count() -> int:
    return conn().execute(
        "SELECT COUNT(*) FROM tokens WHERE launch_ts IS NULL").fetchone()[0]


def cached_block_ts(numbers: Sequence[int]) -> dict[int, int]:
    """Timestamps we already know, keyed by block number."""
    if not numbers:
        return {}
    out: dict[int, int] = {}
    nums = list(set(numbers))
    c = conn()
    for i in range(0, len(nums), 900):
        chunk = nums[i:i + 900]
        qs = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT number, ts FROM blocks WHERE number IN ({qs})", chunk):
            out[row["number"]] = row["ts"]
    return out


def save_block_ts(pairs: dict[int, int]) -> None:
    if not pairs:
        return
    c = conn()
    c.executemany("INSERT OR IGNORE INTO blocks(number, ts) VALUES(?,?)",
                  list(pairs.items()))
    c.commit()


def set_quote(addr: str, symbol: str, name: str, decimals: int,
              kind: str, usd_price: float | None) -> None:
    c = conn()
    c.execute(
        "INSERT INTO quote_assets(address,symbol,name,decimals,kind,usd_price,"
        "updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(address) DO UPDATE SET symbol=excluded.symbol, "
        "name=excluded.name, decimals=excluded.decimals, kind=excluded.kind, "
        "usd_price=COALESCE(excluded.usd_price, quote_assets.usd_price), "
        "updated_at=excluded.updated_at",
        (addr, symbol, name, decimals, kind, usd_price, int(time.time())))
    c.commit()


def quotes() -> list[dict[str, Any]]:
    rows = conn().execute("SELECT * FROM quote_assets").fetchall()
    return [dict(r) for r in rows]


def quote_rates() -> dict[str, float]:
    """USD per whole unit, keyed by quote symbol in lower case.

    Keyed by symbol rather than address because the callers that need it work
    from rows that list a token's pair by name and not by address. The native
    pair has no row in `quote_assets` - ether is not a contract - so its rate
    comes from the kv entry the price loop writes, which is the same number
    everything else on the dashboard prices ether with.
    """
    out: dict[str, float] = {}
    for q in quotes():
        sym = (q["symbol"] or "").strip().lower()
        if sym and q["usd_price"] is not None:
            out[sym] = float(q["usd_price"])
    eth = kv_get("eth_usd")
    if eth is not None:
        try:
            out["eth"] = float(eth)
        except (TypeError, ValueError):
            pass
    return out


def discard() -> None:
    """Drop whatever transaction this thread's connection has open.

    Python's sqlite3 opens a transaction for a write statement and closes it
    only on commit or rollback. A statement that *fails* - 'database is locked'
    is the one that actually happens here - therefore leaves the connection
    sitting inside a transaction that nothing closed, and the driver never
    mentions it. Its next read then takes a snapshot at whatever frame the log
    is on at that moment, and a snapshot is precisely what a checkpoint cannot
    pass.

    That is not a theory. It reproduces exactly: a connection left this way
    froze `copied` at a single frame number while the log grew, and one rollback
    let the checkpoint fold and truncate immediately. On the live server the
    frozen number was 1001 and it did not move for four hours and twenty minutes
    while the log went from 47 MB to 50 GB.

    Called from the error path of every loop, so that a failed statement costs
    one iteration instead of the run.
    """
    c = getattr(_local, "conn", None)
    if c is None:
        return
    try:
        if c.in_transaction:
            c.rollback()
    except sqlite3.Error as e:
        log.warning("rollback failed: %s", str(e)[:120])


def release_snapshots(stale: float = 0.0) -> int:
    """Roll back every transaction left open on any connection we handed out.

    `discard()` clears the calling thread's own connection, which is all a
    loop can do for itself. A request handler runs in a pool thread whose
    connection nothing else can reach, and a failed write there leaves its
    transaction open for the life of the process - pinning the write-ahead
    log at the frame that transaction took its snapshot on. A log that cannot
    be folded past a frame is a log that only grows, which is the shape the
    hundred-and-nineteen-gigabyte one had. This is the reach across threads
    that `discard()` does not have.

    `stale` is how long a transaction has to have been open before it counts
    as abandoned, and it is what makes this safe to run while requests are in
    flight. FastAPI answers requests in a pool, so a sweep that took every
    transaction it found would roll back the work of whichever request had
    not finished - one request ending would break another. A transaction is
    therefore recorded the first time a sweep sees it and left alone until it
    is older than `stale`; a statement that is merely slow keeps its seconds
    and commits, and one that was abandoned stops counting at `stale`. The
    default of zero is the emergency reading, used by `wal_checkpoint` when
    the log is already past the size where leaving it alone costs the disk.

    Returns how many were rolled back.
    """
    now = time.monotonic()
    with _conns_lock:
        conns = list(_conns)
    n = 0
    for c in conns:
        try:
            if not c.in_transaction:
                _tx_seen.pop(c, None)
                continue
            since = _tx_seen.setdefault(c, now)
            if now - since < stale:
                continue
            c.rollback()
            _tx_seen.pop(c, None)
            n += 1
        except sqlite3.Error:
            _tx_seen.pop(c, None)
    return n


def sweep_stale_transactions() -> int:
    """`release_snapshots()` for the request path, throttled.

    Throttled because it runs on every request, and the request path carries
    image and data fetches that arrive far faster than a sweep is worth. One
    pass every `WAL_SWEEP_SEC` is enough to catch a transaction the moment it
    becomes abandoned, which is minutes before the size cap would have.
    """
    global _swept_at
    now = time.monotonic()
    if now - _swept_at < C.WAL_SWEEP_SEC:
        return 0
    _swept_at = now
    return release_snapshots(C.WAL_STALE_TX_SEC)


def replace_connections() -> int:
    """Throw away every connection, so each thread builds a new one.

    The heavier answer to the same problem. A snapshot is not always a
    transaction that `rollback()` can reach - a statement still stepping
    holds one too - and a connection that will not let go has to be closed.
    Threads find out through the generation counter rather than by being
    interrupted: the next call to `conn()` on any of them sees the counter
    has moved and builds a fresh connection, so nothing has to reach into
    another thread's state to make this work.

    Returns how many were closed.
    """
    global _gen
    with _conns_lock:
        conns = list(_conns)
        _conns.clear()
        _tx_seen.clear()
        _gen += 1
    n = 0
    for c in conns:
        try:
            c.close()
            n += 1
        except sqlite3.Error:
            pass
    return n


def _reprice(c: sqlite3.Connection, addr: str, rate: float,
             after: int = 0, most: int | None = None) -> tuple[int, int | None]:
    """Rewrite mcap_usd for one quote's tokens, in bounded transactions.

    A single statement over a quarter of a million rows is one transaction of
    three hundred megabytes, and a log holding one cannot be folded back into
    the database: the next tick is ninety seconds away, so the checkpoint never
    gets a window it can finish in and the file grows until the disk is full.
    Cut into chunks, every commit is a window.

    Rows already carrying the right value are left alone, which is what keeps
    the usd-pegged quote - a rate of exactly 1.0, forever - from rewriting its
    tokens at all. The row ids are collected first and updated by rowid, rather
    than re-running the predicate per chunk: the predicate cannot be answered
    from the index alone, so a scan per chunk would read the whole table a
    hundred and twenty times over.

    `most` caps how many rows this call touches and `after` says where to
    resume; together they are what lets one pass be spread over several ticks.
    It answers the number of rows written and the rowid to carry on from, or
    None when the quote is finished.

    The cap is not about the size of a commit, which the chunking already
    bounds. It is about the size of a tick: the log file keeps the highest
    number of frames that were ever unfolded at once, and three hundred
    megabytes written inside one tick is that number, whether it went in as one
    statement or a hundred. A quarter of the rows per tick puts the same writes
    behind a checkpoint that has ninety seconds to fold each slice.
    """
    step = max(1, C.WRITE_CHUNK)
    if most is None:
        most = C.REPRICE_PER_TICK
    # The index on quote_address carries the rowid, so this is a seek to
    # `after` and a walk forward - no sort, and no scan of the rows already
    # done. One row over the cap is asked for only to learn whether more is
    # left.
    want = None if most <= 0 else most + 1
    ids = [r[0] for r in c.execute(
        "SELECT rowid FROM tokens WHERE quote_address=? AND rowid>? "
        "AND (mcap_usd IS NOT (mcap_quote * ?)) ORDER BY rowid LIMIT ?",
        (addr, after, rate, want if want is not None else -1)).fetchall()]
    if not ids:
        return 0, None
    rest = want is not None and len(ids) > most
    if rest:
        ids = ids[:most]
    for i in range(0, len(ids), step):
        part = ids[i:i + step]
        c.execute("UPDATE tokens SET mcap_usd = mcap_quote * ? "
                  "WHERE rowid IN (%s)" % ",".join("?" * len(part)),
                  [rate] + part)
        c.commit()
    return len(ids), (ids[-1] if rest else None)


_repriced: dict[str, tuple[float, float]] = {}

# The re-price passes that were too big for one tick, as quote address ->
# (rate, the rowid to resume after, the rate the rows before that rowid were
# written with). Bounded by the number of quotes, and a quote leaves it as soon
# as its rows are done.
_pending: dict[str, tuple[float, int, float]] = {}


def apply_quote_rate(addr: str, usd_price: float | None) -> None:
    """Record a quote's USD rate, and re-price its tokens only if it moved.

    The rate itself is written every time: it is one row, and the rest of the
    dashboard prices things with it directly. What is held back is the re-price,
    which costs one write per token - 255 thousand of them for the native pair -
    and which buys nothing below C.RATE_EPSILON. The grid prints market caps in
    millions, so a tenth of a percent never reaches a digit it draws.

    A rate that has not moved that far since the last pass leaves the tokens
    alone. One that has not been re-priced for C.REPRICE_MAX_AGE seconds is
    re-priced regardless, so the column cannot drift however quiet the market
    is.

    After a restart the loop has no memory of the last pass, and the version of
    this that simply re-priced on the first tick re-priced all seventy quotes
    unconditionally - 478 thousand rows and 609 MB of log in one tick, because
    "I have never seen this quote" was read as "its rate has moved infinitely".
    What the tokens were actually priced at is in the database, so that is what
    the first tick compares against, and a quote that did not move while the
    server was down is left alone. It has to be read before the rate itself is
    written, or the only rate there is to compare with is the one just arrived.

    A pass that is too big for one tick is carried on across ticks rather than
    finished here: what is left of it is remembered in `_pending`, and the next
    tick resumes from the rowid it stopped at. The writes are the same either
    way; what changes is that no single tick holds three hundred megabytes of
    log while a reader is inside it.
    """
    if usd_price is None:
        return
    c = conn()
    row = c.execute("SELECT usd_price FROM quote_assets WHERE address=?",
                    (addr,)).fetchone()
    priced_at = row["usd_price"] if row is not None else None
    c.execute("UPDATE quote_assets SET usd_price=?, updated_at=? WHERE address=?",
              (usd_price, int(time.time()), addr))
    c.commit()

    now = time.time()

    pend = _pending.get(addr)
    if pend is not None:
        # A pass already in flight. It carries on from where it stopped, at the
        # rate it was started with unless the rate has moved again, in which case
        # the rest of the rows are written at the newer one.
        rate, after, head = pend
        if abs(usd_price - rate) / max(abs(rate), 1e-12) >= C.RATE_EPSILON:
            rate = usd_price
        n, after = _reprice(c, addr, rate, after)
        if after is None:
            del _pending[addr]
            # The pass is over, and the one number that says what the quote is
            # priced at is the rate its earliest rows got - `head` - because that
            # is what the epsilon has to compare against next. If the rate moved
            # while this pass was running, `head` is not the newest rate, and the
            # tick that follows starts a pass that finds only the head: the rows
            # already written at the newest rate no longer match the WHERE inside
            # `_reprice`. Recording the newest rate here instead would declare the
            # whole quote current and leave that head stale until the rate moved
            # again by itself - up to the hour the age floor allows.
            _repriced[addr] = (head, now)
        else:
            _pending[addr] = (rate, after, head)
            _repriced[addr] = (rate, now)
        if n:
            log.debug("re-priced %d tokens for %s at %.6g (continuing)",
                      n, addr[:12], rate)
        return

    prev = _repriced.get(addr)
    if prev is None and priced_at:
        # The rate these tokens were last priced at, from the run before this
        # one. The age is set to now rather than to when that pass ran, which
        # this cannot know: the epsilon is what holds the column, and the age is
        # the floor under it.
        prev = (float(priced_at), now)
        _repriced[addr] = prev
    if prev is not None:
        last_rate, last_at = prev
        moved = abs(usd_price - last_rate) / max(abs(last_rate), 1e-12)
        if moved < C.RATE_EPSILON and now - last_at < C.REPRICE_MAX_AGE:
            return
    _repriced[addr] = (usd_price, now)
    n, after = _reprice(c, addr, usd_price)
    if after is not None:
        _pending[addr] = (usd_price, after, usd_price)
    if n:
        log.debug("re-priced %d tokens for %s at %.6g%s", n, addr[:12], usd_price,
                  "" if after is None else " (more to do)")


def known_quote_addresses() -> list[str]:
    rows = conn().execute(
        "SELECT DISTINCT pair_token FROM tokens "
        "WHERE pair_token IS NOT NULL").fetchall()
    return [r["pair_token"] for r in rows if r["pair_token"]]


# ------------------------------------------------------------------ queries
SORTS = {
    "newest":   "launch_ts DESC, launch_block DESC, log_index DESC",
    "oldest":   "launch_ts ASC, launch_block ASC, log_index ASC",
    "mcap":     "mcap_usd DESC NULLS LAST",
    "progress": "progress_pct DESC NULLS LAST",
    "block":    "launch_block DESC",
    # These two live in the trades table, so they pull in a join.
    "volume":   "COALESCE(tr.buy_volume, 0) + COALESCE(tr.sell_volume, 0) DESC",
    "snipe":    "CASE WHEN tr.first_buy_block IS NULL THEN 1 ELSE 0 END, "
                "(tr.first_buy_block - tokens.launch_block) ASC, "
                "tr.first_buy_quote DESC",
}
_JOINED_SORTS = {"volume", "snipe"}


def _address_forms(q: str) -> list[str]:
    """Every spelling a stored address could have for this query, or none.

    Returns empty unless the query is a whole address, which is the only case
    worth an index seek: a partial address has to stay a substring search,
    because that is how someone finds a token from the first few characters.

    An address is stored exactly as the launcher wrote it, and that is not one
    spelling - the same checksummed address comes back lower case from a
    wallet and mixed case from an explorer. Comparing against all of them at
    once is still an index seek per value, which is the point: the five LIKEs
    this replaces read every row of the table.
    """
    text = (q or "").strip()
    if len(text) != 42 or not text[:2].lower() == "0x":
        return []
    body = text[2:]
    if any(ch not in "0123456789abcdefABCDEF" for ch in body):
        return []
    forms = {text, text.lower(), "0x" + body.upper()}
    try:
        from eth_utils import to_checksum_address
        forms.add(to_checksum_address(text))
    except Exception:
        # No checksum available is not a reason to refuse the query; the
        # spellings above already cover what this machine writes.
        pass
    return sorted(forms)


def list_tokens(sort: str = "newest", limit: int = C.DEFAULT_LIMIT,
                offset: int = 0, q: str | None = None,
                quote: str | None = None, min_progress: float | None = None,
                graduated: bool | None = None,
                since_ts: int | None = None,
                min_volume_usd: float | None = None,
                traded: bool | None = None) -> list[dict[str, Any]]:
    addr_forms = _address_forms(q) if q else []
    where, params = [], []
    if addr_forms:
        # A whole address is the query the coin card and every deep link make,
        # and it is the one the LIKEs below answer worst: five unindexed text
        # comparisons over every row in the table, about a second, on a click.
        # `address` is the primary key and `deployer` has an index, so both
        # ends of the same question - "this token" and "what did this wallet
        # launch" - are seeks.
        marks = ",".join("?" * len(addr_forms))
        where.append(f"(tokens.address IN ({marks}) OR tokens.deployer IN ({marks}))")
        params += addr_forms * 2
    elif q:
        # What a row is known by: its own name, the wallet that launched it,
        # and the handle it claims. The last two answer the same question from
        # either end - "was a token launched for this handle" and "what did
        # this wallet launch".
        #
        # Every column is written with the table it is in, because two of these
        # queries read a join: `trades` has an `address` of its own, so a bare
        # `address` here is ambiguous the moment the sort is volume or snipe,
        # and SQLite answers that with an error rather than a guess. That was a
        # 500 on every text search made from the volume tab.
        like = f"%{q}%"
        parts = ["tokens.symbol LIKE ?", "tokens.name LIKE ?",
                 "tokens.address LIKE ?", "tokens.twitter LIKE ?",
                 "tokens.deployer LIKE ?"]
        params += [like] * len(parts)
        # A handle is typed the way it is written, with the @, and stored the
        # way it is shared, inside a url. "@elonmusk" is a substring of
        # neither "elonmusk" nor "x.com/elonmusk", so the bare form is tried
        # as a second chance rather than by rewriting the query. A lone "@" is
        # not a handle and would match every row that has one.
        if q.startswith("@") and len(q) > 1:
            parts.append("tokens.twitter LIKE ?")
            params.append(f"%{q[1:]}%")
        where.append("(" + " OR ".join(parts) + ")")
    if quote:
        where.append("quote_symbol = ?")
        params.append(quote)
    if min_progress is not None:
        where.append("progress_pct >= ?")
        params.append(min_progress)
    if graduated is not None:
        where.append("graduated = ?")
        params.append(1 if graduated else 0)
    if since_ts is not None:
        where.append("launch_ts >= ?")
        params.append(since_ts)
    if min_volume_usd is not None:
        # The rate, recovered per row rather than looked up once. A row
        # whose curve has priced it knows its own rate exactly; a
        # stablecoin pair is 1 by definition; anything else is the native
        # quote and takes the eth rate the price loop keeps current. A row
        # that matches none of those is priced at zero, so a floor never
        # lets an unpriceable row through on a technicality.
        rate = (
            "CASE WHEN tokens.mcap_quote > 0 AND tokens.mcap_usd IS NOT NULL "
            "          THEN tokens.mcap_usd / tokens.mcap_quote "
            "     WHEN upper(COALESCE(tokens.quote_symbol, '')) IN "
            "          (" + ", ".join("?" * len(C.STABLE_SYMBOLS)) + ") "
            "          THEN 1.0 "
            "     WHEN tokens.quote_address IS NULL "
            "       OR lower(tokens.quote_address) = ? THEN ? "
            "     ELSE 0.0 END")
        # The decimals of the pair token, so the stored base units become
        # whole units before the rate is applied. Written out rather than
        # computed because SQLite has no pow() everywhere, and every quote
        # on this chain is one of these three.
        scale = ("CASE COALESCE(tokens.quote_decimals, 18) "
                 "WHEN 6 THEN 1e6 WHEN 8 THEN 1e8 WHEN 18 THEN 1e18 "
                 "ELSE 1e18 END")
        where.append(
            "(COALESCE(tr.buy_volume, 0) + COALESCE(tr.sell_volume, 0)) "
            "/ " + scale + " * " + rate + " >= ?")
        params += sorted(C.STABLE_SYMBOLS)
        try:
            eth = float(kv_get("eth_usd") or 0)
        except (TypeError, ValueError):
            eth = 0.0
        params += [C.NATIVE_QUOTE, eth, min_volume_usd]

    if traded:
        # "Did anybody ever buy this" is a question about the trade totals, and
        # they live in the joined row rather than in `tokens`: a launch nobody
        # traded has no row there at all, so COALESCE is what turns that
        # absence into the zero that answers the question. The count is of buys
        # and not of trades, because a pool whose only swap was a sell is not a
        # token anybody bought. This is the one filter here that a launchpad
        # with an empty trade table shows as an empty list rather than as a
        # wrong one - the row it would have shown is not traded, which is the
        # question that was asked.
        where.append("COALESCE(tr.buys, 0) > 0")

    # A volume floor is a condition on the joined row, so asking for one
    # has to pull the join in even when the sort would not have. The traded
    # filter is the second one that reads the join, for the same reason.
    if sort in _JOINED_SORTS or min_volume_usd is not None or traded:
        sql = ("SELECT tokens.*, tr.buys AS t_buys, tr.sells AS t_sells, "
               "tr.buy_volume AS t_buyvol, tr.sell_volume AS t_sellvol, "
               "tr.buyers AS t_buyers, tr.first_buy_block, tr.first_buy_ts, "
               "tr.first_buyer, tr.first_buy_quote, tr.first_buy_tokens, "
               "tr.first_buy_tx, tr.last_trade_ts AS t_last_trade_ts, "
               "tr.snipe_counted AS t_snipe_counted "
               "FROM tokens LEFT JOIN trades tr ON tr.address = tokens.address")
    else:
        sql = "SELECT * FROM tokens"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order = SORTS.get(sort, SORTS.get("newest") or SORTS["newest"])
    order_params: list[Any] = []
    if addr_forms:
        # The address the caller named is the row they asked for. The rest of
        # what matches are tokens that same wallet launched, and the card
        # reads the first row, so the exact match sorts ahead of them whatever
        # order was asked for.
        marks = ",".join("?" * len(addr_forms))
        order = f"(tokens.address IN ({marks})) DESC, " + order
        order_params = list(addr_forms)
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params += order_params + [limit, offset]

    rows = conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def by_handle(handle: str, limit: int = 500) -> list[dict[str, Any]]:
    """Candidate rows for one X handle.

    A prefilter and nothing more. The column holds whatever the launcher typed
    into the contract, so the same account appears as a bare handle, an
    @handle and several url spellings; the only way to compare them is to
    normalise each one, which SQL cannot do. This narrows the table to the
    rows that could possibly match - the LIKE is case-insensitive over ascii,
    which is the case that matters here - and handles.claims() makes the
    decision exact.

    LIKE treats _ and % as wildcards and a handle may contain the first, so
    this can return rows that do not match. That is the safe direction: the
    exact pass is what the answer comes from.
    """
    rows = conn().execute(
        "SELECT * FROM tokens WHERE twitter LIKE ? "
        "ORDER BY launch_ts DESC LIMIT ?",
        (f"%{handle}%", limit)).fetchall()
    return [dict(r) for r in rows]


def handle_coverage() -> dict[str, Any]:
    """How far back an answer about a handle can be trusted.

    "No launch claims this handle" is a statement about the depth this table
    actually holds and never about the chain, so the checker needs both the
    count and that depth. Both are read from the rows rather than from the
    configuration, because what the walk was aiming for and what it reached are
    different numbers and only the second one is a claim about the data.

    The aimed-for depth is `_history_floor` in indexer.py, which reaches back to
    the factory's deployment: history ends where the launchpad begins, so once
    the walk is done this depth is the whole life of the pad and the answer is
    "never" rather than "not lately". Until it is done the number is smaller
    than that, which is exactly what it is for - `oldest_ts` and its depth only
    ever move backwards.

    These used to be the same thing because everything older was deleted.
    Launches are kept now, so both are facts about how far the walk has got.

    `depth_blocks` is 0 on a table with no rows: nothing has been read, so no
    answer rests on anything.
    """
    n, oldest, oldest_block = _coverage_bounds()
    tip = kv_get("last_indexed_block")
    depth = 0
    if oldest_block is not None and tip:
        depth = max(int(tip) - int(oldest_block), 0)
    return {"tokens": int(n or 0), "oldest_ts": oldest,
            # Read from the rows, not from INDEX_WINDOW_BLOCKS. This used to
            # report the window, which was the depth being aimed for - and once
            # the walk reaches the factory's deployment the window is no longer
            # what the walk aims at, so reporting it would have understated the
            # coverage by a fortnight.
            "depth_blocks": depth}


def _coverage_bounds() -> tuple[int, Any, Any]:
    """The three numbers `handle_coverage` is made of, as three index seeks.

    They used to be one query - `SELECT COUNT(*), MIN(launch_ts),
    MIN(launch_block) FROM tokens` - which is a full scan of the table. SQLite's
    min/max optimisation only applies when a min or a max is the whole of the
    answer, so asking for two of them beside a count disqualifies it and the
    plan becomes `SCAN tokens`: half a million rows for three numbers that are
    the first entry of an index each.

    Measured on the live table, which is the only measurement that matters
    here: 431 ms for the scan when the disk was quiet, and 11-15 s while the
    indexer was writing - longer than the page waits for an answer, so the tab
    reported `handle check failed: signal timed out` on a handle that was
    sitting in the cache. Split, the three are about 4 ms together and do not
    grow with the table.

    `IS NOT NULL` is spelled out rather than relied on: `MIN()` already ignores
    NULLs, so it changes no answer, and it is what makes the index usable
    without SQLite having to consider whether the column can hold one.
    """
    c = conn()
    return (c.execute("SELECT COUNT(*) FROM tokens").fetchone()[0],
            c.execute("SELECT MIN(launch_ts) FROM tokens "
                      "WHERE launch_ts IS NOT NULL").fetchone()[0],
            c.execute("SELECT MIN(launch_block) FROM tokens "
                      "WHERE launch_block IS NOT NULL").fetchone()[0])


def handles_for(handles_: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Per handle: how many launches claim it, and the one worth showing.

    This exists because `by_handle` cannot answer a list. That one is a LIKE
    over the raw column and costs a scan of half a million rows per handle,
    which is fine for the one lookup the Handles tab makes and impossible for
    a page of a followings list - a hundred handles would be a hundred scans.

    Answers only for handles that have launches; the caller already has the
    list it asked about, so an absent handle means none rather than unknown.

    The top token comes from a window function rather than from SQLite's bare
    column with MAX(), which does return the right row but only by a documented
    special case that reads like a bug and breaks the moment a second aggregate
    is added next to it.

    The keys asked for are normalised the same way the column was, so `@Foo`, a
    profile url and `foo` all find the one row. That is safe rather than clever:
    every handle in this table is a string `handles.norm` produced, and norm is
    idempotent on its own output, so turning the question into that same form
    cannot miss a row that is there.
    """
    hs = sorted({h for h in (handles.norm(x) for x in handles_) if h})
    if not hs:
        return {}
    out: dict[str, dict[str, Any]] = {}
    # SQLite's default limit is 999 bound variables, and a page of a followings
    # list is a hundred - but nothing here is what enforces that, so the batch
    # is capped rather than trusted.
    for i in range(0, len(hs), 400):
        part = hs[i:i + 400]
        rows = conn().execute(
            "SELECT handle, address, symbol, mcap_usd, launch_ts, n FROM ("
            "  SELECT th.handle AS handle, t.address AS address,"
            "         t.symbol AS symbol, t.mcap_usd AS mcap_usd,"
            "         t.launch_ts AS launch_ts,"
            "         COUNT(*) OVER (PARTITION BY th.handle) AS n,"
            "         ROW_NUMBER() OVER (PARTITION BY th.handle"
            "                            ORDER BY t.mcap_usd DESC) AS rn"
            "  FROM token_handles th JOIN tokens t ON t.address = th.address"
            "  WHERE th.handle IN (%s)"
            ") WHERE rn = 1" % ",".join("?" * len(part)), part).fetchall()
        for r in rows:
            out[r["handle"]] = {
                "launches": int(r["n"] or 0),
                "top_address": r["address"],
                "top_symbol": r["symbol"],
                "top_mcap_usd": r["mcap_usd"],
                "last_ts": r["launch_ts"],
            }
    return out


def handle_stats(handles_: Iterable[str]) -> dict[str, dict[str, int]]:
    """Per handle: how many launches claim it, and how many wallets made them.

    Two numbers rather than one because they answer different questions about
    the same row: three launches under one handle is an account launching,
    three under three wallets is a handle being farmed. The followings list
    sorts by either, so both have to be here.

    `FROM` and `WHERE` are the ones `handles_for` uses, and that is not a
    coincidence to be tidied away: the two only agree on what a launch is
    while they are counting the same set of rows. The join is one to one -
    `token_handles` and `tokens` are keyed by the same address - so `COUNT(*)`
    here is the same number as `handles_for`'s window count, and a test says
    so rather than a comment.

    A separate statement rather than another column on that query, because
    SQLite has no `COUNT(DISTINCT ...) OVER (PARTITION BY ...)`: DISTINCT is
    refused inside a window function. The obvious way round it, a correlated
    subquery in the SELECT list, was measured at ten to twenty seconds on a
    five thousand hand list - it is evaluated per matched row, before the
    window's own row filter. This grouping query does the same work in tens of
    milliseconds.

    Answers only for handles that have launches; a caller asking about a list
    already holds the list, so an absent handle means none rather than unknown.
    """
    hs = sorted({h for h in (handles.norm(x) for x in handles_) if h})
    if not hs:
        return {}
    out: dict[str, dict[str, int]] = {}
    for i in range(0, len(hs), 400):
        part = hs[i:i + 400]
        rows = conn().execute(
            "SELECT th.handle AS handle, COUNT(*) AS n,"
            "       COUNT(DISTINCT t.deployer) AS d "
            "FROM token_handles th JOIN tokens t ON t.address = th.address "
            "WHERE th.handle IN (%s) GROUP BY th.handle"
            % ",".join("?" * len(part)), part).fetchall()
        for r in rows:
            out[r["handle"]] = {"launches": int(r["n"] or 0),
                                "deployers": int(r["d"] or 0)}
    return out


def handle_tokens(handle: str, limit: int = 500) -> list[dict[str, Any]]:
    """Every indexed launch that claims one handle, newest first.

    The indexed counterpart of `by_handle`, which stays as it is for the caller
    that wants candidate rows to normalise itself. Here the normalising has
    already happened - it is what the row in token_handles is - so this is a
    join and not a scan, and the answer is exact rather than a prefilter.

    `handle` may be given in any of the forms the rest of the app takes - `Foo`,
    `@foo`, a profile url - and is normalised here for the same reason
    `handles_for` does it. The tab accepts a typed handle, so it has to.
    """
    h = handles.norm(handle)
    if not h:
        return []
    return [dict(r) for r in conn().execute(
        "SELECT t.* FROM token_handles th JOIN tokens t ON t.address = th.address "
        "WHERE th.handle = ? ORDER BY t.launch_ts DESC LIMIT ?", (h, limit))]


def stats() -> dict[str, Any]:
    c = conn()
    now = int(time.time())
    g = lambda sql, *p: c.execute(sql, p).fetchone()[0]
    return {
        "tokens": g("SELECT COUNT(*) FROM tokens"),
        "enriched": g("SELECT COUNT(*) FROM tokens WHERE meta_ok=1"),
        "pending": g("SELECT COUNT(*) FROM tokens WHERE meta_ok=0"),
        "graduated": g("SELECT COUNT(*) FROM tokens WHERE graduated=1"),
        "last_1h": g("SELECT COUNT(*) FROM tokens WHERE launch_ts >= ?", now - 3600),
        "last_24h": g("SELECT COUNT(*) FROM tokens WHERE launch_ts >= ?", now - 86400),
        "latest_block": kv_get("last_indexed_block"),
        "latest_ts": g("SELECT MAX(launch_ts) FROM tokens"),
    }


def prune(keep_blocks: int = C.INDEX_WINDOW_BLOCKS,
          tip: int | None = None) -> int:
    if tip is None:
        return 0
    cutoff = tip - keep_blocks
    c = conn()
    cur = c.execute("DELETE FROM tokens WHERE launch_block < ?", (cutoff,))
    c.commit()
    return cur.rowcount


# ------------------------------------------------------------------ trades
# A token's first buy is the earliest one the chain ever saw, but spans are
# walked in both directions (live forward, history backward) and restarts
# replay ground already covered, so every write has to be a guarded merge
# rather than a plain assignment. `_first` builds that guard per column.
#
# The guard key is first_buy_block, and it has to be guarded itself. Leaving
# it out freezes it at whatever span wrote first - which for a downward walk
# is the *latest* span - while the columns it governs keep taking the earlier
# span's values. That produces rows that claim a first buy in the launch
# transaction but at a block 144k later, which reads as a snipe at a block
# distance no snipe could have.
def _first(col: str, cmp_op: str) -> str:
    return (
        f"{col} = CASE "
        f"WHEN excluded.first_buy_block IS NULL THEN trades.{col} "
        f"WHEN trades.first_buy_block IS NULL THEN excluded.{col} "
        f"WHEN excluded.first_buy_block {cmp_op} trades.first_buy_block "
        f"THEN excluded.{col} ELSE trades.{col} END"
    )


_FIRST_COLS = ("first_buy_block", "first_buy_ts", "first_buyer",
               "first_buy_quote", "first_buy_tokens", "first_buy_tx")
_FIRST_KEYS = ("block", "ts", "buyer", "quote", "tokens", "tx")

_TRADE_COLS = ("address", "curve", "buys", "sells", "buy_volume",
               "sell_volume", "first_buy_block", "first_buy_ts",
               "first_buyer", "first_buy_quote", "first_buy_tokens",
               "first_buy_tx", "last_trade_block", "last_trade_ts")

_TRADE_SQL = f"""
INSERT INTO trades ({", ".join(_TRADE_COLS)}, updated_at)
VALUES ({", ".join("?" * len(_TRADE_COLS))}, ?)
ON CONFLICT(address) DO UPDATE SET
  buys         = trades.buys + excluded.buys,
  sells        = trades.sells + excluded.sells,
  buy_volume   = trades.buy_volume + excluded.buy_volume,
  sell_volume  = trades.sell_volume + excluded.sell_volume,
  curve        = COALESCE(trades.curve, excluded.curve),
  {_first("first_buy_block", "<")},
  {_first("first_buy_ts", "<")},
  {_first("first_buyer", "<")},
  {_first("first_buy_quote", "<")},
  {_first("first_buy_tokens", "<")},
  {_first("first_buy_tx", "<")},
  last_trade_block = CASE
      WHEN excluded.last_trade_block IS NULL THEN trades.last_trade_block
      WHEN trades.last_trade_block IS NULL THEN excluded.last_trade_block
      WHEN excluded.last_trade_block > trades.last_trade_block
      THEN excluded.last_trade_block ELSE trades.last_trade_block END,
  last_trade_ts = MAX(COALESCE(trades.last_trade_ts, 0),
                      COALESCE(excluded.last_trade_ts, 0)),
  updated_at = excluded.updated_at
"""


_WALLET_SQL = """
INSERT INTO wallet_trades (address, wallet, bought, sold, spent, received,
                           first_ts, last_ts)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(address, wallet) DO UPDATE SET
  bought   = wallet_trades.bought + excluded.bought,
  sold     = wallet_trades.sold + excluded.sold,
  spent    = wallet_trades.spent + excluded.spent,
  received = wallet_trades.received + excluded.received,
  first_ts = CASE
      WHEN wallet_trades.first_ts IS NULL THEN excluded.first_ts
      WHEN excluded.first_ts IS NULL THEN wallet_trades.first_ts
      ELSE MIN(wallet_trades.first_ts, excluded.first_ts) END,
  last_ts = CASE
      WHEN wallet_trades.last_ts IS NULL THEN excluded.last_ts
      WHEN excluded.last_ts IS NULL THEN wallet_trades.last_ts
      ELSE MAX(wallet_trades.last_ts, excluded.last_ts) END
"""

_CANDLE_SQL = """
INSERT INTO price_points (address, bucket, ts, quote, tokens, buys, sells)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(address, bucket) DO UPDATE SET
  quote  = price_points.quote + excluded.quote,
  tokens = price_points.tokens + excluded.tokens,
  buys   = price_points.buys + excluded.buys,
  sells  = price_points.sells + excluded.sells
"""


def _many(c: sqlite3.Connection, sql: str, rows: Sequence[Any],
          chunk: int | None = None) -> None:
    """Run a batch in bounded transactions instead of one enormous one.

    What this buys is not speed, it is the size of the transaction. A span of
    ten thousand blocks aggregated whole is a single transaction of hundreds of
    megabytes, and a write-ahead log holding one cannot be folded back into the
    database: the next span starts half a second later, and the checkpoint
    never gets a window it can finish in. Smaller transactions are windows.

    Every statement this is used for is a sum or a guarded merge keyed on a
    primary key, so how the batch is cut changes nothing about what it writes.
    """
    if not rows:
        return
    step = C.WRITE_CHUNK if chunk is None else chunk
    if step <= 0 or len(rows) <= step:
        c.executemany(sql, rows)
        c.commit()
        return
    for i in range(0, len(rows), step):
        c.executemany(sql, rows[i:i + step])
        c.commit()


def apply_trades(agg: dict[str, list[tuple]],
                 cards_only: bool = False) -> tuple[int, int]:
    """Merge one span of aggregated trades into the tables.

    agg holds plain tuples rather than dicts: this runs for hundreds of
    thousands of logs, and the row count is what costs, not the shape.

    `cards_only` writes the coin card's tables and nothing else. Volumes and
    buckets are sums, so replaying a span over them inflates them; the
    position backfill necessarily replays blocks the trade walk has already
    been through, and this is what keeps that replay from touching them.
    """
    trades = agg.get("trades") or []
    buckets = agg.get("buckets") or []
    candles = agg.get("candles") or []
    wallets = agg.get("wallets") or []
    early = agg.get("early") or []
    buyers = agg.get("buyers") or []
    c = conn()
    now = int(time.time())

    if trades and not cards_only:
        _many(c, _TRADE_SQL, [tuple(r) + (now,) for r in trades])

    if buckets and not cards_only:
        _many(c,
              "INSERT INTO trade_buckets(address,bucket,buys,sells,buy_volume,"
              "sell_volume) VALUES(?,?,?,?,?,?) "
              "ON CONFLICT(address,bucket) DO UPDATE SET "
              "buys=buys+excluded.buys, sells=sells+excluded.sells, "
              "buy_volume=buy_volume+excluded.buy_volume, "
              "sell_volume=sell_volume+excluded.sell_volume", buckets)

    if candles:
        _many(c, _CANDLE_SQL, candles)

    if wallets:
        _many(c, _WALLET_SQL, wallets)

    if early and not cards_only:
        _many(c,
              "INSERT OR IGNORE INTO early_buys(address,block,log_index,ts,"
              "buyer,quote,tokens,tx) VALUES(?,?,?,?,?,?,?,?)", early)

    if buyers and not cards_only:
        _many(c,
              "INSERT OR IGNORE INTO trade_buyers(address,buyer) VALUES(?,?)",
              buyers)
        # Recount only what changed: a full COUNT over an indexed table is
        # cheap, but doing it for every token on every span is not.
        addrs = sorted({b[0] for b in buyers})
        _many(c,
              "UPDATE trades SET buyers=(SELECT COUNT(*) FROM trade_buyers "
              "WHERE address=?) WHERE address=?", [(a, a) for a in addrs])

    c.commit()
    return len(trades), len(early)


# The first-buy columns are the facts the snipe verdict is read from, and they
# are the one part of a trade row that is a guarded merge rather than a sum:
# they only ever move a token's first buy earlier, so they can be written again
# over blocks that were already walked. The sums next to them cannot - buys,
# sells and the volumes would count the span twice - and a repair pass reads
# exactly such blocks. Hence this statement, which is `_first` on its own: it
# matches only the rows whose first buy is missing or later than the one this
# span saw, so replaying a span is free and the count it returns is the number
# of tokens it actually corrected.
_REPAIR_SQL = f"""
UPDATE trades SET
  {", ".join(f"{col} = :{key}" for col, key in zip(_FIRST_COLS, _FIRST_KEYS))},
  updated_at = :now
WHERE address = :address
  AND :block IS NOT NULL
  AND (trades.first_buy_block IS NULL OR :block < trades.first_buy_block)
"""


def repair_first(agg: dict[str, list[tuple]]) -> tuple[int, int]:
    """Restore a span's first-buy facts without touching the sums.

    Used by the repair pass, which re-reads blocks the trade walk has already
    been through because the buys it dropped there cannot come back on their
    own: the walk moves forward and never revisits a block.

    Returns (tokens corrected, early rows added). Both are what the statements
    changed, not what they were handed - an update that matches a row and
    writes the value already in it is not a correction.
    """
    rows = agg.get("trades") or []
    early = agg.get("early") or []
    buyers = agg.get("buyers") or []
    c = conn()
    now = int(time.time())

    args = [{"address": r[0], "curve": r[1], "block": r[6], "ts": r[7],
             "buyer": r[8], "quote": r[9], "tokens": r[10], "tx": r[11],
             "now": now}
            for r in rows if r[6] is not None]
    before = c.total_changes
    if args:
        c.executemany(_REPAIR_SQL, args)
    moved = c.total_changes - before

    added = 0
    if early:
        before = c.total_changes
        c.executemany(
            "INSERT OR IGNORE INTO early_buys(address,block,log_index,ts,"
            "buyer,quote,tokens,tx) VALUES(?,?,?,?,?,?,?,?)", early)
        added = c.total_changes - before
    if buyers:
        # Idempotent, and the count next to it is recomputed rather than
        # incremented, so neither can drift when a span is read twice.
        c.executemany(
            "INSERT OR IGNORE INTO trade_buyers(address,buyer) VALUES(?,?)",
            buyers)
        addrs = sorted({b[0] for b in buyers})
        c.executemany(
            "UPDATE trades SET buyers=(SELECT COUNT(*) FROM trade_buyers "
            "WHERE address=?) WHERE address=?", [(a, a) for a in addrs])
    return moved, added


def unfinalized_snipes(max_launch_block: int,
                       min_launch_block: int | None = None
                       ) -> list[dict[str, Any]]:
    """Tokens whose early window has closed but whose first buyer is uncounted.

    The window has to be fully *indexed*, and the chain tip is not that: the
    trade walk runs behind the tip on purpose, so judging against the tip let
    a token be finalised from a span that did not yet contain the buys racing
    it - and the verdict is marked done, so it was never revisited. The two
    cursors of the walk bracket exactly the blocks it has been through, which
    is the honest bound.
    """
    where = ["tr.snipe_counted = 0", "t.launch_block + ? <= ?"]
    params: list[Any] = [C.EARLY_BLOCKS, max_launch_block]
    if min_launch_block is not None:
        where.append("t.launch_block >= ?")
        params.append(min_launch_block)
    rows = conn().execute(
        "SELECT t.address, t.deployer, t.launch_block, t.launch_tx "
        "FROM trades tr JOIN tokens t ON t.address = tr.address "
        "WHERE " + " AND ".join(where), params).fetchall()
    return [dict(r) for r in rows]


def first_racers(addresses: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The first early buy per token by a wallet other than the deployer.

    This, and not "whoever bought first", is what identifies a sniper: a
    wallet that shows up four hours later is an ordinary buyer, and counting
    those would make every popular token's first buyer look like a bot.
    """
    if not addresses:
        return {}
    out: dict[str, dict[str, Any]] = {}
    c = conn()
    addrs = list(dict.fromkeys(addresses))
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT e.address, e.buyer, e.quote, e.block, e.log_index, "
                f"       e.ts, e.tx FROM early_buys e "
                f"JOIN tokens t ON t.address = e.address "
                f"WHERE e.address IN ({qs}) "
                f"  AND e.buyer <> t.deployer "
                f"ORDER BY e.address, e.block, e.log_index", chunk):
            # Ordered, so the first row per address is that token's racer.
            out.setdefault(row["address"], dict(row))
    return out


def count_snipes(rows: Sequence[dict[str, Any]],
                 racers: dict[str, dict[str, Any]]) -> int:
    """Bump the repeat-offender counter for the wallets that raced in.

    The deployer buying in its own launch is not a snipe, so it never counts
    towards a wallet's hits. What counts is being the first wallet other than
    the deployer into a launch, inside the early window.
    """
    if not rows:
        return 0
    now = int(time.time())
    hits: dict[str, int] = {}
    for r in rows:
        who = (racers.get(r["address"]) or {}).get("buyer")
        if who:
            hits[who] = hits.get(who, 0) + 1
    c = conn()
    if hits:
        c.executemany(
            "INSERT INTO sniper_wallets(address,hits,last_ts) VALUES(?,?,?) "
            "ON CONFLICT(address) DO UPDATE SET "
            "hits=sniper_wallets.hits+excluded.hits, last_ts=excluded.last_ts",
            [(a, n, now) for a, n in hits.items()])
    c.executemany("UPDATE trades SET snipe_counted=1 WHERE address=?",
                  [(r["address"],) for r in rows])
    c.commit()
    return len(hits)


def sniper_list(limit: int = 100, offset: int = 0, sort: str = "hits",
                min_hits: int = 1) -> list[dict[str, Any]]:
    """Wallets ranked by how many launches they were first into."""
    order = {"hits": "hits DESC, last_ts DESC",
             "recent": "last_ts DESC, hits DESC",
             "address": "address ASC"}.get(sort, "hits DESC, last_ts DESC")
    rows = conn().execute(
        f"SELECT * FROM sniper_wallets WHERE hits >= ? "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        (min_hits, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def sniper_tokens(addresses: Sequence[str],
                  per_wallet: int = 12) -> dict[str, list[dict[str, Any]]]:
    """The launches each wallet was first into, newest first.

    "First into" is meant literally: the earliest early buy by anyone other
    than the deployer. A wallet that bought in the same window but behind
    someone else was not first in, and listing it anyway would make the list
    disagree with the `hits` count sitting next to it - which is exactly the
    number that says whether a wallet is a bot.
    """
    if not addresses:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    c = conn()
    addrs = list(dict.fromkeys(addresses))
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT e.buyer, e.address, e.block, e.log_index, e.quote, "
                f"       e.ts, e.tx, t.symbol, t.name, t.logo, t.launch_block, "
                f"       t.launch_ts, t.launch_tx, t.graduation_threshold, "
                f"       t.quote_symbol, t.quote_decimals, t.graduated, "
                f"       t.total_supply, t.decimals, t.phantom_quote, "
                f"       t.real_quote_reserve, t.sellable_tokens, "
                f"       t.reserved_tokens, "
                f"       w.bought, w.sold, w.spent, w.received, "
                f"       w.first_ts AS held_since, w.last_ts AS traded_at "
                f"FROM early_buys e JOIN tokens t ON t.address = e.address "
                f"LEFT JOIN wallet_trades w ON w.address = e.address "
                f"                         AND w.wallet = e.buyer "
                f"WHERE e.buyer IN ({qs}) AND e.buyer <> t.deployer "
                f"  AND NOT EXISTS ("
                f"    SELECT 1 FROM early_buys e2 "
                f"    WHERE e2.address = e.address AND e2.buyer <> t.deployer "
                f"      AND (e2.block < e.block "
                f"           OR (e2.block = e.block "
                f"               AND e2.log_index < e.log_index))) "
                f"ORDER BY e.buyer, e.block, e.log_index", chunk):
            lst = out.setdefault(row["buyer"], [])
            # One row per launch: the wallet's first buy in that window.
            if lst and lst[-1]["address"] == row["address"]:
                continue
            lst.append(dict(row))
    for k, v in out.items():
        v = sorted(v, key=lambda r: r["block"], reverse=True)
        # per_wallet of 0 means every one: a total is only honest if it is
        # taken over the whole set rather than the page that happens to fit.
        out[k] = v[:per_wallet] if per_wallet else v
    return out


def sniper_stats(min_hits: int = 1) -> dict[str, Any]:
    c = conn()
    return {
        "wallets": c.execute("SELECT COUNT(*) FROM sniper_wallets").fetchone()[0],
        "repeat": c.execute(
            "SELECT COUNT(*) FROM sniper_wallets WHERE hits >= ?",
            (C.BOT_HITS_MIN,)).fetchone()[0],
        "launches": c.execute(
            "SELECT COALESCE(SUM(hits),0) FROM sniper_wallets WHERE hits >= ?",
            (min_hits,)).fetchone()[0],
    }


# The keys a sniper's snipe list can be ordered by, as (field, descending). The
# same habit as FOLLOW_SORTS and the volume sorts: both directions of a key are
# one name each rather than a key plus a `dir` flag, and an unknown name is not
# an error - it falls out of the dict and the list keeps its default order.
#
# The last two order by what is *cached*, not by what is knowable: a deployer
# nobody has read has no nonce, and a handle nobody has looked up has no
# followers. Those rows sort last in both directions rather than as zero, and
# the page prints how many of how many took part - see sniper_order.
SNIPE_SORTS: dict[str, tuple[str, bool]] = {
    "block_desc":             ("block", True),
    "block_asc":              ("block", False),
    "delta_asc":              ("delta", False),
    "delta_desc":             ("delta", True),
    "quote_desc":             ("quote", True),
    "quote_asc":              ("quote", False),
    "deployer_launches_desc": ("d_launches", True),
    "deployer_nonce_desc":    ("d_nonce", True),
    "followers_desc":         ("followers", True),
    "followers_asc":          ("followers", False),
}
SNIPE_SORT_DEFAULT = "block_desc"

# The token and launch columns a snipe row is drawn from - the same ones the
# Snipers tab already shows, plus the deployer and the claimed handle. `delta`
# is computed here rather than in Python because it is the field the most
# interesting sort key is built on.
_SNIPE_ROW_SQL = """
SELECT e.address AS address, e.block AS block, e.log_index AS log_index,
       e.ts AS ts, e.quote AS quote, e.tx AS buy_tx,
       t.symbol AS symbol, t.name AS name, t.logo AS logo,
       t.deployer AS deployer, t.launch_block AS launch_block,
       t.launch_ts AS launch_ts, t.launch_tx AS launch_tx,
       t.graduated AS graduated, t.progress_pct AS progress_pct,
       t.mcap_usd AS mcap_usd, t.twitter AS twitter,
       t.quote_symbol AS quote_symbol, t.quote_decimals AS quote_decimals,
       t.total_supply AS total_supply, t.decimals AS decimals,
       t.phantom_quote AS phantom_quote,
       t.real_quote_reserve AS real_quote_reserve,
       t.sellable_tokens AS sellable_tokens,
       t.reserved_tokens AS reserved_tokens,
       t.graduation_threshold AS graduation_threshold,
       (e.block - t.launch_block) AS delta,
       CASE WHEN NOT EXISTS (
           SELECT 1 FROM early_buys e2
           WHERE e2.address = e.address AND e2.buyer <> t.deployer
             AND (e2.block < e.block
                  OR (e2.block = e.block AND e2.log_index < e.log_index)))
       THEN 1 ELSE 0 END AS first
FROM early_buys e JOIN tokens t ON t.address = e.address
WHERE e.buyer = ? AND e.buyer <> t.deployer
ORDER BY e.block DESC, e.log_index DESC
"""


def sniper_snipes(address: str) -> list[dict[str, Any]]:
    """Every buy this wallet made inside a launch window, newest first.

    Each row carries `first`: 1 when this wallet was the earliest buyer other
    than the deployer, 0 when somebody beat it. Both sets come back from one
    query on purpose. The firsts are what "which tokens did he snipe" means; the
    rest are the races he lost, and the ratio between them is the most telling
    single thing about a sniper - one who enters everything and is first into
    half of it is a different animal from one who only enters what he can win.
    Two queries would also pay the NOT EXISTS twice, and the LIMIT does not push
    down through it anyway: a page of 100 costs the same 80 ms as the lot, so
    the whole set is read once and paged in Python.

    The launch transaction is deliberately NOT excluded. Its `early_buys` row is
    the bundle's router buying at `launch_block` with a low log_index, and it
    honestly beats everyone who came in later; excluding it would file wallets
    the bundle overtook as snipers. This is the same rule `first_racers` and
    `count_snipes` use to credit `sniper_wallets.hits`, so all three agree - on
    the live table this function and `hits` both count 1169 for the top wallet.
    """
    if not address:
        return []
    return [dict(r) for r in conn().execute(_SNIPE_ROW_SQL, (address,))]


def sniper_get(address: str) -> dict[str, Any] | None:
    """One leaderboard row, matched without case.

    Matched by hand because every query that finds a wallet's snipes compares
    `early_buys.buyer` exactly: a hash typed in lower case would otherwise find
    the wallet in the leaderboard and none of its snipes. The scan this costs is
    five thousand rows on a primary key, which is not a cost. Returns None for a
    wallet the counter has never credited - which is not the same as a wallet
    with no snipes, and callers must not treat it as one.
    """
    if not address:
        return None
    row = conn().execute(
        "SELECT address, hits, last_ts FROM sniper_wallets "
        "WHERE address = ? COLLATE NOCASE", (address,)).fetchone()
    return dict(row) if row else None


def deployer_profiles(addresses: Sequence[str]) -> dict[str, dict[str, Any]]:
    """What is known about each deployer: its launches, and its chain row.

    `launches` and `first_block` are counted from `tokens` by this function's
    own GROUP BY rather than by calling `deployer_counts`, so that the two can
    be held against each other by a test instead of by a comment - which is how
    `handle_stats` is already pinned to `handles_for`.

    The chain columns are joined in where a row exists. An address with no row
    in `deployer_chain` comes back with `state: None`, which means "never
    looked", and callers must not render that as a zero.

    `ext` carries the readings from the chains we do not index, keyed by
    chain_id, and it is empty rather than absent when there are none - the same
    distinction as `state`, one level down. Each of its entries has its own
    state, because Ethereum answering while Arbitrum refuses is a real outcome
    and the page draws the two differently.
    """
    addrs = list(dict.fromkeys(a for a in addresses if a))
    if not addrs:
        return {}
    c = conn()
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT deployer, COUNT(*) AS n, MIN(launch_block) AS fb "
                f"FROM tokens WHERE deployer IN ({marks}) GROUP BY deployer",
                chunk):
            out[row["deployer"]] = {
                "address": row["deployer"], "launches": int(row["n"]),
                "first_block": row["fb"],
                "nonce": None, "balance_wei": None, "code": None,
                "state": None, "block": None, "fetched_at": None,
                "ext": {},
            }
        for row in c.execute(
                f"SELECT * FROM deployer_chain WHERE address IN ({marks})",
                chunk):
            e = out.setdefault(row["address"], {
                "address": row["address"], "launches": 0, "first_block": None,
                "ext": {}})
            e.update({"nonce": row["nonce"], "balance_wei": row["balance_wei"],
                      "code": row["code"], "state": row["state"],
                      "block": row["block"], "fetched_at": row["fetched_at"]})
        for row in c.execute(
                f"SELECT * FROM deployer_chain_ext WHERE address IN ({marks})",
                chunk):
            e = out.setdefault(row["address"], {
                "address": row["address"], "launches": 0, "first_block": None,
                "nonce": None, "balance_wei": None, "code": None,
                "state": None, "block": None, "fetched_at": None, "ext": {}})
            e["ext"][int(row["chain_id"])] = {
                "nonce": row["nonce"], "state": row["state"],
                "block": row["block"], "fetched_at": row["fetched_at"],
                "error": row["error"],
            }
    return out


def handles_for_tokens(addresses: Sequence[str]) -> dict[str, str]:
    """The claimed handle per token address.

    The other direction of `handles_for`, which only ever asks handle -> tokens.
    Keyed on `token_handles.address`, which is its primary key and has never
    been used as a lookup key before. Tokens whose `twitter` field is prose
    rather than a handle have no row at all, so absence means "claims nothing",
    not "claims something unreadable".
    """
    addrs = list(dict.fromkeys(a for a in addresses if a))
    if not addrs:
        return {}
    c = conn()
    out: dict[str, str] = {}
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT address, handle FROM token_handles "
                f"WHERE address IN ({marks})", chunk):
            out[row["address"]] = row["handle"]
    return out


def x_users_by_handle(handles_: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The X profile of each handle we have seen, via idx_xu_handle.

    An account whose screen name has changed since it was fetched is still
    found under the name it had then; the id in `x_users` is the stable key, and
    a rename simply means the old handle no longer matches anything.
    """
    names = list(dict.fromkeys(h for h in handles_ if h))
    if not names:
        return {}
    c = conn()
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(names), 400):
        chunk = names[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT * FROM x_users WHERE handle IN ({marks})", chunk):
            out[row["handle"]] = dict(row)
    return out


def x_lookups_get(handles_: Iterable[str]) -> dict[str, dict[str, Any]]:
    """What is known about whether each handle was ever asked for."""
    names = list(dict.fromkeys(h for h in handles_ if h))
    if not names:
        return {}
    c = conn()
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(names), 400):
        chunk = names[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT * FROM x_lookups WHERE handle IN ({marks})", chunk):
            out[row["handle"]] = dict(row)
    return out


def x_lookups_put(rows: Sequence[dict[str, Any]],
                  now: int | None = None) -> int:
    """Remember the outcome of asking X about a handle.

    A handle that does not exist is written as state='missing' and never asked
    for again. Without that row the same dead handle is a fresh request on every
    visit to the page, which is how a handful of typos in token descriptions
    turns into a permanent tax on the account's rate limit.
    """
    at = now or int(time.time())
    vals = []
    for r in rows or []:
        h = (r.get("handle") or "").strip().lower().lstrip("@")
        if not h:
            continue
        vals.append((h, r.get("state") or "ok", r.get("user_id"),
                     r.get("error") or "", at))
    if not vals:
        return 0
    c = conn()
    c.executemany(
        "INSERT OR REPLACE INTO x_lookups(handle, state, user_id, error, "
        "checked_at) VALUES(?,?,?,?,?)", vals)
    c.commit()
    return len(vals)


def deployer_chain_put(rows: Sequence[dict[str, Any]]) -> int:
    """Remember a chain reading for a batch of addresses.

    `nonce` and `balance_wei` are a slice of state, so the block they were read
    at travels with them. `balance_wei` is written as a string: 9.2 ETH is the
    signed 64-bit ceiling in wei, and every balance above it would break
    silently in an INTEGER.
    """
    vals = []
    for r in rows or []:
        a = r.get("address")
        if not a:
            continue
        bal = r.get("balance_wei")
        vals.append((a, r.get("nonce"),
                     None if bal is None else str(bal),
                     r.get("code"), r.get("state") or "ok",
                     r.get("block"), int(r.get("fetched_at") or time.time()),
                     r.get("error") or ""))
    if not vals:
        return 0
    c = conn()
    c.executemany(
        "INSERT OR REPLACE INTO deployer_chain(address, nonce, balance_wei, "
        "code, state, block, fetched_at, error) VALUES(?,?,?,?,?,?,?,?)", vals)
    c.commit()
    return len(vals)


def deployer_chain_get(addresses: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The cached chain reading per address, for the addresses that have one."""
    addrs = list(dict.fromkeys(a for a in addresses if a))
    if not addrs:
        return {}
    c = conn()
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT * FROM deployer_chain WHERE address IN ({marks})",
                chunk):
            out[row["address"]] = dict(row)
    return out


def deployer_chain_ext_put(rows: Sequence[dict[str, Any]]) -> int:
    """Remember one chain's nonce reading for a batch of addresses.

    `chain_id` is required and a row without one is dropped rather than written
    with a null: it is half the primary key, and a nonce filed under no chain is
    a number that cannot be read back as anything.
    """
    vals = []
    for r in rows or []:
        a, cid = r.get("address"), r.get("chain_id")
        if not a or cid is None:
            continue
        vals.append((a, int(cid), r.get("nonce"), r.get("state") or "ok",
                     r.get("block"), int(r.get("fetched_at") or time.time()),
                     r.get("error") or ""))
    if not vals:
        return 0
    c = conn()
    c.executemany(
        "INSERT OR REPLACE INTO deployer_chain_ext(address, chain_id, nonce, "
        "state, block, fetched_at, error) VALUES(?,?,?,?,?,?,?)", vals)
    c.commit()
    return len(vals)


def deployer_chain_ext_get(addresses: Sequence[str]
                           ) -> dict[str, dict[int, dict[str, Any]]]:
    """{address: {chain_id: row}} for the addresses that have any.

    Keyed by address first because every caller has a list of addresses and no
    opinion about chains: the page draws whatever chains came back, in the order
    the config names them, rather than asking for one at a time.
    """
    addrs = list(dict.fromkeys(a for a in addresses if a))
    if not addrs:
        return {}
    c = conn()
    out: dict[str, dict[int, dict[str, Any]]] = {}
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT * FROM deployer_chain_ext WHERE address IN ({marks})",
                chunk):
            out.setdefault(row["address"], {})[int(row["chain_id"])] = dict(row)
    return out


def sniper_order(rows: Sequence[dict[str, Any]], sort: str = "",
                 sort2: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """Order snipes by the caller's keys, and say which keys actually applied.

    Order is decided in Python for the same reason `x_follows_order` does it:
    two of the keys are not columns of the row - the deployer's launch count and
    its nonce are joined in from other tables - so an ORDER BY would have to
    remember the join in every branch.

    The last key is always (block, log_index). Without it the hundredth row of
    one request is not the hundredth row of the next, and a list that loads by
    scrolling repeats and loses entries - the same failure `x_follows` avoids by
    tie-breaking on `ord`.

    A row whose sort value is unknown sorts last in BOTH directions. A nonce
    nobody has read is not a nonce of zero, and putting it at the top of an
    ascending list would state a fact we do not have.
    """
    keys: list[tuple[str, bool]] = []
    applied: list[str] = []
    for i, name in enumerate((sort, sort2)):
        k = SNIPE_SORTS.get((name or "").strip())
        # A secondary key never applies on its own. It qualifies the primary
        # one - "by lag, then by size" - and with no primary to qualify it would
        # silently replace the default order with whatever the second dropdown
        # happened to hold.
        if k and (i == 0 or keys):
            keys.append(k)
            applied.append(name)
    if not keys:
        keys = [SNIPE_SORTS[SNIPE_SORT_DEFAULT]]
        applied = [SNIPE_SORT_DEFAULT]
    sign = -1 if keys[0][1] else 1

    def key(r: dict[str, Any]):
        out = []
        for field, desc in keys:
            v = r.get(field)
            if v is None:
                out.append((1, 0))
            else:
                out.append((0, (-v if desc else v)))
        out.append((0, sign * (r.get("block") or 0)))
        out.append((0, sign * (r.get("log_index") or 0)))
        return tuple(out)

    return sorted(rows, key=key), applied


def _median(vals: Sequence[float]) -> float | None:
    """The median, or None for an empty set - never 0 for one.

    A median of nothing is not zero, and the page prints the count beside every
    median so that an empty one is visible rather than plausible.
    """
    return statistics.median(vals) if vals else None


def sniper_criteria(rows: Sequence[dict[str, Any]],
                    deps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """How a wallet snipes, from rows already read and deployers already joined.

    Two denominators, and both are reported, because they answer different
    questions and a median taken across both measures somebody else's speed:
    `snipes` counts the launches he was first into, `contested.early_buys`
    counts every launch he entered. Every median here is over the first.

    Purchase size is split by quote asset rather than pooled. `spent_quote` on
    the Snipers tab already summed ETH with USDG and NVDA and produced a total
    in no unit at all; a median has the same problem, so each asset gets its
    own with its own count beside it.
    """
    firsts = [r for r in rows if r.get("first")]
    lost = len(rows) - len(firsts)
    deltas = [int(r["delta"]) for r in firsts if r.get("delta") is not None]
    hist = {"0": 0, "1": 0, "2": 0, "3-5": 0, "6+": 0}
    for d in deltas:
        if d <= 2:
            hist[str(d)] += 1
        elif d <= 5:
            hist["3-5"] += 1
        else:
            hist["6+"] += 1

    by_quote: dict[str, list[float]] = {}
    for r in firsts:
        sym = r.get("quote_symbol") or "?"
        if r.get("quote") is not None:
            # `early_buys.quote` is the amount in the quote token's own smallest
            # unit, so 2.34e16 wei is 0.023 ETH and 45647150 is 45.6 USDG. The
            # decimals are on the row (`tokens.quote_decimals`) and have to be
            # applied here: a median taken over the raw numbers is a figure in
            # no unit at all, which is the same mistake `spent_quote` made.
            dec = r.get("quote_decimals")
            scale = 10 ** (int(dec) if dec is not None else 18)
            by_quote.setdefault(sym, []).append(float(r["quote"]) / scale)
    quote_mix = [{"symbol": s, "n": len(v), "median": _median(v)}
                 for s, v in sorted(by_quote.items(), key=lambda kv: -len(kv[1]))]

    dep_count: dict[str, int] = {}
    fresh = 0
    launches_seen = 0
    for r in firsts:
        d = r.get("deployer")
        if not d:
            continue
        dep_count[d] = dep_count.get(d, 0) + 1
        p = deps.get(d) or {}
        if p.get("first_block") is not None:
            launches_seen += 1
            if p["first_block"] == r.get("launch_block"):
                fresh += 1
    ranked = sorted(dep_count.items(), key=lambda kv: -kv[1])
    total_firsts = len(firsts) or 1

    graded = sum(1 for r in firsts if r.get("graduated"))
    return {
        "denominator": "first",
        "snipes": len(firsts),
        "deployers": len(dep_count),
        "median_delta": _median(deltas),
        "p90_delta": (sorted(deltas)[min(len(deltas) - 1,
                                         int(len(deltas) * 0.9))] if deltas else None),
        "delta_hist": hist,
        "contested": {
            "early_buys": len(rows),
            "firsts": len(firsts),
            "lost_races": lost,
            "win_rate": (len(firsts) / len(rows)) if rows else None,
        },
        "fresh_share": (fresh / launches_seen) if launches_seen else None,
        "fresh_known": launches_seen,
        "graduated_share": (graded / total_firsts) if firsts else None,
        "quote_mix": quote_mix,
        "top_deployer": ({"address": ranked[0][0],
                          "share": ranked[0][1] / total_firsts,
                          "snipes": ranked[0][1]} if ranked else None),
        "top10_share": (sum(n for _, n in ranked[:10]) / total_firsts
                        if ranked else None),
    }


def prune_trades(keep_hours: float = C.TRADE_KEEP_HOURS) -> int:
    """Drop buckets and buyers that fell out of the window.

    Each delete commits on its own rather than the four of them together. The
    two anti-join deletes scan a million rows between them to find a few
    thousand, so the batch is seconds of transaction either way - and this runs
    immediately before the checkpoint asks for the log, on the same connection.
    A statement that fails here now leaves nothing behind for the checkpoint to
    trip over.
    """
    c = conn()
    cutoff = int(time.time() // C.TRADE_BUCKET_SEC) - int(
        keep_hours * 3600 / C.TRADE_BUCKET_SEC)
    n = c.execute("DELETE FROM trade_buckets WHERE bucket < ?",
                  (cutoff,)).rowcount
    c.commit()
    n += c.execute(
        "DELETE FROM trade_buyers WHERE address NOT IN "
        "(SELECT address FROM tokens)").rowcount
    c.commit()
    n += c.execute("DELETE FROM price_points WHERE bucket < ?",
                   (int(time.time() // C.CANDLE_BUCKET_SEC) - int(
                       keep_hours * 3600 / C.CANDLE_BUCKET_SEC),)).rowcount
    c.commit()
    # Positions are per token rather than per time slice, so they leave with
    # the token they belong to, the same way the buyer set does.
    n += c.execute(
        "DELETE FROM wallet_trades WHERE address NOT IN "
        "(SELECT address FROM tokens)").rowcount
    c.commit()
    return n


def _wal_bytes() -> float:
    """Size of the write-ahead log beside the database this thread is on."""
    try:
        return float(os.path.getsize(str(current_db()) + "-wal"))
    except OSError:
        return 0.0


def _wal_fold(c: sqlite3.Connection) -> tuple[int, ...]:
    """Ask SQLite to fold the log and truncate it. Returns its three numbers."""
    return tuple(c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())


def wal_checkpoint() -> tuple[int, ...]:
    """Fold the write-ahead log back into the database and truncate it.

    Returns SQLite's own answer: (busy, frames in the log, frames copied). A
    busy one is not a failure - it means a reader or a writer held the database
    for the moment it asked, and the log was left alone, which is the right
    outcome.

    A checkpoint that copies almost nothing while the log holds thousands of
    frames is the other thing entirely: it is pinned, and it will stay pinned.
    The copy phase is passive even for TRUNCATE, so it stops quietly at the
    oldest snapshot and reports a plain success-shaped tuple - which is how a
    frozen `copied=1001` went unremarked every fifteen minutes for four hours
    while the file reached fifty gigabytes. It warns now.

    The timeout is shortened for this call alone. The connection's own is
    thirty seconds, which is the right patience for a query and the wrong one
    for maintenance: a checkpoint that cannot start should say so in a moment
    rather than hold a thread while a page waits to be answered.

    A log past `WAL_MAX_GIB` is not reported and then left alone. The warning
    added for the fifty-gigabyte log was correct and did not help: it went to
    stderr, and nothing was reading stderr. So the recovery happens here
    instead of being left to whoever reads the line. The stray transaction is
    rolled back first, because that is the usual cause and the cheap fix, and
    the connections are replaced only when the log still will not fold - the
    case where the snapshot belongs to a statement rather than a transaction
    and no rollback can reach it.
    """
    c = conn()
    prev = c.execute("PRAGMA busy_timeout").fetchone()[0]
    try:
        c.execute("PRAGMA busy_timeout=2000")
        at = _wal_fold(c)
        stuck = at[1] - at[2]
        if stuck > C.WAL_STUCK_FRAMES:
            log.warning(
                "wal checkpoint is stuck: %d of %d frames copied (%.1f GB in "
                "the log). Something holds a snapshot at frame %d, so the log "
                "cannot be folded and will only grow. `store.discard()` clears "
                "a transaction a failed statement left open.",
                at[2], at[1], _wal_bytes() / 1073741824.0, at[2] + 1)

        cap = C.WAL_MAX_GIB * 1073741824.0
        if _wal_bytes() > cap:
            over = _wal_bytes()
            rolled = release_snapshots()
            at = _wal_fold(c)
            if _wal_bytes() > cap:
                killed = replace_connections()
                c = conn()
                c.execute("PRAGMA busy_timeout=2000")
                at = _wal_fold(c)
                log.error(
                    "wal log %.1f GB was over the %.1f GB cap and stayed "
                    "over it: rolled back %d transaction(s), replaced %d "
                    "connection(s); now busy=%s frames=%s copied=%s with "
                    "%.1f GB left in the log",
                    over / 1073741824.0, C.WAL_MAX_GIB, rolled, killed,
                    at[0], at[1], at[2], _wal_bytes() / 1073741824.0)
            else:
                log.warning(
                    "wal log %.1f GB was over the %.1f GB cap: rolling back "
                    "%d transaction(s) let it fold, %d frames copied",
                    over / 1073741824.0, C.WAL_MAX_GIB, rolled, at[2])
        return at
    except sqlite3.Error as e:
        log.warning("wal checkpoint: %s", str(e)[:120])
        return (1, -1, -1)
    finally:
        try:
            c.execute("PRAGMA busy_timeout=%d" % prev)
        except sqlite3.Error:
            pass


def recent_logos(limit: int = 200) -> list[str]:
    """Logo uris of the newest tokens, newest first and deduplicated.

    This is what the first page of the grid is about to draw, which is exactly
    the set whose images no browser has ever asked for. Deduplicated here
    rather than in SQL because one launcher with one picture is common, and the
    fetch behind the duplicate is the expensive part.
    """
    rows = conn().execute(
        "SELECT logo FROM tokens "
        "WHERE logo IS NOT NULL AND logo <> '' AND logo <> 'None' "
        "ORDER BY launch_ts DESC, launch_block DESC LIMIT ?",
        (limit,)).fetchall()
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        u = (r["logo"] or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ------------------------------------------------------------------ coin card
def curve_ratio(d: dict) -> float | None:
    """Quote base units per token base unit on the curve right now.

    The same quantity `derive` prices with, pulled out so a position can be
    valued without going through the display shape. Virtual tokens is
    reserved + sellable: supply minus what the curve has already sold.

    A graduated token has no curve left - the sweep zeroes both reserves - so
    this returns None rather than a ratio built from leftovers, which is what
    lets a caller tell "worth nothing" apart from "no longer priceable here".
    """
    if d.get("graduated"):
        return None
    try:
        ph = int(d["phantom_quote"])
        rq = int(d["real_quote_reserve"])
        vt = int(d["reserved_tokens"]) + int(d["sellable_tokens"])
    except (KeyError, TypeError, ValueError):
        return None
    if vt <= 0:
        return None
    return (ph + rq) / vt


def position_value(wt: dict | None, tok: dict) -> dict:
    """What one wallet's trade history in one token is worth, in raw quote.

    Realised and unrealised together: what the sells already took out, plus
    what is still held valued at the curve's present price. Both halves come
    from the same row, so this is a wallet's actual result in that token
    rather than a guess made from its first buy alone.

    `known` is False when the unrealised half cannot be established - a
    graduated token trades somewhere this indexer does not read. Reporting
    only the realised half then is deliberate: it is a true lower bound and a
    caller can say so, where a curve price from swept reserves would be a
    fiction.
    """
    wt = wt or {}
    if wt.get("bought") is None:
        # No row for this wallet in this token yet. Reporting zeros would read
        # as "broke even", which is a claim, where the truth is that we have
        # nothing to go on - usually a token the position backfill has not
        # reached. `pnl` of None is what says so, and callers draw a dash.
        return {"net_tokens": 0.0, "spent": 0.0, "received": 0.0,
                "realized": 0.0, "unrealized": None, "pnl": None,
                "roi_pct": None, "known": False}
    bought = float(wt.get("bought") or 0)
    sold = float(wt.get("sold") or 0)
    spent = float(wt.get("spent") or 0)
    received = float(wt.get("received") or 0)
    net = bought - sold
    out = {
        "net_tokens": max(0.0, net),
        "spent": spent,
        "received": received,
        "realized": received - spent,
        "unrealized": None,
        "pnl": received - spent,
        "roi_pct": None,
        "known": net <= 0,
    }
    if net > 0:
        ratio = curve_ratio(tok)
        if ratio is not None:
            out["unrealized"] = net * ratio
            out["pnl"] = out["realized"] + out["unrealized"]
            out["known"] = True
    if out["known"] and spent > 0:
        out["roi_pct"] = out["pnl"] / spent * 100.0
    return out


def wallet_position(address: str, wallet: str) -> dict[str, Any] | None:
    """One wallet's history in one token, whatever it did with it.

    `token_holders` only returns wallets that still hold, which is the wrong
    set for asking what a sniper made: the ones that took their profit and
    left are exactly the interesting case.
    """
    row = conn().execute(
        "SELECT * FROM wallet_trades WHERE address=? AND wallet=?",
        (address, wallet)).fetchone()
    return dict(row) if row else None


def wallet_trades_for(wallet: str, addresses: Sequence[str]) -> dict[str, dict]:
    """The same rows as `wallet_trade`, for a page of tokens at once.

    One query per page rather than one per row, because a profile renders a
    hundred rows and a hundred round trips through `wallet_trade` is a hundred
    times the work for the same answer. The primary key is (address, wallet), so
    the IN list uses its leading column and the wallet is the residual filter.
    """
    addrs = list(dict.fromkeys(a for a in addresses if a))
    if not addrs or not wallet:
        return {}
    c = conn()
    out: dict[str, dict] = {}
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT * FROM wallet_trades WHERE wallet=? "
                f"AND address IN ({marks})", [wallet, *chunk]):
            out[row["address"]] = dict(row)
    return out


def price_series(address: str, since_ts: int | None = None,
                 limit: int = 1440) -> list[dict]:
    """The token's chart, oldest first, capped to the most recent `limit`."""
    sql = ("SELECT bucket, ts, quote, tokens, buys, sells FROM price_points "
           "WHERE address=?")
    params = [address]
    if since_ts:
        sql += " AND ts>=?"
        params.append(since_ts)
    sql += " ORDER BY bucket DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn().execute(sql, params).fetchall()]
    rows.reverse()
    return rows


def token_holders(address: str, limit: int = 50,
                  offset: int = 0) -> tuple[list[dict], int]:
    """Wallets still holding, largest position first.

    Holding means bought more than sold: the curve burns on a sell, so a
    wallet that has sold everything it bought is gone from the supply rather
    than sitting in it at zero.
    """
    c = conn()
    total = int(c.execute(
        "SELECT COUNT(*) FROM wallet_trades WHERE address=? AND bought>sold",
        (address,)).fetchone()[0])
    rows = c.execute(
        "SELECT wallet, bought, sold, spent, received, first_ts, last_ts "
        "FROM wallet_trades WHERE address=? AND bought>sold "
        "ORDER BY (bought - sold) DESC LIMIT ? OFFSET ?",
        (address, limit, offset)).fetchall()
    return [dict(r) for r in rows], total


def holder_totals(address: str) -> dict:
    """How much of the supply is actually held, and by how many wallets."""
    row = conn().execute(
        "SELECT COUNT(*) AS holders, "
        "       COALESCE(SUM(bought - sold), 0) AS held "
        "FROM wallet_trades WHERE address=? AND bought>sold",
        (address,)).fetchone()
    return {"holders": int(row["holders"] or 0), "held": float(row["held"] or 0)}



# ------------------------------------------------------------------ trade reads
_SNIPE_FIELDS = ("tr.first_buy_block", "tr.first_buy_ts", "tr.first_buyer",
                 "tr.first_buy_quote", "tr.first_buy_tokens",
                 "tr.first_buy_tx", "tr.snipe_counted")


def trades_for(addresses: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Trade totals plus raw first-buy fields, keyed by token address."""
    if not addresses:
        return {}
    out: dict[str, dict[str, Any]] = {}
    c = conn()
    addrs = list(dict.fromkeys(addresses))
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT tr.*, t.launch_block, t.deployer, t.launch_tx, "
                f"       t.graduation_threshold "
                f"FROM trades tr JOIN tokens t ON t.address = tr.address "
                f"WHERE tr.address IN ({qs})", chunk):
            out[row["address"]] = dict(row)
    return out


def early_buys_for(addresses: Sequence[str],
                   per_token: int = 25) -> dict[str, list[dict[str, Any]]]:
    if not addresses:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    c = conn()
    addrs = list(dict.fromkeys(addresses))
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT * FROM early_buys WHERE address IN ({qs}) "
                f"ORDER BY address, block, log_index", chunk):
            out.setdefault(row["address"], []).append(dict(row))
    if per_token:
        for k, v in out.items():
            out[k] = v[:per_token]
    return out


def recent_trades(address: str, limit: int = 40) -> list[dict[str, Any]]:
    """The most recent trades, read back out of the buckets.

    Individual trades are not kept, only their aggregates, so a 5 minute
    bucket is the finest granularity available. That is enough to show that
    something is trading right now.
    """
    rows = conn().execute(
        "SELECT * FROM trade_buckets WHERE address=? "
        "ORDER BY bucket DESC LIMIT ?", (address, limit)).fetchall()
    out = []
    for r in rows:
        ts = r["bucket"] * C.TRADE_BUCKET_SEC
        if r["buys"]:
            out.append({"ts": ts, "side": "buy", "count": r["buys"],
                        "quote": r["buy_volume"]})
        if r["sells"]:
            out.append({"ts": ts, "side": "sell", "count": r["sells"],
                        "quote": r["sell_volume"]})
    return sorted(out, key=lambda x: x["ts"], reverse=True)[:limit]


_VOL_SORTS = {
    # Unqualified on purpose: the aggregate runs in a subquery, so by the time
    # the sort is applied the token columns are flat names rather than t.*.
    "volume": "vol DESC",
    "buys": "buys DESC",
    "sells": "sells DESC",
    "net": "net DESC",
    "newest": "launch_ts DESC",
}


def volume_rows(window_sec: int | None, sort: str = "volume", limit: int = 100,
                offset: int = 0, q: str | None = None, quote: str | None = None,
                graduated: bool | None = None) -> list[dict[str, Any]]:
    """Tokens ranked by traded volume over a window, with their token metadata.

    `window_sec = None` means everything indexed so far, which reads the
    running totals; anything shorter reads the time buckets.
    """
    where, params = [], []
    if q:
        where.append("(t.symbol LIKE ? OR t.name LIKE ? OR t.address LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if quote:
        where.append("t.quote_symbol = ?")
        params.append(quote)
    if graduated is not None:
        where.append("t.graduated = ?")
        params.append(1 if graduated else 0)

    if window_sec is None:
        agg = ("COALESCE(tr.buy_volume,0) AS buyvol, "
               "COALESCE(tr.sell_volume,0) AS sellvol, "
               "COALESCE(tr.buys,0) AS buys, COALESCE(tr.sells,0) AS sells, "
               "COALESCE(tr.buyers,0) AS buyers, tr.last_trade_ts AS ltt")
        src = "trades tr JOIN tokens t ON t.address = tr.address"
    else:
        bucket = int(time.time() // C.TRADE_BUCKET_SEC) - int(
            window_sec / C.TRADE_BUCKET_SEC)
        where.append("b.bucket >= ?")
        params.append(bucket)
        agg = ("SUM(b.buy_volume) AS buyvol, SUM(b.sell_volume) AS sellvol, "
               "SUM(b.buys) AS buys, SUM(b.sells) AS sells, "
               "COALESCE((SELECT buyers FROM trades x WHERE x.address=b.address),0) "
               "AS buyers, MAX(b.bucket)*%d AS ltt" % C.TRADE_BUCKET_SEC)
        src = "trade_buckets b JOIN tokens t ON t.address = b.address"

    # vol and net have to be computed one level out. SQLite resolves an alias
    # in ORDER BY but not in the same SELECT list, so referring to buyvol
    # alongside its own definition is an error rather than a shorthand.
    sql = (f"SELECT *, (buyvol + sellvol) AS vol, (buyvol - sellvol) AS net "
           f"FROM (SELECT t.*, {agg} FROM {src} ")
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    if window_sec is not None:
        sql += "GROUP BY b.address "
    sql += ") "
    sql += f"ORDER BY {_VOL_SORTS.get(sort, _VOL_SORTS['volume'])} "
    sql += "LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def sniper_hits(addresses: Sequence[str]) -> dict[str, int]:
    """How many launches each wallet was first into, for a batch of wallets.

    The bot flag is a property of the wallet that raced in, which is not
    always the wallet that bought first overall: when the deployer bought
    inside their own launch, the racer is somebody else entirely. Looking the
    count up by `trades.first_buyer` would credit the deployer's zero and lose
    the signal on exactly the tokens where a race happened.
    """
    out: dict[str, int] = {}
    addrs = list(dict.fromkeys(a for a in addresses if a))
    if not addrs:
        return out
    c = conn()
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT address, hits FROM sniper_wallets "
                f"WHERE address IN ({qs})", chunk):
            out[row["address"]] = int(row["hits"] or 0)
    return out


def snipe_candidates(limit: int = 4000, offset: int = 0,
                     since_ts: int | None = None, q: str | None = None,
                     quote: str | None = None) -> list[dict[str, Any]]:
    """Rows for the Snipers tab, before the score is computed in Python.

    SQL orders by how fast someone other than the deployer got in, which is
    the strongest single part of the score, so the window only has to cover
    rows that could actually rank. Filtering by score or bot flag happens
    after scoring, which is why the cap here is generous.
    """
    where, params = [], []
    if since_ts:
        where.append("t.launch_ts >= ?")
        params.append(since_ts)
    if q:
        where.append("(t.symbol LIKE ? OR t.name LIKE ? OR t.address LIKE ? "
                     "OR tr.first_buyer LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like, like]
    if quote:
        where.append("t.quote_symbol = ?")
        params.append(quote)
    sql = ("SELECT t.address, t.symbol, t.name, t.logo, t.launch_block, "
           "       t.launch_ts, t.deployer, t.launch_tx, "
           "       t.graduation_threshold, t.quote_symbol, t.quote_decimals, "
           "       t.price_quote, t.mcap_usd, t.progress_pct, t.graduated, "
           "       t.quote_address, t.mcap_quote, "
           "       tr.buys, tr.sells, tr.buy_volume, tr.sell_volume, "
           "       tr.buyers, tr.last_trade_ts, "
           "       tr.first_buy_block, tr.first_buy_ts, tr.first_buyer, "
           "       tr.first_buy_quote, tr.first_buy_tokens, tr.first_buy_tx "
           "FROM trades tr JOIN tokens t ON t.address = tr.address ")
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += ("ORDER BY (tr.first_buy_block - t.launch_block) ASC, "
            "tr.first_buy_quote DESC LIMIT ? OFFSET ?")
    params += [limit, offset]
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def early_buyer_counts(addresses: Sequence[str]) -> dict[str, int]:
    """Distinct early buyers per token, for the concentration part of a score."""
    if not addresses:
        return {}
    out: dict[str, int] = {}
    c = conn()
    addrs = list(dict.fromkeys(addresses))
    for i in range(0, len(addrs), 400):
        chunk = addrs[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT address, COUNT(DISTINCT buyer) AS n FROM early_buys "
                f"WHERE address IN ({qs}) GROUP BY address", chunk):
            out[row["address"]] = row["n"]
    return out


def wallet_early_buys(buyer: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT e.address, e.block, e.quote, e.ts, e.buyer, e.tx, "
        "       t.symbol, t.launch_block, t.launch_ts, t.deployer, "
        "       t.launch_tx, t.graduation_threshold, t.quote_decimals, "
        "       t.quote_symbol "
        "FROM early_buys e JOIN tokens t ON t.address = e.address "
        "WHERE e.buyer = ? ORDER BY e.block DESC LIMIT ?",
        (buyer, limit)).fetchall()
    return [dict(r) for r in rows]


def wallet_early_buy_count(buyer: str) -> int:
    """How many early buys this wallet actually has.

    wallet_early_buys above stops at `limit` rows because the panel renders a
    table, not a ledger. Reporting the length of that page as the total made a
    wallet with 440 of them claim exactly 100, which is the shape of a cap and
    not of a measurement.
    """
    row = conn().execute(
        "SELECT COUNT(*) AS n FROM early_buys WHERE buyer = ?",
        (buyer,)).fetchone()
    return int(row["n"] or 0) if row else 0


def wallet_launched(address: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT t.address, t.symbol, t.name, t.logo, t.launch_block, "
        "       t.launch_ts, t.mcap_usd, t.progress_pct, t.graduated, "
        "       t.price_quote, t.quote_symbol, t.graduation_threshold, "
        "       t.launch_tx, t.deployer, "
        "       COALESCE(tr.buy_volume,0) + COALESCE(tr.sell_volume,0) AS vol, "
        "       tr.buys, tr.sells, tr.first_buy_block, tr.first_buyer, "
        "       tr.first_buy_quote, tr.first_buy_tx "
        "FROM tokens t LEFT JOIN trades tr ON tr.address = t.address "
        "WHERE t.deployer = ? ORDER BY t.launch_block DESC LIMIT ?",
        (address, limit)).fetchall()
    return [dict(r) for r in rows]


def sniper_wallet_list(limit: int = 100) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM sniper_wallets ORDER BY hits DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ copy plans
def _plan(r: sqlite3.Row) -> dict[str, Any]:
    import json
    d = dict(r)
    d["fields"] = json.loads(d.get("fields") or "{}")
    d["extra"] = json.loads(d.get("extra") or "[]")
    return d


def copy_list(limit: int = 200) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM copy_plans ORDER BY updated_at DESC LIMIT ?",
        (limit,)).fetchall()
    return [_plan(r) for r in rows]


def copy_get(plan_id: int) -> dict[str, Any] | None:
    r = conn().execute("SELECT * FROM copy_plans WHERE id=?",
                       (plan_id,)).fetchone()
    return _plan(r) if r else None


def copy_save(plan_id: int | None, fields: dict[str, Any],
              extra: list[dict[str, Any]], notes: str,
              source_address: str | None,
              source_symbol: str | None) -> int:
    import json
    now = int(time.time())
    c = conn()
    if plan_id:
        c.execute(
            "UPDATE copy_plans SET fields=?, extra=?, notes=?, updated_at=? "
            "WHERE id=?",
            (json.dumps(fields), json.dumps(extra), notes, now, plan_id))
    else:
        cur = c.execute(
            "INSERT INTO copy_plans(created_at,updated_at,status,"
            "source_address,source_symbol,fields,extra,notes) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (now, now, "draft", source_address, source_symbol,
             json.dumps(fields), json.dumps(extra), notes))
        plan_id = cur.lastrowid
    c.commit()
    return plan_id


def copy_delete(plan_id: int) -> bool:
    c = conn()
    cur = c.execute("DELETE FROM copy_plans WHERE id=?", (plan_id,))
    c.commit()
    return bool(cur.rowcount)


def copy_status(plan_id: int, status: str, tx_hash: str | None,
                error: str | None) -> bool:
    c = conn()
    cur = c.execute(
        "UPDATE copy_plans SET status=?, tx_hash=COALESCE(?,tx_hash), "
        "error=?, updated_at=? WHERE id=?",
        (status, tx_hash, error, int(time.time()), plan_id))
    c.commit()
    return bool(cur.rowcount)


def known_symbols(symbol: str) -> int:
    return conn().execute(
        "SELECT COUNT(*) FROM tokens WHERE symbol = ? COLLATE NOCASE",
        (symbol,)).fetchone()[0]


# ------------------------------------------------------------------ wallets
def wallet_list() -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM wallets ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


def wallet_add(address: str, label: str | None) -> None:
    c = conn()
    c.execute(
        "INSERT INTO wallets(address,label,added_at) VALUES(?,?,?) "
        "ON CONFLICT(address) DO UPDATE SET label=excluded.label",
        (address, label, int(time.time())))
    c.commit()


def wallet_remove(address: str) -> bool:
    c = conn()
    cur = c.execute("DELETE FROM wallets WHERE address=?", (address,))
    c.commit()
    return bool(cur.rowcount)


# -------------------------------------------------------------- key wallets
# The secret is selected by its own function and never by the list, so the one
# query that feeds the table cannot leak a key into a response that was only
# meant to draw rows. That reads as a promise below and it is kept literally:
# the list does not name the column, and the mask it draws was computed when
# the key went in.
def keywallet_list() -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT address, label, mask, added_at FROM key_wallets "
        "ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


def keywallet_get(address: str) -> dict[str, Any] | None:
    """The row without its key. Whoever needs the key asks for it by name."""
    row = conn().execute(
        "SELECT address, label, mask, added_at FROM key_wallets "
        "WHERE address=?", (address,)).fetchone()
    return dict(row) if row else None


def keywallet_blobs() -> list[str]:
    """Every stored value, for the one caller that has to check a passphrase
    against one of them. Not for anything that draws: this is a list of
    keystores, not of wallets."""
    return [r["secret"] for r in
            conn().execute("SELECT secret FROM key_wallets").fetchall()]


def keywallet_secret(address: str) -> str | None:
    """The only place a key comes back out, and it needs an open vault.

    A row that is missing answers None; a vault that is shut raises `Locked`
    and a blob that will not open raises `WrapError`, so the caller can say
    which of the three happened instead of reporting a wallet that is not
    there.
    """
    row = conn().execute(
        "SELECT secret FROM key_wallets WHERE address=?", (address,)).fetchone()
    return keysafe.unwrap(row["secret"]) if row else None


def keywallet_add(address: str, label: str | None, secret: str) -> None:
    """Add, or update the label of, the wallet this key belongs to.

    Keyed by address rather than by key: pasting the same wallet twice with a
    different label is a rename, and pasting the same key twice should not
    produce two rows nobody can tell apart.

    The encryption is done here rather than by the caller, so this is the only
    way a key reaches the table and no path exists that writes one in the
    clear. It also means adding a key needs an open vault, which is the
    intended cost rather than an accident: a key that could be stored without
    the passphrase could be read without it too. `wrap` is called before the
    statement is built, so a locked vault refuses before anything is executed.
    """
    c = conn()
    c.execute(
        "INSERT INTO key_wallets(address,label,secret,mask,added_at) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(address) DO UPDATE SET label=excluded.label, "
        "secret=excluded.secret, mask=excluded.mask",
        (address, label, keysafe.wrap(secret), keysafe.mask(secret),
         int(time.time())))
    c.commit()


def keywallet_repair() -> int:
    """Bring rows written before the vault up to it, and say how many.

    Two things can be wrong with an older row, and both of them come from the
    same place: a database restored out of a backup made before any of this
    existed. A key can be sitting in the clear, and a keystore can be sitting
    without the mask that was added later. Both are fixed here, and only here,
    because both need the passphrase and this runs at the moment it is given.

    The update runs with secure_delete on and the log is truncated after it, so
    what was in the clear does not stay behind in a freed page or in the
    write-ahead log: otherwise the file would keep a copy of the very thing
    this is for, readable by anyone who reads the bytes instead of the table.
    """
    rows = conn().execute("SELECT address, secret, mask FROM key_wallets").fetchall()
    plain = [(r["address"], r["secret"]) for r in rows
             if r["secret"] and not keysafe.is_wrapped(r["secret"])]
    bare = [r["address"] for r in rows
            if keysafe.is_wrapped(r["secret"]) and not r["mask"]]
    if not plain and not bare:
        return 0
    c = conn()
    c.execute("PRAGMA secure_delete=ON")
    try:
        if plain:
            c.executemany(
                "UPDATE key_wallets SET secret=?, mask=? WHERE address=?",
                [(keysafe.wrap(s), keysafe.mask(s), a) for a, s in plain])
        for addr in bare:
            key = keywallet_secret(addr)
            if key:
                c.execute("UPDATE key_wallets SET mask=? WHERE address=?",
                          (keysafe.mask(key), addr))
        c.commit()
    finally:
        c.execute("PRAGMA secure_delete=OFF")
    if plain:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    log.info("keys: %d encrypted in place, %d masks filled in",
             len(plain), len(bare))
    return len(plain) + len(bare)


def keywallet_label(address: str, label: str | None) -> bool:
    c = conn()
    cur = c.execute("UPDATE key_wallets SET label=? WHERE address=?",
                    (label, address))
    c.commit()
    return bool(cur.rowcount)


def keywallet_remove(address: str) -> bool:
    c = conn()
    cur = c.execute("DELETE FROM key_wallets WHERE address=?", (address,))
    c.commit()
    # Forgetting the key forgets what was known about the address with it. The
    # two rows are the same wallet, and a cache left behind would be a balance
    # waiting to be re-attached to a key nobody has any more.
    keywallet_chain_drop([address])
    return bool(cur.rowcount)


# ------------------------------------------- what the chain said about a key
# The numbers the Wallets tab draws, cached between readings. They are written
# by a background read and read by a request that must never block on a node,
# so everything here is deliberately small and local: one UPSERT, one SELECT,
# one DELETE, and no arithmetic that a second writer could race.
_CHAIN_UPSERT = """
INSERT INTO key_wallet_chain(address, rh_wei, rh_at, rh_err,
                             arb_wei, arb_at, arb_err, tried_at)
VALUES(?,?,?,?,?,?,?,?)
ON CONFLICT(address) DO UPDATE SET
  rh_wei   = COALESCE(excluded.rh_wei,  key_wallet_chain.rh_wei),
  rh_at    = COALESCE(excluded.rh_at,   key_wallet_chain.rh_at),
  arb_wei  = COALESCE(excluded.arb_wei, key_wallet_chain.arb_wei),
  arb_at   = COALESCE(excluded.arb_at,  key_wallet_chain.arb_at),
  rh_err   = excluded.rh_err,
  arb_err  = excluded.arb_err,
  tried_at = excluded.tried_at
"""


def _chain_chunks(items, size=400):
    """The addresses, deduped, in batches small enough for one IN clause."""
    addrs = list(dict.fromkeys(a for a in items if a))
    return [addrs[i:i + size] for i in range(0, len(addrs), size)]


def keywallet_chain_put(addresses: Sequence[str], rh: dict[str, Any],
                        arb: dict[str, Any], rh_error: str = "",
                        arb_error: str = "") -> int:
    """Remember what one reading said about a batch of stored wallets.

    `addresses` is what was asked about, passed in rather than taken from the
    two dictionaries, because absence in them is overloaded: `chain.balances`
    returns every address it was given and puts `None` on a sub-call that
    failed, while `bridge.arb_balances` returns `{}` for a batch that did not
    happen at all and says nothing about the rest. Only the caller knows what it
    asked for, and an address that was asked about and came back missing is a
    refusal to write down - not a row to leave alone.

    The COALESCE in the statement is the rule, not decoration: a column this
    reading gave no number for keeps the old number and its old stamp exactly
    where they were. A failed read has no right to destroy a good number, and
    this is the one place it could. The refusals are *not* coalesced - a refusal
    is the last word about a column rather than a value, and a node that started
    answering again must be able to clear yesterday's sentence. `tried_at` is
    always written: every put is an attempt.
    """
    chunks = _chain_chunks(addresses)
    if not chunks:
        return 0
    now = int(time.time())
    rh, arb = rh or {}, arb or {}
    # The same sentence serves both halves, so a caller that knows why a half
    # failed passes it and a per-address miss falls back to the plain one.
    rmiss = rh_error or "no balance came back for this wallet"
    amiss = arb_error or "no balance came back for this wallet"
    c = conn()
    written = 0
    for chunk in chunks:
        vals = []
        for a in chunk:
            rv, av = rh.get(a), arb.get(a)
            vals.append((
                a,
                # str() is what makes a zero a reading: str(0) is "0", which
                # COALESCE writes, while a refusal is a None, which it skips.
                None if rv is None else str(rv),
                now if rv is not None else None,
                "" if rv is not None else rmiss,
                None if av is None else str(av),
                now if av is not None else None,
                "" if av is not None else amiss,
                now,
            ))
        c.executemany(_CHAIN_UPSERT, vals)
        written += len(vals)
    c.commit()
    return written


def keywallet_chain_get(addresses: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The cached reading per address, for the addresses that have one.

    A missing address means "nobody has read this one", which is a different
    thing from a row whose number is null ("a reading was made and gave no
    number") - the page draws both as a dash and the difference is what the
    retry policy turns on.
    """
    out: dict[str, dict[str, Any]] = {}
    c = conn()
    for chunk in _chain_chunks(addresses):
        marks = ",".join("?" * len(chunk))
        for row in c.execute(
                f"SELECT * FROM key_wallet_chain WHERE address IN ({marks})",
                chunk):
            out[row["address"]] = dict(row)
    return out


def keywallet_chain_drop(addresses: Sequence[str]) -> int:
    """Forget the cached reading for these addresses. Returns how many went.

    Two callers. Forgetting a key drops its row, and so does a sweep that just
    moved money: the cache cannot know whether the balances moved with it, so
    the row is dropped rather than guessed at. The address goes back to "nobody
    has read this one", the table draws a dash for one tick, and the next draw
    is due - so the number is back within a second.
    """
    n = 0
    c = conn()
    for chunk in _chain_chunks(addresses):
        marks = ",".join("?" * len(chunk))
        n += c.execute(
            f"DELETE FROM key_wallet_chain WHERE address IN ({marks})",
            chunk).rowcount
    c.commit()
    return n


def deployer_counts(addresses: Sequence[str]) -> dict[str, int]:
    """How many indexed launches each of these wallets made, in one query.

    Deliberately the indexed count rather than the chain's own
    deployerTokenCount: that is one eth_call per wallet, and the Wallets tab
    draws a row per stored key. The true lifetime figure is on the detail
    panel, which reads /api/wallet/{address} and gets it from the factory.
    """
    addrs = [a for a in addresses if a]
    if not addrs:
        return {}
    marks = ",".join("?" * len(addrs))
    rows = conn().execute(
        f"SELECT deployer, COUNT(*) AS n FROM tokens "
        f"WHERE deployer IN ({marks}) GROUP BY deployer", addrs).fetchall()
    return {r["deployer"]: int(r["n"]) for r in rows}


def early_buy_counts(addresses: Sequence[str]) -> dict[str, int]:
    """The same, for early buys. One query for the whole list."""
    addrs = [a for a in addresses if a]
    if not addrs:
        return {}
    marks = ",".join("?" * len(addrs))
    rows = conn().execute(
        f"SELECT buyer, COUNT(*) AS n FROM early_buys "
        f"WHERE buyer IN ({marks}) GROUP BY buyer", addrs).fetchall()
    return {r["buyer"]: int(r["n"]) for r in rows}



# ------------------------------------------------------------------- x side
# Everything the dashboard knows about X accounts: the profiles it has seen,
# the follow edges it has walked, and how far each walk got. The network itself
# lives in xsource.py - nothing here opens a socket, which is what lets the
# whole reading half be tested without X being up.


def x_users_put(users: Sequence[dict[str, Any]], now: int | None = None) -> int:
    """Remember a batch of X accounts. One statement, no commit - see x_user_put.

    Batched because a page of a followings walk is two hundred accounts, and a
    commit each would be two hundred commits per page - which is exactly the
    thing the streaming design is trying not to do.
    """
    rows = []
    at = now or int(time.time())
    for u in users or []:
        if not u.get("id"):
            continue
        rows.append((str(u["id"]), (u.get("handle") or "").lower() or None,
                     u.get("name"), u.get("bio"), u.get("followers"),
                     u.get("following"), u.get("statuses"), u.get("listed"),
                     u.get("location"), u.get("website"), u.get("avatar"),
                     u.get("banner"), 1 if u.get("verified") else 0,
                     1 if u.get("blue") else 0,
                     1 if u.get("protected") else 0, u.get("created_ts"), at))
    if not rows:
        return 0
    conn().executemany(
        "INSERT OR REPLACE INTO x_users(id, handle, name, bio, followers, "
        "following, statuses, listed, location, website, avatar, banner, "
        "verified, blue, protected, created_ts, fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def x_user_put(u: dict[str, Any], now: int | None = None) -> None:
    """Remember one X account, replacing whatever was known about it.

    Keyed on the id X gives the account, because a screen name is not stable -
    it can be changed, and the one that was given up can be taken by somebody
    else. The handle is stored lowercased for the same reason `handles.norm`
    lowercases: it is the key everything joins on.
    """
    if x_users_put([u], now):
        conn().commit()


def x_user_get(handle: str = "", user_id: str = "") -> dict[str, Any] | None:
    """One stored profile, by handle or by id. None if never looked up."""
    if user_id:
        row = conn().execute("SELECT * FROM x_users WHERE id=?",
                             (str(user_id),)).fetchone()
    elif handle:
        row = conn().execute("SELECT * FROM x_users WHERE handle=?",
                             ((handle or "").strip().lower().lstrip("@"),)).fetchone()
    else:
        return None
    return dict(row) if row else None


def x_follows_put(owner_id: str, users: Sequence[dict[str, Any]],
                  seen_at: int | None = None) -> int:
    """Append one page of a followings walk, and commit it.

    Committed here rather than once at the end of the walk, because the page
    reads this table while the walk is still running: the whole point of the
    list filling in batches is that a finished page is visible immediately, so
    the delay between X answering and the row appearing is one commit.

    The ord is read from the database rather than counted by the caller. A
    counter in the walk's memory would be wrong the moment a walk is resumed or
    restarted, and wrong in the worst way: the ord it hands out would collide
    with a row that already exists, and `INSERT OR IGNORE` would drop a person
    who is genuinely new because somebody else was already sitting at that ord.
    Reading MAX(ord) costs an index lookup and cannot drift.

    A row already known is skipped by the unique index on (owner_id, target_id)
    and keeps the ord it had, so re-walking a list extends it instead of
    reshuffling what has already been shown. Its seen_at is still refreshed,
    which is what makes unfollows visible after a walk that finished.

    The page's accounts and the edges pointing at them go in as one commit. Two
    commits would mean a page whose rows are on screen but whose names are not,
    which renders as a column of blank people for the length of a write - and
    being visible before the walk ends is the entire point of committing here.
    """
    if not owner_id or not users:
        return 0
    now = seen_at or int(time.time())
    ids = [str(u["id"]) for u in users if u.get("id")]
    if not ids:
        return 0

    c = conn()
    x_users_put(users, now)
    ord_ = x_follows_next_ord(owner_id)
    rows = [(owner_id, ord_ + i, tid, now) for i, tid in enumerate(ids)]
    c.executemany("INSERT OR IGNORE INTO x_follows(owner_id, ord, target_id, "
                  "seen_at) VALUES(?,?,?,?)", rows)
    # Everything on this page has now been seen, whether it was new or not.
    # Batched because a page is 200 ids and the variable limit is 999.
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        c.execute("UPDATE x_follows SET seen_at=? WHERE owner_id=? "
                  "AND target_id IN (%s)" % ",".join("?" * len(part)),
                  [now, owner_id] + part)
    c.commit()
    # The page size, not the insert count. The caller reports "parsed" to the
    # page, and the number it wants is how many people this walk has covered -
    # a re-walk covers the same people again and must not look like it found
    # nothing. How many rows there are to show is x_follows_count's question.
    return len(rows)


def x_follows_next_ord(owner_id: str) -> int:
    """Where the next appended row goes.

    Zero for a list never walked; for a re-walk, past everything already there,
    so the rows that arrive now land after the rows that arrived last time and
    nothing already on screen moves.
    """
    row = conn().execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM x_follows "
                         "WHERE owner_id=?", (owner_id,)).fetchone()
    return int(row[0] or 0)


def x_follows_page(owner_id: str, offset: int = 0,
                   limit: int = 100) -> list[dict[str, Any]]:
    """One page of a stored followings list, in the order X gave them.

    Ordered by ord and not by anything read out of the profile, because ord is
    the only ordering that stays put while the rest of the list is being
    written. Reads are cheap: the key is (owner_id, ord), so this is a range
    scan of exactly the rows asked for.
    """
    return [dict(r) for r in conn().execute(
        "SELECT f.ord, f.target_id, f.seen_at, "
        "       u.handle, u.name, u.bio, u.followers, u.following, "
        "       u.avatar, u.banner, u.blue, u.verified, u.protected, "
        "       u.website, u.location "
        "FROM x_follows f LEFT JOIN x_users u ON u.id = f.target_id "
        "WHERE f.owner_id=? ORDER BY f.ord LIMIT ? OFFSET ?",
        (owner_id, limit, offset))]


def x_follows_count(owner_id: str) -> int:
    row = conn().execute("SELECT COUNT(*) FROM x_follows WHERE owner_id=?",
                         (owner_id,)).fetchone()
    return int(row[0] or 0)


# The keys the followings list can be ordered by, as (field, descending). Every
# name is here with both directions rather than a separate `dir` parameter, which
# is this project's habit: `newest` and `oldest` above are one key each, not a key
# and a flag. An unknown name is not an error and not a 500 - it falls out of the
# dict and the list stays in arrival order, the same way an unknown volume sort
# falls back to volume.
FOLLOW_SORTS: dict[str, tuple[str, bool]] = {
    "followers_desc": ("followers", True),
    "followers_asc":  ("followers", False),
    "launches_desc":  ("launches", True),
    "launches_asc":   ("launches", False),
    "deployers_desc": ("deployers", True),
    "deployers_asc":  ("deployers", False),
}


def x_follows_order(owner_id: str, sort: str = "",
                    sort2: str = "") -> list[int]:
    """The owner's follows, ordered by the caller's keys, as a list of ord.

    Order is decided here rather than in SQL because two of the three keys are
    not columns: the launch and deployer counts are counted from `token_handles`
    and `tokens`, and a page of a sorted list needs them for every row of the
    owner, not just for the hundred being drawn. Sorting on them in SQLite means
    either a correlated subquery per row or a join whose counts then have to
    agree with `handles_for` - two chances to be subtly wrong. Counting them
    once with `handle_stats` and ordering a list of dicts has neither.

    `ord` is always the last key, ascending. Not a detail: without it two rows
    with equal counts come back in whatever order the sort happens to leave
    them, so the hundredth row of one request is not the hundredth of the next
    and the scroll repeats some people and skips others. That is the same
    failure the (owner_id, ord) key exists to prevent, arriving by another road.

    A row whose handle has no launches is not missing from the answer - it
    sorts as zero, which is what it is.

    Returns every ord the owner has, in order, so the caller can slice it. The
    list is not cached: five thousand rows cost about 150 ms to order, and a
    cache would have to be invalidated by a walk that is still writing.
    """
    rows = conn().execute(
        "SELECT f.ord, u.handle, u.followers "
        "FROM x_follows f LEFT JOIN x_users u ON u.id = f.target_id "
        "WHERE f.owner_id=?", (owner_id,)).fetchall()
    keys = [FOLLOW_SORTS[s] for s in (sort, sort2) if s in FOLLOW_SORTS]
    if not keys:
        return [int(r["ord"]) for r in rows]

    stats = handle_stats([r["handle"] for r in rows])

    def key(r):
        h = handles.norm(r["handle"])
        st = stats.get(h) if h else None
        f = {"followers": int(r["followers"] or 0),
             "launches": int((st or {}).get("launches", 0)),
             "deployers": int((st or {}).get("deployers", 0))}
        # Descending is negation because every key here is a count and counts
        # are not negative. That is what makes two directions mixable in one
        # tuple without a branch per key.
        return tuple(-f[name] if desc else f[name]
                     for name, desc in keys) + (int(r["ord"]),)

    return [int(r["ord"]) for r in sorted(rows, key=key)]


def x_follows_rows_by_ord(owner_id: str,
                          ords: Iterable[int]) -> list[dict[str, Any]]:
    """The rows for a list of ord values, in the order the caller gave them.

    `x_follows_page` cannot serve a sorted page: its offset counts rows in the
    table's own order, not in the caller's. So the order is built once by
    `x_follows_order`, sliced, and the rows are fetched by key here and put back
    into the order they were asked for.

    Same columns as `x_follows_page`, because the page draws them the same way.
    An ord this owner does not have is absent from the answer rather than an
    error, which is what an offset past the end does too.
    """
    want = [int(o) for o in ords]
    if not want:
        return []
    got: dict[int, dict[str, Any]] = {}
    c = conn()
    for i in range(0, len(want), 400):
        part = want[i:i + 400]
        for r in c.execute(
            "SELECT f.ord, f.target_id, f.seen_at, "
            "       u.handle, u.name, u.bio, u.followers, u.following, "
            "       u.avatar, u.banner, u.blue, u.verified, u.protected, "
            "       u.website, u.location "
            "FROM x_follows f LEFT JOIN x_users u ON u.id = f.target_id "
            "WHERE f.owner_id=? AND f.ord IN (%s)"
            % ",".join("?" * len(part)), [owner_id] + part):
            got[int(r["ord"])] = dict(r)
    return [got[o] for o in want if o in got]


def x_list_put(owner_id: str, **fields: Any) -> None:
    """Record how far a walk got. Every field is optional except the id.

    This row is what stops half a list from reading like a whole one: `users`
    against `total` is the difference, and without it a walk that stopped after
    three hundred of five thousand is indistinguishable from an account that
    follows three hundred people.
    """
    if not owner_id:
        return
    have = {r["name"] for r in conn().execute("PRAGMA table_info(x_lists)")}
    keys = [k for k in fields if k in have]
    c = conn()
    if not c.execute("SELECT 1 FROM x_lists WHERE owner_id=?",
                     (owner_id,)).fetchone():
        c.execute("INSERT INTO x_lists(owner_id) VALUES(?)", (owner_id,))
    if keys:
        c.execute("UPDATE x_lists SET %s WHERE owner_id=?"
                  % ",".join(f"{k}=?" for k in keys),
                  [fields[k] for k in keys] + [owner_id])
    c.commit()


def x_list_get(owner_id: str) -> dict[str, Any] | None:
    row = conn().execute("SELECT * FROM x_lists WHERE owner_id=?",
                         (owner_id,)).fetchone()
    return dict(row) if row else None


def x_lists_unstick(now: int | None = None) -> int:
    """Mark walks that were still running when the process stopped.

    A walk lives in memory: the thread that writes `state="running"` here is the
    same thread that later writes the state it ended in. If the process goes away
    mid-walk nothing is left to write that ending, and the row says "running"
    forever. On the page that reads as a walk still in flight - a list that will
    never grow, a progress line waiting for pages that cannot arrive, and the
    sort controls waiting for an ending that is never coming.

    Called once at startup, where the answer is certain: nothing is running in a
    process that has just booted. The reason is recorded rather than the row
    quietly becoming "stopped", because "we stopped this" and "the server went
    away under it" are different facts about the same partial list.
    """
    at = now or int(time.time())
    c = conn()
    n = int(c.execute("SELECT COUNT(*) FROM x_lists WHERE state='running'"
                      ).fetchone()[0] or 0)
    if n:
        c.execute("UPDATE x_lists SET state='stopped', done_at=?, "
                  "error='the server restarted while this walk was running' "
                  "WHERE state='running'", (at,))
        c.commit()
    return n
