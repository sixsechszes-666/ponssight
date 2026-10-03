"""One command that runs every check, so the verdict is a single line.

Each check is independent and each fails loudly. Files are checked as bytes
where the encoding matters, because a cp1251 console will render a stray
byte as a question mark and nobody will notice the file is wrong.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
fails = []


def run(label, args, tail=None):
    r = subprocess.run(args, capture_output=True, cwd=str(ROOT))
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    lines = [ln for ln in out.strip().split("\n") if ln]
    if tail:
        lines = lines[-tail:]
    ok = r.returncode == 0
    print("%-5s %s" % ("ok" if ok else "FAIL", label))
    for ln in lines:
        print("      " + ln)
    if not ok:
        fails.append(label)
    return out


print("=" * 72)
run("markup: ids and class inventory", [PY, "tools/markup_check.py"], tail=6)
print("-" * 72)
run("css: tokens, balance, palette, coverage", [PY, "tools/check_css.py"], tail=3)
print("-" * 72)

# every js file must parse
js = sorted((ROOT / "static" / "js").glob("*.js"))
bad = []
for f in js:
    r = subprocess.run(["node", "--check", str(f)], capture_output=True)
    if r.returncode:
        bad.append("%s: %s" % (f.name, r.stderr.decode("utf-8", "replace")[:200]))
print("%-5s js: %d modules parse" % ("ok" if not bad else "FAIL", len(js)))
for b in bad:
    print("      " + b)
if bad:
    fails.append("js parse")
print("-" * 72)

# python files must parse
pys = ["store.py", "server.py", "chain.py", "indexer.py", "snipe.py",
       "trades.py", "config.py", "bridge.py", "repair.py", "keysafe.py",
       "imgcache.py", "handles.py", "xprofile.py", "xsource.py"]
import ast
badp = []
for f in pys:
    try:
        ast.parse((ROOT / f).read_text(encoding="utf-8"))
    except SyntaxError as e:
        badp.append("%s: %s" % (f, e))
print("%-5s python: %d modules parse" % ("ok" if not badp else "FAIL", len(pys)))
for b in badp:
    print("      " + b)
if badp:
    fails.append("python parse")
print("-" * 72)

# A parse check proves a file compiles and nothing else. `store.conn()` was
# removed by a bad range edit during the argus removal, and every check above
# passed while the server died on startup with AttributeError and the indexer
# failed every insert - so what is checked here is that every global a function
# reads exists, and that every `store.x` / `C.X` attribute is really defined.
run("names: every global and module attribute resolves",
    [PY, "tools/names_check.py"], tail=10)
print("-" * 72)

# no colour literal and no dash outside the token file
DASH = re.compile("[—–]")
RAW = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")
issues = []
for f in sorted((ROOT / "static" / "css").glob("*.css")):
    t = f.read_text(encoding="utf-8")
    if f.name != "tokens.css" and RAW.search(re.sub(r"/\*.*?\*/", "", t, flags=re.S)):
        issues.append("%s: colour literal" % f.name)
for f in sorted((ROOT / "static").rglob("*.js")):
    t = f.read_text(encoding="utf-8")
    if RAW.search(t):
        issues.append("%s: colour literal" % f.name)
    if DASH.search(t):
        issues.append("%s: long dash" % f.name)
for f in ["docs/DESIGN.md", "README.md"] + [p.name for p in sorted(ROOT.glob("*.py"))]:
    p = ROOT / f
    if p.exists() and DASH.search(p.read_text(encoding="utf-8")):
        issues.append("%s: long dash" % f)
print("%-5s style: no colour literals in css/js, no long dashes"
      % ("ok" if not issues else "FAIL"))
for i in issues:
    print("      " + i)
if issues:
    fails.append("style")

# css must be pure ascii, so the file encoding is never in question
nonascii = []
for f in sorted((ROOT / "static" / "css").glob("*.css")):
    for ch in f.read_text(encoding="utf-8"):
        if ord(ch) > 127:
            nonascii.append("%s: %r" % (f.name, ch))
            break
print("%-5s css: %s" % ("ok" if not nonascii else "FAIL",
                        "pure ascii" if not nonascii else " ".join(nonascii)))
if nonascii:
    fails.append("css ascii")
print("-" * 72)
run("live: page, assets, endpoints", [PY, "tools/verify_page.py"], tail=12)
print("-" * 72)

# The game logic, under the gate rather than beside it. Each test file is a
# script that ran green on its own for months; wiring them in here is what
# makes "the gate passed" mean the tests passed too, instead of meaning
# somebody remembered to run them.
run("tests: every test file", ["bash", "tools/run_tests.sh"], tail=3)

print("=" * 72)
print("GATE: %s" % ("FAILED - " + ", ".join(fails) if fails else "all checks passed"))
sys.exit(1 if fails else 0)
