"""AST import graph for app/ — layer edges, violations, cycles, lazy imports."""
import ast
import os
import sys
from collections import defaultdict

ROOT = r"<repo>"
APP = os.path.join(ROOT, "app")


def layer(mod: str) -> str:
    parts = mod.split(".")
    if len(parts) < 2:
        return "app"
    if parts[1] in ("main", "config"):
        return parts[1]
    if parts[1] == "core":
        if len(parts) >= 3 and parts[2] == "simulator":
            return "core.simulator"
        return "core"
    return parts[1]


def modname(path: str) -> str:
    rel = os.path.relpath(path, ROOT).replace(os.sep, ".")
    rel = rel[:-3]
    if rel.endswith(".__init__"):
        rel = rel[: -len(".__init__")]
    return rel


edges = defaultdict(set)  # mod -> set of (target, lazy(bool), lineno)
files = {}
for dp, dn, fn in os.walk(APP):
    if "__pycache__" in dp:
        continue
    for f in fn:
        if f.endswith(".py"):
            p = os.path.join(dp, f)
            files[modname(p)] = p

for mod, p in files.items():
    src = open(p, encoding="utf-8").read()
    tree = ast.parse(src)
    # top-level imports vs nested
    def visit(node, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                lazy = depth > 0
                if isinstance(child, ast.Import):
                    for a in child.names:
                        if a.name.startswith("app"):
                            edges[mod].add((a.name, lazy, child.lineno))
                else:
                    if child.module and child.module.startswith("app"):
                        base = child.module
                        for a in child.names:
                            edges[mod].add((base + "." + a.name if base + "." + a.name in files else base, lazy, child.lineno))
                    elif child.level:
                        pkg = mod.rsplit(".", child.level)[0] if p.endswith("__init__.py") is False else mod.rsplit(".", child.level - 1)[0]
                        base = pkg + ("." + child.module if child.module else "")
                        for a in child.names:
                            edges[mod].add((base + "." + a.name if base + "." + a.name in files else base, lazy, child.lineno))
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.Try, ast.With, ast.For, ast.While)):
                visit(child, depth + 1 if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else depth)
            else:
                visit(child, depth)
    visit(tree, 0)

# layer matrix
layer_edges = defaultdict(lambda: defaultdict(set))
for m, tg in edges.items():
    for t, lazy, ln in tg:
        layer_edges[layer(m)][layer(t)].add((m, t, lazy, ln))

print("== LAYER MATRIX (count of import statements) ==")
for a in sorted(layer_edges):
    for b in sorted(layer_edges[a]):
        print(f"{a:15} -> {b:15} {len(layer_edges[a][b])}")

print("\n== VIOLATIONS ==")
allowed_down = {
    "main": {"api", "core", "core.simulator", "db", "schemas", "utils", "config"},
    "api": {"core", "core.simulator", "db", "schemas", "utils", "config", "api"},
    "core": {"core", "db", "utils", "config", "schemas"},
    "core.simulator": {"core", "core.simulator", "db", "utils", "config", "schemas"},
    "db": {"db", "config", "utils"},
    "schemas": {"schemas", "utils"},
    "utils": {"utils", "config"},
    "config": set(),
}
suspect = [
    ("core", "api"), ("core.simulator", "api"), ("db", "core"), ("db", "core.simulator"), ("db", "api"),
    ("db", "schemas"), ("utils", "core"), ("utils", "core.simulator"), ("utils", "db"), ("utils", "api"), ("utils", "schemas"),
    ("schemas", "core"), ("schemas", "db"), ("schemas", "api"), ("core", "schemas"), ("core.simulator", "schemas"),
    ("config", "core"), ("core", "main"), ("api", "main"), ("core", "core.simulator"),
]
for a, b in suspect:
    for m, t, lazy, ln in sorted(layer_edges.get(a, {}).get(b, set())):
        print(f"{a}->{b}: {m}:{ln} imports {t}{' [LAZY]' if lazy else ''}")

print("\n== LAZY IMPORTS (inside function bodies) ==")
for m in sorted(edges):
    for t, lazy, ln in sorted(edges[m]):
        if lazy:
            print(f"{m}:{ln} -> {t}")

# cycles via Tarjan on module graph (only top-level edges) and with lazy
def sccs(use_lazy: bool):
    g = defaultdict(set)
    for m, tg in edges.items():
        for t, lazy, ln in tg:
            if not use_lazy and lazy:
                continue
            # normalize target to a known module (or its package)
            tt = t
            while tt not in files and "." in tt:
                tt = tt.rsplit(".", 1)[0]
            if tt in files and tt != m:
                g[m].add(tt)
    index = {}
    low = {}
    stack = []
    onstack = set()
    out = []
    counter = [0]
    sys.setrecursionlimit(10000)

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        onstack.add(v)
        for w in g.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                onstack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))
    for v in list(files):
        if v not in index:
            strong(v)
    return out

print("\n== CYCLES (top-level imports only) ==")
for c in sccs(False):
    print(c)
print("\n== CYCLES (including lazy imports) ==")
for c in sccs(True):
    print(c)

print("\n== FAN-IN of utils/schemas/db modules ==")
fanin = defaultdict(set)
for m, tg in edges.items():
    for t, lazy, ln in tg:
        tt = t
        while tt not in files and "." in tt:
            tt = tt.rsplit(".", 1)[0]
        fanin[tt].add(m)
for t in sorted(fanin):
    if t.startswith(("app.utils", "app.schemas", "app.db", "app.config")):
        print(f"{t:45} <- {len(fanin[t])}: {sorted(fanin[t])[:8]}{'...' if len(fanin[t])>8 else ''}")
