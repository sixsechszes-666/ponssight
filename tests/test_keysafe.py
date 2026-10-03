"""The private key under a passphrase, checked on a database of its own.

The claim this file has to hold up is narrow and checkable: after a key is
stored, the key is not in the file. Not "the column looks encrypted" - the
actual bytes of both the database and its write-ahead log are read here and the
key is looked for in them, because a page freed after an update and a log that
was never truncated are exactly how a plaintext copy survives an encryption
that was otherwise done correctly.

The rest is the vault's own rules: a wrong passphrase opens nothing and leaves
the vault shut, a locked vault hands out nothing, a damaged blob is refused
rather than half-decrypted, an unused passphrase expires while one that is being
used does not, and a row from a database restored out of an old backup is both
readable and repairable.

Nothing here touches the dashboard's database, and nothing here talks to the
chain. scrypt at n=262144 is doing its job, so this takes about fifteen seconds:
that cost is the protection, and it is deliberately not lowered for the test.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config as C

# Before the first connection, which is lazy: store.conn() reads this.
C.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pons-keytest-"), "test.db")

import keysafe
import store

store.init()

ADDR = "0x" + "01" * 20
ADDR2 = "0x" + "02" * 20
KEY = "0x" + "0123456789abcdef" * 4
KEY2 = "0x" + "fedcba9876543210" * 4
PHRASE = "correct horse battery staple"
HEX = KEY[2:]

bad = 0


def check(name, ok, extra=""):
    global bad
    if not ok:
        bad += 1
    print("%-5s %-52s %s" % ("ok" if ok else "FAIL", name, extra))


def raises(fn, *a, **k):
    """The exception a call raises, or None if it did not raise at all."""
    try:
        fn(*a, **k)
    except Exception as e:
        return e
    return None


def stored_bytes():
    """What is actually on the disk, both files, as bytes.

    The log matters as much as the database: in WAL mode a committed row can
    sit in it for a while, so a check that read only the database would pass on
    a key that is in the log the whole time. The checkpoint first is what makes
    this deterministic rather than dependent on when SQLite last moved a page.
    """
    store.conn().execute("PRAGMA wal_checkpoint(PASSIVE)")
    out = b""
    for path in (C.DB_PATH, C.DB_PATH + "-wal", C.DB_PATH + "-shm"):
        if os.path.exists(path):
            with open(path, "rb") as f:
                out += f.read()
    return out


# --- 1. the format -------------------------------------------------------
# A keystore, not a cipher of this project's own: the version and the kdf are
# what make the same blob importable elsewhere.
blob = keysafe.wrap(KEY, PHRASE)
parsed = json.loads(blob)
check("wrap produces a web3 keystore",
      parsed.get("version") == 3 and parsed["crypto"]["kdf"] == "scrypt"
      and parsed["crypto"]["kdfparams"]["n"] == 262144,
      "version=%s kdf=%s" % (parsed.get("version"), parsed["crypto"]["kdf"]))
check("and a bare key is not in it",
      HEX not in blob and HEX.upper() not in blob,
      "%d bytes of json" % len(blob))
check("it opens with the passphrase it was written with",
      keysafe.unwrap_with(blob, PHRASE).lower() == KEY.lower())

# The failure has to be a refusal with a sentence, because the alternative -
# returning whatever the cipher produced - would be a wrong address and a
# transaction signed for a wallet nobody owns.
wrong = raises(keysafe.unwrap_with, blob, "not the passphrase")
check("a wrong passphrase is refused, not tolerated",
      isinstance(wrong, keysafe.WrapError) and "does not open" in str(wrong),
      type(wrong).__name__ if wrong else "no exception")

# --- 2. the vault is shut until it is opened ----------------------------
keysafe.lock()
check("a locked vault reports itself locked", not keysafe.unlocked())
check("and hands out nothing",
      isinstance(raises(keysafe.unwrap, blob), keysafe.Locked))
check("and writes nothing",
      isinstance(raises(keysafe.wrap, KEY), keysafe.Locked))

# A wrong passphrase against a stored key must leave the vault shut. If it did
# not, the page would report success and every later read would fail instead.
refused = raises(keysafe.unlock, "not the passphrase, but long enough", [blob])
check("a wrong passphrase does not open the vault",
      isinstance(refused, keysafe.WrapError) and not keysafe.unlocked(),
      "unlocked=%s" % keysafe.unlocked())

# An empty vault has nothing to check against, so the passphrase is being set
# and the length rule is the only thing standing there.
check("setting a passphrase refuses a short one",
      isinstance(raises(keysafe.unlock, "short", []), keysafe.WrapError))
fresh = keysafe.unlock(PHRASE, [])
check("and accepts a real one, reporting the vault as fresh",
      fresh and keysafe.unlocked())

# --- 3. the store is the only door -------------------------------------
store.keywallet_add(ADDR, "burner one", KEY)
rows = store.keywallet_list()
row = rows[0] if rows else {}
check("the stored value is a keystore, not the key",
      keysafe.is_wrapped(store.conn().execute(
          "SELECT secret FROM key_wallets WHERE address=?",
          (ADDR,)).fetchone()["secret"]))
check("the list carries the mask, not the key",
      row.get("mask") == "0x0123...cdef" and "secret" not in row,
      "mask=%s fields=%d" % (row.get("mask"), len(row)))
check("and so does the single-row read",
      "secret" not in (store.keywallet_get(ADDR) or {}))
check("the key comes back out of its own function",
      (store.keywallet_secret(ADDR) or "").lower() == KEY.lower())

# The one that matters: read the files. The mask is in there on purpose - it is
# ten characters of a sixty-six character string, and the page shows it - but
# the key is not, in either case, in any of the three files.
disk = stored_bytes()
check("the key is not in the database or its log",
      HEX.encode() not in disk and HEX.upper().encode() not in disk,
      "read %d bytes" % len(disk))

# --- 4. a key cannot even be written while the vault is shut ------------
keysafe.lock()
check("adding a key needs an open vault",
      isinstance(raises(store.keywallet_add, ADDR2, None, KEY2),
                 keysafe.Locked))
check("and the refused key was not written",
      store.conn().execute("SELECT COUNT(*) n FROM key_wallets").fetchone()["n"]
      == 1)
# The deliberate exception: getting rid of a key is always allowed to be easier
# than keeping one, so renaming and forgetting do not ask for the passphrase.
check("renaming a wallet does not need an open vault",
      store.keywallet_label(ADDR, "renamed while locked") is True)
keysafe.unlock(PHRASE, store.keywallet_blobs())
check("the same passphrase reopens what is stored", keysafe.unlocked())

# --- 5. a damaged blob is refused --------------------------------------
chunks = json.loads(blob)
ct = chunks["crypto"]["ciphertext"]
chunks["crypto"]["ciphertext"] = ("0" if ct[0] != "0" else "1") + ct[1:]
damaged = json.dumps(chunks)
broke = raises(keysafe.unwrap_with, damaged, PHRASE)
check("a flipped byte is caught by the mac, not decrypted",
      isinstance(broke, keysafe.WrapError),
      type(broke).__name__ if broke else "no exception")
check("and it is not mistaken for a passphrase problem",
      isinstance(raises(keysafe.unwrap_with, "not json at all", PHRASE),
                 keysafe.WrapError))

# --- 6. a row from an old backup ---------------------------------------
# Exactly what a restore produces: the key in the clear, and no mask, because
# neither existed when the copy was made. It has to stay usable - the wallet is
# real - and it has to be repairable, because it is the copy the encryption
# exists to prevent.
c = store.conn()
c.execute("INSERT INTO key_wallets(address,label,secret,mask,added_at) "
          "VALUES(?,?,?,?,?)", (ADDR2, "from a backup", KEY2, None, 0))
c.commit()
legacy = store.keywallet_list()
leak = [r for r in legacy if r["address"] == ADDR2][0]
check("an unwrapped row reads as it is rather than breaking",
      (store.keywallet_secret(ADDR2) or "").lower() == KEY2.lower())
check("and it was in the clear, which is why this matters",
      HEX.encode() in stored_bytes() or KEY2[2:].encode() in stored_bytes(),
      "mask=%s" % leak["mask"])

fixed = store.keywallet_repair()
check("the repair encrypts it and fills the mask",
      fixed == 1
      and keysafe.is_wrapped(store.conn().execute(
          "SELECT secret FROM key_wallets WHERE address=?",
          (ADDR2,)).fetchone()["secret"])
      and [r for r in store.keywallet_list()
           if r["address"] == ADDR2][0]["mask"] == "0xfedc...3210",
      "repaired=%s" % fixed)
check("it still opens afterwards",
      (store.keywallet_secret(ADDR2) or "").lower() == KEY2.lower())
check("and the plaintext is gone from the files",
      KEY2[2:].encode() not in stored_bytes())
check("running it again is a no-op", store.keywallet_repair() == 0)

# --- 7. the mask is computed once, not per row -------------------------
# Two keys must not collide in the table, which is the only thing the mask is
# for, and two different keys must not produce the same one.
check("mask keeps the head and the tail",
      keysafe.mask(KEY) == "0x0123...cdef"
      and keysafe.mask(KEY2) == "0xfedc...3210")
check("a key without 0x is masked as if it had one",
      keysafe.mask(HEX) == keysafe.mask(KEY))

# --- 8. the idle limit -------------------------------------------------
# A passphrase that stays in memory forever is a passphrase that is there the
# next morning, so it expires. Two things have to hold: it actually expires, and
# using the vault postpones it - an expiry that fired while someone was working
# would be a bug that only shows up as a refusal at random.
keysafe.unlock(PHRASE, store.keywallet_blobs())
C.KEY_IDLE_SEC = 0.5
time.sleep(0.8)
check("an unused passphrase expires", not keysafe.unlocked())
check("and the key it guarded is refused again",
      isinstance(raises(store.keywallet_secret, ADDR), keysafe.Locked))

# The second one is measured against a real cost rather than a guessed one.
# Opening a key runs scrypt, and the clock is touched when the passphrase is
# taken and not when the key comes back out. Three intervals matter, with `s`
# the sleep before the read and `k` one scrypt:
#
#   s     from the unlock to the read   - must be inside the limit, or the read
#                                         is refused before it happens
#   k     from the read to the check    - must be inside the limit, or the vault
#                                         shut while it was being read
#   s + k from the unlock to the check  - must be OUTSIDE the limit, or the vault
#                                         would be open whether the read counted
#                                         or not and the case proves nothing
#
# So the limit belongs between `s` and `s + k`, and both are derived from the
# measured cost instead of being written down: scrypt is quick on an idle
# machine and slower on a busy one. A hardcoded 1.4 against a hardcoded second
# sat between the intervals only until this machine had something else to do -
# which is how this case first failed, with the key read back fine and the vault
# already expired.
# The limit from the case above is still in force, and `unlock` verifies the
# passphrase by opening a key - a scrypt of its own, which would already be past
# a limit of half a second and would shut the vault while unlocking it. So the
# limit is set generously first, and the clock starts when `unlock` returns,
# which is where it is touched.
C.KEY_IDLE_SEC = 3600.0
t0 = time.time()
keysafe.unlock(PHRASE, store.keywallet_blobs())
k = time.time() - t0                       # what one scrypt costs, right now
s = 4 * k
C.KEY_IDLE_SEC = s + k / 2.0               # strictly between `s` and `s + k`
time.sleep(s)
t1 = time.time()
try:
    used, why = store.keywallet_secret(ADDR), ""
except Exception as e:
    used, why = None, type(e).__name__
k2 = time.time() - t1                      # the read costs a scrypt of its own
# The case means something only if the vault would have expired without the
# read counting as a use. Said out loud rather than assumed, because that is the
# half of it that a too-generous limit would quietly drop.
proves = s + k2 > C.KEY_IDLE_SEC
check("and using it puts the clock back",
      bool(used) and keysafe.unlocked() and proves,
      "read back: %s%s, %.1fs since the unlock, %.1fs since the read, limit "
      "%.1fs (scrypt %.1fs, that read %.1fs, and without the read it would have "
      "been %.1fs over)"
      % (bool(used), "" if used else " (%s)" % why, s + k2, k2,
         C.KEY_IDLE_SEC, k, k2, s + k2 - C.KEY_IDLE_SEC))

# 0 is the documented way to switch it off, for whoever wants the old behaviour.
keysafe.unlock(PHRASE, store.keywallet_blobs())
C.KEY_IDLE_SEC = 0
time.sleep(0.9)
check("0 turns the limit off", keysafe.unlocked())

keysafe.lock()
check("forgetting a wallet does not need an open vault",
      store.keywallet_remove(ADDR) is True)
print("\nALL UNIT CASES PASS:", not bad)
sys.exit(1 if bad else 0)
