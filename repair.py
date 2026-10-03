"""Give back the first buys the trade walk dropped before the buffer existed.

The walk reads a block once and moves on. While it was running ahead of the
launch indexer, a fresh token's launch-window buys were logs whose curve no
token row claimed yet, and a log like that was skipped - so the buys that make
the whole snipe verdict were gone, and the token read as an ordinary late buy
instead of a bundle. The deferral buffer in `trades` stops that happening from
now on; it cannot return what was already lost, because the walk never revisits
a block and the launch window is long past.

This re-reads a range and writes back only the first-buy facts and the early
window rows - the guarded part of a trade row, which can be replayed, as
opposed to the sums, which cannot. It is idempotent: a second run over the same
range changes nothing, so an interrupted run can simply be started again.

Nothing is written before the plan is printed, and --dry-run writes nothing at
all - it runs the real statements and rolls them back, so its numbers are the
numbers the real pass would write.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

import config as C
import store
import trades

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("repair")


def _defaults() -> tuple[int, int]:
    """The range the walk itself has been through.

    Its two cursors, for the same reason `store.unfinalized_snipes` uses them:
    they bracket exactly the blocks the indexer has read, and a repair over
    blocks nobody has read would be guessing at what is missing.
    """
    back = store.kv_get("trades_back_block")
    fwd = store.kv_get("trades_block")
    return int(back or 0), int(fwd or 0)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Re-read walked blocks and restore dropped first buys.")
    ap.add_argument("--from", dest="lo", type=int, default=None,
                    help="first block (default: trades_back_block)")
    ap.add_argument("--to", dest="hi", type=int, default=None,
                    help="last block (default: trades_block)")
    ap.add_argument("--dry-run", action="store_true",
                    help="write nothing, report what would change")
    ap.add_argument("--recount-snipes", action="store_true",
                    help="also put every token back in the snipe queue and "
                         "empty the wallet counter, so it is rebuilt from the "
                         "repaired tables (the snipes_v2 migration, again)")
    ap.add_argument("--pause", type=float, default=C.TRADES_PAUSE,
                    help="seconds between spans (default: %(default)s)")
    args = ap.parse_args()

    store.init()
    dlo, dhi = _defaults()
    lo = dlo if args.lo is None else args.lo
    hi = dhi if args.hi is None else args.hi
    if lo > hi:
        print("nothing to do: %s is above %s" % (lo, hi))
        return 0

    spans = (hi - lo) // C.TRADES_SPAN + 1
    print("blocks %s..%s (%s), %s spans of %s, ~%s plus reading time"
          % (lo, hi, hi - lo + 1, spans, C.TRADES_SPAN,
             time.strftime("%M:%S", time.gmtime(spans * args.pause))))
    print("mode: %s" % ("dry run, nothing is written" if args.dry_run
                        else "writing first-buy facts only"))
    print()

    started = time.time()
    last = [0.0]

    def progress(start: int, end: int, out: dict[str, int]) -> None:
        now = time.time()
        if now - last[0] >= 5.0 or out["spans"] >= spans:
            last[0] = now
            print("  %s..%s  logs=%-7s corrected=%-6s early=%-6s unknown=%s"
                  % (start, end, out["logs"], out["moved"], out["early"],
                     out["unknown"]))
            sys.stdout.flush()
        # Paced like the walk is: the point is not to be faster than it, it
        # is to read the same blocks once more.
        if out["spans"] < spans:
            trades._stop.wait(args.pause)

    try:
        out = trades.repair_spans(lo, hi, dry_run=args.dry_run,
                                  on_span=progress)
    except KeyboardInterrupt:
        print("\ninterrupted; what was committed stays, and a second run "
              "picks up where this one stopped")
        return 130

    print()
    print("%s: %s spans, %s logs, %s tokens corrected, %s early rows added, "
          "%s logs of unindexed curves skipped"
          % ("would change" if args.dry_run else "changed",
             out["spans"], out["logs"], out["moved"], out["early"],
             out["unknown"]))
    print("elapsed %s" % time.strftime("%M:%S", time.gmtime(time.time() - started)))

    if args.recount_snipes:
        if args.dry_run:
            print("\n--recount-snipes with --dry-run: not clearing anything")
            return 0
        c = store.conn()
        n = c.execute("UPDATE trades SET snipe_counted=0").rowcount
        c.execute("DELETE FROM sniper_wallets")
        c.commit()
        print("\n%s tokens back in the snipe queue, wallet counter cleared; "
              "the trades loop rebuilds it from the repaired tables as it "
              "runs" % n)
    elif not args.dry_run:
        print("\nThe per-token labels are computed on read, so they are "
              "already right. The wallet hit counts under the Snipers tab are "
              "not: they are built once per token and never revisited, so a "
              "token that gained a racer here is still missing from them. "
              "Run again with --recount-snipes to have them rebuilt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
