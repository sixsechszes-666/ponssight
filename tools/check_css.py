"""Check the stylesheets against the contract in DESIGN.md.

Run with no argument to check every sheet, or with a filename to check one.
Nothing here parses CSS properly - it does not need to. The four things that
actually go wrong when several people write sheets against one token file are
a typo'd token name, a raw colour that drifts from the palette, an unbalanced
brace that silently eats the rest of the file, and a class nobody covered.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSS = ROOT / "static" / "css"
# The cascade order is the order the page loads them in, so it is read from the
# page instead of kept here as a second copy. The copy drifted the moment a
# sheet was added: this file still scanned ten of them, the coverage check below
# read those ten, and every class in the eleventh was reported as unstyled -
# which reads as sixteen missing rules rather than one missing line.
PAGE = ROOT / "static" / "index.html"
ORDER = re.findall(r'href="/static/css/([a-z0-9_-]+)[.]css"',
                   PAGE.read_text(encoding="utf-8"))
if not ORDER:
    sys.exit("index.html links no stylesheets - there is nothing to check")
ON_DISK = sorted(x.stem for x in CSS.glob("*.css"))
if sorted(ORDER) != ON_DISK:
    print("stylesheets on disk and stylesheets linked do not match")
    print("  on disk only: %s" % " ".join(sorted(set(ON_DISK) - set(ORDER))))
    print("  linked only:  %s" % " ".join(sorted(set(ORDER) - set(ON_DISK))))
    sys.exit(1)

# `--line` in a shorthand is a token; `-1px` is not. Names are matched where
# they are used, `var(--x)`, and where they are defined, `--x:`.
USE = re.compile(r"var\(\s*(--[a-z0-9-]+)")
# Not anchored to the line: tokens.css groups related tokens on one line
# (--s1: 4px;  --s2: 8px;  ...), and an anchored pattern only ever saw the
# first of them and then reported the rest as undefined. A declaration is the
# only place a name is followed by a colon - var(--x) and --x) in a value are
# not - so the colon is the discriminator, not the line start.
DEF = re.compile(r"(--[a-z0-9-]+)\s*:")
RAW = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")
DASH = re.compile("[—–]")
# The reduced-motion block exists to defeat the cascade on purpose - it has to
# outrank whatever transition or animation another sheet set, and there is no
# way to do that from a media query without !important.
REDUCED = re.compile(r"@media[^{]*prefers-reduced-motion[^{]*\{[^}]*\}", re.S)


def segments(code):
    """Split a stylesheet into (depth, text) runs at every brace.

    The text of a run is what sits between two braces, so a run at depth 1 is
    the inside of a rule and a run at depth 0 is either the top of the file or
    the gap between two rules. That distinction is the whole point: a token
    declared in a depth-0 run is a token the browser throws away, and the
    regex-only check this file used to do could not tell the difference - it
    found the text `--lb-sniped:` in the file, called the token defined, and
    reported success while every label on the page rendered colourless.
    """
    out, buf, depth = [], [], 0
    for ch in code:
        if ch == "{":
            out.append((depth, "".join(buf)))
            buf = []
            depth += 1
        elif ch == "}":
            out.append((depth, "".join(buf)))
            buf = []
            depth = max(0, depth - 1)
        else:
            buf.append(ch)
    out.append((depth, "".join(buf)))
    return out

fail = 0


def say(bad, path, msg):
    global fail
    if bad:
        fail += 1
        print("  FAIL %-14s %s" % (path.name, msg))
    return bad


only = sys.argv[1] if len(sys.argv) > 1 else None
tokens_file = CSS / "tokens.css"
if not tokens_file.exists():
    sys.exit("tokens.css is missing - nothing to check against")
_tokseg = segments(re.sub(r"/\*.*?\*/", "",
                         tokens_file.read_text(encoding="utf-8"), flags=re.S))
defined = {m for d, txt in _tokseg if d >= 1 for m in DEF.findall(txt)}
print("tokens defined: %d" % len(defined))

sheets = []
for name in ORDER:
    p = CSS / (name + ".css")
    if only and p.name != only:
        continue
    if not p.exists():
        print("  MISSING %s" % p.name)
        fail += 1
        continue
    sheets.append(p)

for p in sheets:
    s = p.read_text(encoding="utf-8")
    # Comments hold prose, and prose legitimately contains braces and words
    # that look like selectors, so strip them before anything structural.
    code = re.sub(r"/\*.*?\*/", "", s, flags=re.S)

    say(code.count("{") != code.count("}"), p,
        "unbalanced braces: %d open, %d close"
        % (code.count("{"), code.count("}")))

    unknown = sorted(set(USE.findall(code)) - defined)
    say(bool(unknown), p, "undefined tokens: " + ", ".join(unknown))

    # A declaration outside a rule block parses as nothing at all. It is the
    # quietest way to lose a stylesheet, because the file still looks right.
    orphans = [m for d, txt in segments(code) if d == 0
               for m in DEF.findall(txt)]
    say(bool(orphans), p,
        "declared outside any block (dropped by the browser): "
        + ", ".join(sorted(set(orphans))))

    if p.name != "tokens.css":
        raw = sorted(set(RAW.findall(code)))
        say(bool(raw), p, "raw colours (use a token): " + ", ".join(raw))

    say(bool(DASH.search(s)), p, "em or en dash - this project uses hyphens")

    bangs = [ln for ln in REDUCED.sub("", code).split("\n")
             if "!important" in ln and "[hidden]" not in ln]
    say(bool(bangs), p, "!important outside the [hidden] rule: %d" % len(bangs))

    print("  ok   %-14s %5d bytes" % (p.name, len(s)))

# Coverage: every class the markup and the renderers emit should appear in at
# least one sheet. A missed class is an unstyled element, which looks like a
# bug rather than a gap.
inv = ROOT / "tools" / "contract" / "classes.txt"
if inv.exists() and not only:
    want = [c for c in inv.read_text(encoding="utf-8").split() if c]
    allcss = "\n".join(p.read_text(encoding="utf-8") for p in sheets)
    missing = [c for c in want
               if not re.search(r"\." + re.escape(c) + r"[^a-zA-Z0-9_-]", allcss)]
    if missing:
        print("\nunstyled classes (%d): %s" % (len(missing), " ".join(missing)))
    else:
        print("\nevery one of %d classes is styled" % len(want))

print("\n%s" % ("FAILED: %d problem(s)" % fail if fail else "all checks passed"))
sys.exit(1 if fail else 0)
