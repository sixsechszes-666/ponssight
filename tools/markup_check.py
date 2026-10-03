"""Prove the markup rewrite did not drop an id or a class the code needs.

The JS was split out untouched, so it still reaches for elements by exactly the
ids the old single file had. A rewritten body that loses one of them fails
silently at runtime - a $("#x") returning null and a TypeError three lines
later - so this runs before anybody styles anything.

The reference is tools/contract/old_markup.html: this project's own index.html
as it stood before the split, kept in the repository because the comparison is
only meaningful against the file the ids actually came from.

It also rebuilds the class inventory the CSS coverage check reads. Class names
are scraped out of template literals, so the scrape catches expression
fragments too; anything that is not a plausible CSS identifier is dropped, and
a short deny list removes the few JS identifiers that happen to look like one.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "tools" / "contract"
old = (CONTRACT / "old_markup.html").read_text(encoding="utf-8")
new = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

ID = re.compile(r'\bid="([^"]+)"')
o = set(ID.findall(old))
n = set(ID.findall(new))
print("old ids %d, new ids %d" % (len(o), len(n)))
# Ids deliberately removed since the snapshot was taken. Each one needs a
# reason: without it the entry is indistinguishable from an accident, which is
# the thing this check exists to catch.
GONE = {
    "b-wallet": "the wallet badge became the #w-open connect widget",
    "b-wallet-v": "the value half of the same badge",
}
lost = sorted(o - n - set(GONE))
print("LOST (%d): %s" % (len(lost), " ".join(lost)))
removed = sorted(o - n & set(GONE))
if removed:
    for i in removed:
        print("  gone on purpose: %s - %s" % (i, GONE[i]))
print("added (%d): %s" % (len(n - o), " ".join(sorted(n - o))))

# The ids the code actually reaches for, which is the list that matters. Some
# are created by coin.js at render time and are legitimately absent from the
# markup, so they are excluded rather than reported as failures.
js = "\n".join(p.read_text(encoding="utf-8")
               for p in sorted((ROOT / "static" / "js").glob("*.js")))
want = set(re.findall(r'\$\("#([a-zA-Z0-9_-]+)"', js))
want |= set(re.findall(r'getElementById\("([a-zA-Z0-9_-]+)"', js))
DYNAMIC = {"coin-chartbox", "coin-cx", "coin-cx-d", "coin-cx-l",
           "coin-cx-read", "coin-rng", "coin-stats", "coin-svg"}
miss = sorted(want - n - DYNAMIC)
print("\nids the JS reads: %d" % len(want))
print("NOT IN MARKUP (%d): %s" % (len(miss), " ".join(miss)))

IDENT = re.compile(r"^[a-z][a-z0-9-]*$")
# Words that survive the identifier test but are code, not classes: locals and
# helpers that appear bare inside a `class="${...}"` interpolation.
DENY = {"esc", "null", "true", "false", "hot", "isnew", "cls", "c", "t", "s",
        "g", "h", "f", "b", "v", "k", "age", "net", "area", "copy", "status",
        "rep", "num", "ok"}
KEEP = {"age", "area", "num", "ok", "rep", "status"}   # real classes too
used = set()
for chunk in (new, js):
    for m in re.finditer(r'''class=["']([^"']+)["']''', chunk):
        for c in m.group(1).split():
            if IDENT.match(c):
                used.add(c)
    for m in re.finditer(r'classList\.(?:add|remove|toggle)\(([^)]*)\)', chunk):
        used |= {c for c in re.findall(r'''["']([a-zA-Z0-9_-]+)["']''',
                                       m.group(1)) if IDENT.match(c)}
    for m in re.finditer(r'''className\s*=\s*["']([^"']*)["']''', chunk):
        used |= {c for c in m.group(1).split() if IDENT.match(c)}
used -= (DENY - KEEP)
# Built by string concatenation rather than a class= literal, so the scrape
# cannot see them, but they are absolutely on the page. `g` and `r` are the
# graduated and non-ETH-pair variants of .pill, glued on in cells.js.
used |= {"pos", "neg", "okbox", "g", "r"}
(CONTRACT / "classes.txt").write_text(
    "\n".join(sorted(used)) + "\n", encoding="utf-8")
print("\nclasses to style: %d -> tools/contract/classes.txt" % len(used))
print(" ".join(sorted(used)))

sys.exit(1 if (lost or miss) else 0)
