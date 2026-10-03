import sys; sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import store, snipe, config as C

store.init()
print("tables:", [r[0] for r in store.conn().execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])

# --- pure verdict unit tests -------------------------------------------
DEV  = "0xdEployer0000000000000000000000000000001"
SNIP = "0x5niper00000000000000000000000000000001".replace("5ni","5n1")
BOT  = "0xB0700000000000000000000000000000000001"
tok = {"launch_block": 1000, "deployer": DEV, "launch_tx": "0xaaa",
       "graduation_threshold": 4.2e18}

def case(name, trade, early, hits, buyers, expect):
    v = snipe.verdict(tok, trade, early, hits, buyers, indexed=True)
    ok = v["label"] == expect
    print(f"{'ok ' if ok else 'FAIL'} {name:34s} label={v['label']:8s} "
          f"score={v['score']} delta={v['delta']} bundled={v['bundled']} "
          f"dev_first={v['deployer_first']} expect={expect}")
    return ok

allok = True
# 1. deployer buys atomically in the launch tx, nobody else races
allok &= case("dev atomic bundle only",
    {"first_buy_block":1000,"first_buy_ts":1,"first_buyer":DEV,
     "first_buy_quote":5e17,"first_buy_tx":"0xaaa"},
    [{"block":1000,"log_index":0,"buyer":DEV,"quote":5e17,"ts":1}], 0, 1, "bundled")

# 2. someone else buys in the same tx as the launch -> atomic sniper
allok &= case("atomic sniper in launch tx",
    {"first_buy_block":1000,"first_buy_ts":1,"first_buyer":SNIP,
     "first_buy_quote":6e17,"first_buy_tx":"0xaaa"},
    [{"block":1000,"log_index":1,"buyer":SNIP,"quote":6e17,"ts":1}], 40, 1, "sniped")

# 3. dev bundles, a bot rides in one block later
allok &= case("dev bundle + bot 1 block later",
    {"first_buy_block":1000,"first_buy_ts":1,"first_buyer":DEV,
     "first_buy_quote":5e17,"first_buy_tx":"0xaaa"},
    [{"block":1000,"log_index":0,"buyer":DEV,"quote":5e17,"ts":1},
     {"block":1001,"log_index":0,"buyer":BOT,"quote":3e17,"ts":1}], 60, 2, "sniped")

# 4. first outside buy 6 blocks in
allok &= case("outside buy 6 blocks in",
    {"first_buy_block":1006,"first_buy_ts":1,"first_buyer":SNIP,
     "first_buy_quote":1e17,"first_buy_tx":"0xbbb"},
    [{"block":1006,"log_index":0,"buyer":SNIP,"quote":1e17,"ts":1}], 0, 1, "early")

# 5. first outside buy 40 blocks in
allok &= case("outside buy 40 blocks in",
    {"first_buy_block":1040,"first_buy_ts":1,"first_buyer":SNIP,
     "first_buy_quote":1e17,"first_buy_tx":"0xbbb"}, [], 0, 0, "slow")

# 6. nobody ever bought
allok &= case("never bought",
    {"first_buy_block":None,"first_buy_ts":None,"first_buyer":None,
     "first_buy_quote":None,"first_buy_tx":None}, [], 0, 0, "none")

# 7. not indexed yet
v = snipe.verdict(tok, None, None, None, None, indexed=False)
print(f"{'ok ' if v['label']=='unknown' else 'FAIL'} not indexed yet"
      f"                    label={v['label']} score={v['score']}")
allok &= v["label"] == "unknown"

# 8. dev is the only early buyer, non-atomically -> dev bought, not a snipe
allok &= case("dev only, bought later",
    {"first_buy_block":1009,"first_buy_ts":1,"first_buyer":DEV,
     "first_buy_quote":9e17,"first_buy_tx":"0xccc"},
    [{"block":1009,"log_index":0,"buyer":DEV,"quote":9e17,"ts":1}], 0, 1, "slow")

print("\nALL UNIT CASES PASS:", bool(allok))
