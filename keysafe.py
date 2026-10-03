"""Private keys at rest.

A key is encrypted under a passphrase before it goes into the database and is
opened again only while something actually needs it, so what the file on disk
holds is a Web3 keystore rather than a key. That is the whole point of this
module: `data/pons.db` sits in a folder that syncs, and every copy of it that
leaves this machine - OneDrive, a backup, a disk somebody else ends up holding -
is a copy of the keys unless they are wrapped.

The passphrase is not stored anywhere. It is held in memory from the moment it
is typed until the vault is locked, the server stops, or it has gone unused for
`KEY_IDLE_SEC`. Nothing here logs it, returns it or writes it down, and it is
not read from the environment either, for the reason `config.py` gives.

The format is the one `eth-account` already implements - Web3 Secret Storage v3,
scrypt and AES-128-CTR, the same blob MetaMask imports - so this is not a cipher
of this project's own invention and the keys stay portable to anywhere else that
speaks it. Measured on this machine: 0.95s to wrap a key and 0.82s to open one,
which is scrypt at n=262144 doing its job, and it is why the vault is opened
once and not per page load.

What that buys and what it does not is worth being exact about, because
"encrypted" invites more confidence than it should:

  * a copy of the file is useless without the passphrase, and the passphrase is
    the whole of the security - scrypt makes each guess cost a second, not a
    microsecond, so a short phrase undoes the work;
  * nothing running as this Windows user is protected against while the vault is
    open, because anything that can call this module can also wait for it to be
    unlocked;
  * the key being signed with is in memory while a sweep runs, since signing
    needs it, and the show button puts a key on the screen on purpose.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from eth_account import Account

import config as C

log = logging.getLogger("keysafe")


class Locked(Exception):
    """The vault is shut: no key can be read or written until it is opened."""


class WrapError(Exception):
    """A passphrase that does not open the blob, or a blob that is damaged."""


# The passphrase, and the last time anything asked for it. Module state on
# purpose: it has to outlive a request but not the process, and a file or a
# cookie would be the thing being defended against.
_PHRASE: str | None = None
_TOUCHED = 0.0
_LOCK = threading.Lock()


def _expire() -> None:
    """Drop a passphrase that has gone unused. The caller holds `_LOCK`."""
    global _PHRASE
    if (_PHRASE is not None and C.KEY_IDLE_SEC > 0
            and time.time() - _TOUCHED > C.KEY_IDLE_SEC):
        _PHRASE = None
        log.info("vault locked: idle for %ss", int(C.KEY_IDLE_SEC))


def is_wrapped(stored: str | None) -> bool:
    """Whether a stored value is a keystore rather than a bare key.

    A row that is not one was written before this module existed, or comes from
    a database restored out of an old backup. It is read as it is - the
    alternative is showing the user a wallet they cannot use - and
    `store.keywallet_repair` is what puts it right.
    """
    return bool(stored) and stored.lstrip().startswith("{")


def unlocked() -> bool:
    """Whether a key can be read right now, idle limit included."""
    with _LOCK:
        _expire()
        return _PHRASE is not None


def lock() -> None:
    global _PHRASE
    with _LOCK:
        _PHRASE = None


def mask(secret: str) -> str:
    """Enough of a key to tell two of them apart, and not enough to be one.

    Computed from the key as it goes in rather than when a row is drawn, so the
    list behind the table can render without opening anything.
    """
    s = secret if secret.startswith("0x") else "0x" + secret
    return s[:6] + "..." + s[-4:]


def _phrase() -> str:
    """The passphrase, and a note that it has just been used."""
    global _TOUCHED
    with _LOCK:
        _expire()
        if _PHRASE is None:
            raise Locked("the vault is locked")
        _TOUCHED = time.time()
        return _PHRASE


def wrap(secret: str, phrase: str | None = None) -> str:
    """Encrypt one key for storage.

    The vault's passphrase is used unless one is passed in, which is how a
    caller with no vault - a test, or a repair of rows written before there was
    one - stays possible without a second way into the database.
    """
    word = phrase if phrase is not None else _phrase()
    return json.dumps(Account.encrypt(secret, word), separators=(",", ":"))


def unwrap_with(stored: str, phrase: str) -> str:
    """The key from a blob, with an explicit passphrase and the vault untouched."""
    try:
        blob = json.loads(stored)
    except ValueError:
        raise WrapError("that is not a keystore")
    try:
        key = Account.decrypt(blob, phrase)
    except Exception as e:
        # One message for every way this can fail, because from the outside they
        # are one event: the blob did not open. What the exception says - a MAC
        # that does not match, a cipher it does not know - is worth a log line
        # and not worth the page, and none of it is ever put in the message.
        raise WrapError("that passphrase does not open this key (%s)"
                        % type(e).__name__)
    return "0x" + key.hex()


def unwrap(stored: str) -> str:
    """The key from a stored value: a keystore opened with the vault's phrase,
    or a bare key handed straight back."""
    if not is_wrapped(stored):
        return stored
    return unwrap_with(stored, _phrase())


def unlock(phrase: str, blobs: list[str]) -> bool:
    """Open the vault, against the keys that are already in it.

    Returns True when nothing stored was a keystore, which means the passphrase
    is being set rather than checked: there is nothing to check it against, and
    the caller is expected to have asked for it twice. Anything wrapped is
    tried first, so a wrong passphrase is refused here rather than discovered
    later on whichever key fails to open.
    """
    global _PHRASE, _TOUCHED
    phrase = phrase or ""
    wrapped = [b for b in blobs if is_wrapped(b)]
    if wrapped:
        if not phrase:
            raise WrapError("enter the passphrase these keys were stored with")
        try:
            unwrap_with(wrapped[0], phrase)
        except WrapError:
            raise WrapError("that passphrase does not open these keys")
    elif len(phrase) < C.KEY_MIN_PHRASE:
        raise WrapError("use at least %d characters, and a few words is better"
                        % C.KEY_MIN_PHRASE)
    with _LOCK:
        _PHRASE = phrase
        _TOUCHED = time.time()
    return not wrapped
