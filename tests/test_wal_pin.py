"""The freeze that filled the log, reproduced here and shown to be fixed.

For four hours and twenty minutes the live server logged the same checkpoint
line every fifteen minutes:

    wal checkpoint: busy=1 frames=11890    copied=1001
    ...
    wal checkpoint: busy=1 frames=13113178 copied=1001

`copied` is how far the checkpoint got. It never moved past frame 1001 while
the log grew from 47 MB to 50 GB, and `busy=1` says why: something was holding
a read snapshot at frame 1001, and a checkpoint cannot pass a snapshot.

The holder was not a reader loop. Python's sqlite3 opens a transaction for a
write statement and closes it only on commit or rollback, so a statement that
fails - and `database is locked` failed four thousand four hundred and
thirty-two times in that run - leaves the connection sitting inside a
transaction nothing closed. Its next read takes a snapshot, and there it stays
for the life of the thread. No loop in `indexer.py` or `trades.py` rolled back.

This file builds that exact sequence on a database of its own and checks both
halves: that a failed write really does leave the transaction open, and that
`store.discard()` - which every loop's error path now calls - lets the
checkpoint fold the log and truncate it.

The dashboard's database is untouched; this one is made here and deleted.
"""
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config as C

# Before the first connection, which is lazy: store.conn() reads this.
C.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pons-pin-"), "test.db")

import store                                              # noqa: E402

store.init()

bad = 0
WAL = str(C.DB_PATH) + "-wal"


def check(name, ok, extra=""):
    global bad
    if not ok:
        bad += 1
    print("%-5s %-52s %s" % ("ok" if ok else "FAIL", name, extra))


def wal_mb():
    return os.path.getsize(WAL) / 1048576.0 if os.path.exists(WAL) else 0.0


def checkpoint(c):
    busy, frames, copied = c.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return busy, frames, copied


# ---------------------------------------------------------------- the setup
store.kv_set("mark", "first")
store.conn().execute("PRAGMA busy_timeout=300")

# A second connection takes the write lock and keeps it, so the store's own
# next write fails the way four thousand of them failed on the live server.
hog = sqlite3.connect(str(C.DB_PATH), timeout=5)
hog.execute("PRAGMA journal_mode=WAL")
hog.execute("BEGIN IMMEDIATE")
hog.execute("INSERT INTO kv(key, value) VALUES('hog','held')")

failed = ""
try:
    store.kv_set("mark", "second")
except sqlite3.OperationalError as exc:
    failed = str(exc)
check("a write under contention fails", "locked" in failed, failed[:50])

c = store.conn()
check("the failure leaves a transaction open", c.in_transaction,
      "in_transaction=%s" % c.in_transaction)

# Its next read takes the snapshot, at whatever frame the log is on now.
store.kv_get("mark")
pinned = store.conn().execute(
    "SELECT 1").fetchone() is not None and c.in_transaction
check("a read inside it takes a snapshot and keeps it", pinned,
      "in_transaction=%s" % c.in_transaction)

hog.rollback()
hog.close()

# ---------------------------------------------------------------- the freeze
writer = sqlite3.connect(str(C.DB_PATH), timeout=30)
writer.execute("PRAGMA journal_mode=WAL")
for i in range(20):
    writer.executemany("INSERT INTO kv(key, value) VALUES(?, 'x')",
                       [("pad%d" % (i * 100 + j),) for j in range(100)])
    writer.commit()
check("the log grew past the snapshot", wal_mb() > 0.5, "%.1f MB" % wal_mb())

probe = sqlite3.connect(str(C.DB_PATH), timeout=5)
busy, frames, copied = checkpoint(probe)
check("a checkpoint stops at the frozen frame", copied < frames,
      "busy=%d frames=%d copied=%d" % (busy, frames, copied))

# And it stays there while the log keeps growing, which is the whole incident.
for i in range(20):
    writer.executemany("INSERT INTO kv(key, value) VALUES(?, 'x')",
                       [("more%d" % (i * 100 + j),) for j in range(100)])
    writer.commit()
busy2, frames2, copied2 = checkpoint(probe)
check("and does not move while the log grows",
      copied2 == copied and frames2 > frames,
      "frames %d -> %d, copied %d -> %d" % (frames, frames2, copied, copied2))

