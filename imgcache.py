"""The disk cache behind /img, and the gateway walk that fills it.

There are two callers and they want different things from the same machinery.
The /img route answers a browser, so it wants the answer now and would rather
fail than wait. The background walk fills the cache ahead of the page, so it
wants the opposite: as many logos as it can get, slowly, and nobody is waiting.
Both need to know where an image lives on this disk, whether it is already
there, and how to ask a gateway for it, so that lives here rather than in the
route.

One cache, two shapes of lookup. Entries written since this module existed are
filed under the first two characters of their key; entries written before it
are still loose in the top folder, and are moved into their shard the first
time anything asks for them. That is what drains the flat folder: the wide scan
is paid once per old entry, by whichever request wanted it, instead of by every
request that wants anything.

And one age limit, which is what keeps the folder from being every logo the
dashboard has ever shown. An entry older than `IMAGE_TTL_SEC` is a miss, so the
request that wanted it fetches it again and it is written anew; the file itself
is deleted by `prune`, off the request path. Nothing here counted bytes before
that, and the folder reached 25.6 GB in a month.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import threading
import time
from pathlib import Path

import requests

import config as C

log = logging.getLogger("imgcache")

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")

_local = threading.local()


class TooLarge(Exception):
    """The image is bigger than this proxy is willing to hold."""


def _session() -> requests.Session:
    """One connection pool per thread, so a gateway is not handshaked twice.

    Per thread rather than shared because a requests session is only as
    thread-safe as the pool underneath it, and this is called from the request
    pool as well as from the walk.
    """
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        # Not a browser agent. Every gateway in the list answers 403 to one and
        # 200 to anything else - the same url, in the same second, changed only
        # by this string. It is why an ipfs logo either came out of the cache
        # or did not come at all.
        s.headers.update({"User-Agent": "ponssight/1.0"})
        _local.session = s
    return s


def key(u: str, w: int = 0) -> str:
    """The cache key for a uri. Keyed on what we were given, not on the mirror
    that happens to answer, so a logo is fetched once whichever served it."""
    return hashlib.sha1(f"{u}|{w}".encode()).hexdigest()


def _shard(k: str) -> Path:
    """The folder an entry lives in, two hex characters deep.

    Flat, every lookup was a scan of the whole cache - twenty-four thousand
    directory entries by now, forty milliseconds before the network was even
    considered, and linear in every logo ever fetched. Two characters cut it by
    two hundred and fifty-six.
    """
    return C.CACHE_DIR / k[:2]


def _adopt(p: Path, k: str) -> Path:
    """File an entry written before the cache was sharded under its shard."""
    try:
        d = _shard(k)
        d.mkdir(parents=True, exist_ok=True)
        moved = d / p.name
        os.replace(p, moved)
        return moved
    except OSError as e:
        # Serving it from where it is beats not serving it.
        log.debug("could not file %s: %s", p.name, str(e)[:80])
        return p


def _size(p: Path) -> int:
    """The file's size, or 0 when it cannot be read at this instant.

    A file that is being renamed into place by the request fetching the same
    logo, or one OneDrive's sync is reading, answers with a permission error for
    a moment. "Cannot be read" and "is empty" want the same answer here - neither
    is a cache hit - and letting the error through would turn a momentary
    collision into a 500 on the whole request.
    """
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _stale(p: Path) -> bool:
    """True when this entry is older than the cache is willing to keep.

    The clock is the file's own modification time, which is when it was
    renamed into place and therefore exactly the age of the bytes. Nothing
    has to be written down to know an entry's age, which is what makes this
    work on the ninety thousand entries that were already here when the limit
    was added.
    """
    if C.IMAGE_TTL_SEC <= 0:
        return False
    try:
        return (time.time() - p.stat().st_mtime) > C.IMAGE_TTL_SEC
    except OSError:
        # Not readable at this instant is a momentary collision, not an old
        # file. Calling it stale would send the caller to the network for
        # something that is on the disk; `_size` has already refused it as a
        # hit, which is the answer that costs nothing.
        return False


def cached(u: str, w: int = 0) -> Path | None:
    """The file this uri is already cached as, or None if it needs fetching.

    An expired entry is not a hit. Reporting it as a miss rather than
    deleting it here is deliberate: this runs on the request path, where an
    unlink of a file another request is reading is a failure on Windows
    rather than a courtesy, and the caller only needs the miss to do the
    right thing - `/img` refetches and rewrites it, the walk skips it and
    comes back to it.
    """
    k = key(u, w)
    # Every match is looked at, not just the first with bytes in it. The
    # extension is whatever the gateway said that time, so a shard can hold
    # an expired {k}.png beside a fresh {k}.webp, and returning the first
    # match would hand back yesterday's file while today's sat next to it.
    for p in _shard(k).glob(f"{k}.*"):
        if _size(p) > 0 and not _stale(p):
            return p
    # The top folder is only ever consulted for entries that predate the
    # shards. It empties as they are asked for.
    for p in C.CACHE_DIR.glob(f"{k}.*"):
        if _size(p) > 0 and not _stale(p):
            return _adopt(p, k)
    return None


def put(u: str, w: int, data: bytes, ext: str) -> Path:
    """Write the image where `cached` will look for it, in one step.

    The destination is read by whatever request arrives while these bytes are
    still going down, and that is not rare: the grid asks for a screenful of
    logos at once and one launcher's picture sits on many cards, so two
    requests for the same uri land together and both find an empty cache. A
    plain write creates the file empty and fills it, so a reader in between
    gets a truncated image and the browser keeps it for a day.

    Written to a name of its own first and renamed into place, which is one
    step: a reader sees the old file or the new one, never half of either. The
    temporary name leads with a dot and does not start with the key, so the
    `{key}.*` lookups cannot pick it up while it exists.

    The rename can fail on Windows, which refuses to replace a file another
    handle has open. That is not a failure here - it means the request that
    got there first has already written this exact image, so this copy is
    dropped and the one on disk is left alone.
    """
    k = key(u, w)
    d = _shard(k)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{k}{ext}"
    tmp = d / f".tmp-{k}-{os.getpid()}-{threading.get_ident()}"
    tmp.write_bytes(data)
    try:
        os.replace(tmp, p)
    except OSError:
        if not p.exists():
            raise
        tmp.unlink(missing_ok=True)
    return p


def prune() -> tuple[int, int]:
    """Delete every entry past the ttl. Returns (files, bytes) reclaimed.

    Run off the request path, from the indexer's cache loop. It walks the
    shards and the top folder with them, because the entries that predate
    the shards are still loose there, and judges every file on its own
    modification time. One rule sweeps three kinds of dead weight: an entry
    that expired, a `.tmp-` file left behind by a write that died before its
    rename, and an entry that was replaced under a different extension and
    now sits beside the live one. Nothing younger than the ttl is a
    candidate, so a write in flight - a temporary file seconds old - is
    never touched.

    A file that will not delete is skipped rather than raised over: an open
    handle, OneDrive reading it, a directory that changed under the walk.
    Whatever it was is still there for the next pass.
    """
    if C.IMAGE_TTL_SEC <= 0:
        return 0, 0
    cutoff = time.time() - C.IMAGE_TTL_SEC
    try:
        shards = [p for p in C.CACHE_DIR.iterdir() if p.is_dir()]
    except OSError:
        # No cache folder yet, or it cannot be listed. Either way there is
        # nothing here to expire.
        return 0, 0

    files = 0
    freed = 0
    for d in [C.CACHE_DIR, *shards]:
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if not p.is_file():
                    continue
                st = p.stat()
                if st.st_mtime > cutoff:
                    continue
                p.unlink()
            except OSError:
                continue
            files += 1
            freed += st.st_size
    return files, freed


def resolve(u: str) -> list[str]:
    """Candidate URLs for a logo uri, best gateway first."""
    if u.startswith("ipfs://"):
        cid = u[len("ipfs://"):].lstrip("/")
        return [g + cid for g in C.IPFS_GATEWAYS]
    if u.startswith(("http://", "https://")):
        return [u]
    if u.startswith("data:"):
        return []
    # bare CID
    if len(u) > 20 and "/" not in u and " " not in u:
        return [g + u for g in C.IPFS_GATEWAYS]
    return []


def ext_for(url: str) -> str:
    for e in IMAGE_EXTS:
        if url.lower().split("?")[0].endswith(e):
            return e
    return ".img"


def fetch(urls: list[str], limit: int | None = None) -> tuple[bytes, str, str]:
    """The first gateway that answers with an image.

    Returns (bytes, content type, extension) and raises TooLarge if what came
    back is bigger than this proxy holds. Sequential rather than racing: the
    gateways are tried best-first by measured answer rate, so the first one is
    usually right, and a race would spend five requests to pick the same
    winner.
    """
    limit = C.IMAGE_MAX_BYTES if limit is None else limit
    last = "no gateway answered"
    for url in urls:
        try:
            r = _session().get(url, timeout=C.IMAGE_TIMEOUT)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "image" not in ctype and not url.lower().endswith(IMAGE_EXTS):
                last = f"not an image: {ctype}"
                continue
            data = r.content
            if not data:
                last = "empty body"
                continue
            if len(data) > limit:
                raise TooLarge(f"{len(data)} bytes")
            ext = ext_for(url)
            if ext == ".img":
                ext = mimetypes.guess_extension(ctype) or ".img"
            return data, (ctype or "image/png"), ext
        except TooLarge:
            raise
        except Exception as e:
            last = str(e)[:100]
            continue
    log.debug("no gateway answered for %s: %s", urls[:1], last)
    raise LookupError(last)


def ensure(u: str, w: int = 0) -> Path | None:
    """The cache entry for this uri, fetching it if it is not there yet.

    What the background walk calls. It answers None rather than raising for
    everything that is not worth a second try: a uri that resolves nowhere, a
    gateway that never answered, an image that is too big to keep.
    """
    p = cached(u, w)
    if p is not None:
        return p
    urls = resolve(u)
    if not urls:
        return None
    try:
        data, _ctype, ext = fetch(urls)
    except (LookupError, TooLarge) as e:
        log.debug("prefetch gave up on %s: %s", u[:60], str(e)[:80])
        return None
    return put(u, w, data, ext)
