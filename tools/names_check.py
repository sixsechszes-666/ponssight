"""Report names a module reads but never binds, and names it reads off config.

Two failures this catches, both of them silent at import time:

  * a global that is not defined anywhere - `store.conn()` is the reason this
    exists, because a function removed by a bad range edit leaves every caller
    failing at request time rather than at startup, and `py_compile` and the
    gate's parse check both pass;
  * `C.SOMETHING` or `store.something` that no longer exists, which is how a
    subsystem removal leaves a live call site behind.

Names are resolved with `symtable` rather than by regex: a name that is local,
a parameter, a comprehension variable or an attribute is not a global read,
and only the compiler's own scoping knows which is which.

    python tools/names_check.py            # every module in the project
    python tools/names_check.py store.py   # one
"""
from __future__ import annotations

import ast
import builtins
import os
import symtable
import sys

SKIP_DIRS = {"_recon", "__pycache__", "static", "data", ".venv", "venv"}


def module_names(root: str = ".") -> dict[str, str]:
    """Local module name -> path, so `store.x` can be resolved to store.py."""
    out: dict[str, str] = {}
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            if n.endswith(".py"):
                out.setdefault(n[:-3], os.path.join(dirpath, n).replace(os.sep, "/"))
    return out


def attrs_read(path: str, aliases: dict[str, str],
               exported: dict[str, set[str]],
               local: set[str]) -> list[tuple[int, str, str]]:
    """`alias.attr` reads where the alias names one of our own modules and the
    attribute is not a name that module defines. Import aliases are followed,
    so `import config as C` resolves `C.CHAIN_ID` against config.py.

    A name that any scope in this file binds is skipped, because `snipe` and
    `handles` are both local variables in server.py and module names here. The
    price of skipping by file rather than by scope is that a real miss on a
    shadowed name goes unreported; the price of not skipping is two false
    reports on every run, and a check that cries wolf is a check nobody reads.
    """
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
    except (OSError, SyntaxError) as e:
        return [(0, "?", "cannot parse: %s" % e)]
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id in local:
            continue
        mod = aliases.get(node.value.id, node.value.id)
        if mod not in exported or mod == path[:-3].replace(os.sep, "/").split("/")[-1]:
            continue
        if node.attr not in exported[mod]:
            out.append((node.lineno, mod, node.attr))
    return sorted(set(out))


def bound_names(top: symtable.SymbolTable) -> set[str]:
    """Every name bound inside a function: locals, parameters, assignments.

    The module's own scope is left out on purpose. Its bindings are the
    imports - `import config as C` binds `C` there - and a filter that skips
    those would skip every name this check exists to resolve. What has to be
    skipped is a function that shadows a module name with a local, which is
    `snipe` and `handles` in server.py.
    """
    out = set()

    def walk_tbl(t: symtable.SymbolTable) -> None:
        for s in t.get_symbols():
            if s.is_local() or s.is_parameter() or s.is_assigned():
                out.add(s.get_name())
        for child in t.get_children():
            walk_tbl(child)

    for child in top.get_children():
        walk_tbl(child)
    return out


def import_aliases(path: str) -> dict[str, str]:
    """Local name -> module name for every `import x` / `import x as y`."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
    except (OSError, SyntaxError):
        return {}
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name.split(".")[0]
    return out


def globals_of(path: str) -> tuple[set[str], symtable.SymbolTable] | None:
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        top = symtable.symtable(src, path, "exec")
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        print("%s: cannot read: %s" % (path, e))
        return None
    names = {s.get_name() for s in top.get_symbols()}
    return names, top


def walk(table, out: list[tuple[str, str]], known: set[str]) -> None:
    """Report every global read inside `table` that `known` does not have."""
    scope = table.get_name()
    for sym in table.get_symbols():
        name = sym.get_name()
        if sym.is_global() and not sym.is_assigned() and name not in known \
                and not hasattr(builtins, name):
            out.append((scope, name))
    for child in table.get_children():
        walk(child, out, known)


def main(argv: list[str]) -> int:
    mods = module_names()
    files = argv[1:]
    if not files:
        files = sorted(mods.values())
    bad = 0
    exported: dict[str, set[str]] = {}
    for name, path in mods.items():
        got = globals_of(path)
        if got is None:
            bad += 1
            continue
        exported[name] = got[0]
    for path in sorted(files):
        name = os.path.basename(path)[:-3]
        got = globals_of(path)
        if got is None:
            bad += 1
            continue
        known, top = got
        out: list[tuple[str, str]] = []
        walk(top, out, known)
        for scope, sym in sorted(set(out)):
            print("%s: %s() reads %r, which is not defined" % (path, scope, sym))
            bad += 1
        for line, mod, attr in attrs_read(path, import_aliases(path), exported,
                                          bound_names(top)):
            print("%s:%d: %s.%s does not exist" % (path, line, mod, attr))
            bad += 1
        del name
    print("names: %s" % ("all defined" if not bad else "%d problem(s)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