# ---------------------------------------------------------------- the fix
store.discard()
check("discard() closes the leftover transaction",
      not store.conn().in_transaction,
      "in_transaction=%s" % store.conn().in_transaction)

busy3, frames3, copied3 = checkpoint(probe)
check("one discard lets the log fold and truncate",
      copied3 == frames3 and wal_mb() < 0.1,
      "busy=%d frames=%d copied=%d wal=%.2f MB"
      % (busy3, frames3, copied3, wal_mb()))

# ---------------------------------------------------------------- it is safe
before = store.kv_get("mark")
store.discard()
check("discard() on a clean connection is a no-op",
      store.kv_get("mark") == before and not store.conn().in_transaction)
check("and the data survived all of it",
      store.kv_get("mark") == "first",
      "mark=%s" % store.kv_get("mark"))

# ---------------------------------------------------------------- the guard
# The warning is the other half of the fix: the frozen checkpoint above reported
# a success-shaped tuple, and nothing said so for four hours. Same shape here -
# a leftover transaction pins the log - but grown past the threshold, and the
# maintenance call has to say it out loud.
c.execute("PRAGMA busy_timeout=3000")
held = sqlite3.connect(str(C.DB_PATH), timeout=5)
held.execute("PRAGMA journal_mode=WAL")
held.execute("BEGIN IMMEDIATE")
held.execute("INSERT INTO kv(key, value) VALUES('held','x')")
try:
    store.kv_set("mark", "third")
except sqlite3.OperationalError:
    pass
store.kv_get("mark")
held.rollback()
held.close()
check("the second pin is in place", store.conn().in_transaction)

need = C.WAL_STUCK_FRAMES + 3000
frames4 = 0
while frames4 < need:
    writer.executemany("INSERT INTO kv(key, value) VALUES(?, 'x')",
                       [("deep%d" % (frames4 * 100 + j),) for j in range(400)])
    writer.commit()
    frames4 = probe.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[1]
# The maintenance call runs on its own thread's connection in the real server,
# and a connection cannot checkpoint past a transaction of its own - it answers
# SQLITE_LOCKED, which the except turns into a warning. So this asks from
# another connection, the way trades_loop does.
real_conn = store.conn
store.conn = lambda: probe
busy4, frames4, copied4 = store.wal_checkpoint()
store.conn = real_conn
check("the stuck checkpoint is reported, not swallowed",
      frames4 - copied4 > C.WAL_STUCK_FRAMES,
      "busy=%d frames=%d copied=%d (warns above %d)"
      % (busy4, frames4, copied4, C.WAL_STUCK_FRAMES))

store.discard()
busy5, frames5, copied5 = checkpoint(probe)
check("and the same discard still clears it",
      copied5 == frames5 and wal_mb() < 0.1,
      "busy=%d frames=%d copied=%d wal=%.2f MB"
      % (busy5, frames5, copied5, wal_mb()))

# ---------------------------------------------------------------- cold start
# The re-price pass remembers the last rate it priced each quote at, in memory,
# and memory does not survive a restart. So every quote looked brand new at boot
# and the first tick re-priced all of them at once: 478 thousand rows and 609 MB
# of log in a single pass, which is the burst that set the log file's
# high-water mark for the whole run that followed - and, before the chunking, a
# commit long enough to starve the checkpoint as well. The rate a quote was last
# priced at is already in the database, so the pass reads it there instead of
# assuming the move was infinite.
Q = "0x" + "ab" * 20
qq = store.conn()
qq.execute("INSERT INTO quote_assets(address, symbol, kind, usd_price, updated_at) "
           "VALUES(?, 'TST', 'crypto', 2.0, ?)", (Q, int(time.time())))
for i in range(200):
    qq.execute("INSERT INTO tokens(address, curve, launch_block, quote_address, "
               "mcap_quote, mcap_usd) VALUES(?, '0xc', 1, ?, 100.0, 200.0)",
               ("0x%040x" % i, Q))
qq.commit()

store._repriced.clear()                        # as it is after a restart
probe.execute("PRAGMA wal_checkpoint(TRUNCATE)")
store.apply_quote_rate(Q, 2.0)                 # the rate it is already priced at
check("a restart does not re-price what is already priced",
      wal_mb() < 0.1, "%.2f MB of log" % wal_mb())

# And a quote that did move while the server was down is still re-priced on that
# first tick - which is the case a seed read after the update cannot tell apart
# from an unmoved one, because the value it would read back is the new rate.
qq.execute("UPDATE tokens SET mcap_usd = mcap_quote * 2.0 WHERE quote_address=?",
           (Q,))
qq.commit()
store._repriced.clear()
store.apply_quote_rate(Q, 6.0)
moved = qq.execute("SELECT COUNT(*) FROM tokens "
                   "WHERE mcap_usd = mcap_quote * 6.0").fetchone()[0]
check("but a rate that moved while it was down still re-prices", moved == 200,
      "%d of 200 rows" % moved)

# ---------------------------------------------------------------- spread out
# The cap that keeps one tick from writing a quarter of a million rows. The log
# file keeps the highest count of frames that were ever unfolded at once, so a
# whole pass inside one tick is the size of the file for the rest of the run,
# however the writes inside it were chunked. A pass that does not fit is carried
# on over the ticks that follow.
store._repriced.clear()
store._pending.clear()
C.REPRICE_PER_TICK = 60
qq.execute("UPDATE tokens SET mcap_usd = -1 WHERE quote_address=?", (Q,))
qq.commit()

done = []
for _ in range(6):
    store.apply_quote_rate(Q, 9.0)
    done.append(qq.execute("SELECT COUNT(*) FROM tokens WHERE quote_address=? "
                           "AND mcap_usd = mcap_quote * 9.0",
                           (Q,)).fetchone()[0])
steps = [done[0]] + [b - a for a, b in zip(done, done[1:])]
check("one tick writes no more than its share of a pass", steps[0] == 60,
      "tick wrote %d rows, cap is %d" % (steps[0], C.REPRICE_PER_TICK))
check("and the pass still finishes, over the ticks that follow",
      done[-1] == 200 and not store._pending,
      "rows priced %s over six ticks, %d quote(s) still pending"
      % (done, len(store._pending)))

# ------------------------------------------------- a rate that moves mid-pass
# Spreading the pass over ticks introduced a way to be wrong that one pass per
# tick could not have: the rate can move while the pass is in flight. The rows
# written before that are then at the old rate while the rows after them are at
# the new one, and `_repriced` - which is what the epsilon reads - has to say
# where the quote is genuinely priced to. Saying the new rate would call the
# whole quote current and leave the head stale until the rate moved again by
# itself, an hour at the outside.
store._repriced.clear()
store._pending.clear()
qq.execute("UPDATE tokens SET mcap_usd = -1 WHERE quote_address=?", (Q,))
qq.commit()


def at(rate):
    return qq.execute("SELECT COUNT(*) FROM tokens WHERE quote_address=? "
                      "AND mcap_usd = mcap_quote * ?", (Q, rate)).fetchone()[0]


store.apply_quote_rate(Q, 10.0)                # its 60 rows, then it stops
store.apply_quote_rate(Q, 20.0)                # the rate moves under it
check("a rate that moves during a pass is used for the rest of it",
      at(10.0) == 60 and at(20.0) == 60 and store._pending[Q][2] == 10.0,
      "%d rows left at the old rate, %d written at the new since, head is %.6g"
      % (at(10.0), at(20.0), store._pending[Q][2]))

# The rest of the pass goes on at the newer rate over the ticks that follow, as
# it must - the cap is per tick, not per pass.
store.apply_quote_rate(Q, 20.0)
store.apply_quote_rate(Q, 20.0)
check("and its remaining rows are written at the newer rate",
      at(20.0) == 140 and not store._pending,
      "%d of 200 rows at the new rate now, %d quote(s) still pending"
      % (at(20.0), len(store._pending)))
check("and the pass remembers the rate its head was written with",
      store._repriced[Q][0] == 10.0,
      "remembered %.6g" % store._repriced[Q][0])

before = qq.total_changes
store.apply_quote_rate(Q, 20.0)                # the same rate, but the head is stale
wrote = qq.total_changes - before
check("so the tick after re-prices that head, and only the head",
      at(20.0) == 200 and wrote == 61 and not store._pending,
      "%d rows written (60 and the quote's own rate, not 200), %d of 200 at the "
      "new rate" % (wrote, at(20.0)))
C.REPRICE_PER_TICK = 20000

writer.close()
probe.close()

print("\nALL UNIT CASES PASS:", not bad)
sys.exit(1 if bad else 0)
