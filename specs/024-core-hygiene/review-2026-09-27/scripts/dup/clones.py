"""Clone candidate generator. Windows of 5 normalized statements (>=8 lines) + function-level stmt-shingle Jaccard."""
import ast, sys, os, hashlib, collections, copy
ROOT = sys.argv[1]
MIN_LINES = 8
WIN = 5

class Norm(ast.NodeTransformer):
    def __init__(self): self.map = {}
    def _n(self, name):
        if name not in self.map: self.map[name] = f"v{len(self.map)}"
        return self.map[name]
    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id=self._n(node.id), ctx=node.ctx), node)
    def visit_arg(self, node):
        node.arg = self._n(node.arg); node.annotation = None; return node
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value="S"), node)
        return node
    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        self.generic_visit(node); return node

def norm_dump(stmts):
    n = Norm(); out = []
    for s in stmts:
        s2 = n.visit(copy.deepcopy(s))
        if s2 is None: continue
        out.append(ast.dump(s2, annotate_fields=False))
    return out

def leaf_stmts(stmts):
    """Flatten to simple statements (normalized per function) for shingles."""
    res = []
    for s in stmts:
        for node in ast.walk(s):
            if isinstance(node, ast.stmt) and not hasattr(node, "body"):
                res.append(node)
    return res

files = []
for dp, dn, fn in os.walk(ROOT):
    if "__pycache__" in dp: continue
    for f in fn:
        if f.endswith(".py"): files.append(os.path.join(dp, f))

funcs = []
windows = collections.defaultdict(list)
for path in files:
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    rel = os.path.relpath(path, os.path.dirname(ROOT)).replace("\\", "/")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ln = (node.end_lineno or node.lineno) - node.lineno + 1
            if ln >= MIN_LINES:
                lines = norm_dump(leaf_stmts(node.body))
                funcs.append((rel, node.name, node.lineno, node.end_lineno, lines))
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if not isinstance(body, list) or not body or not isinstance(body[0], ast.stmt): continue
            for i in range(0, max(0, len(body) - WIN + 1)):
                chunk = body[i:i+WIN]
                a, b = chunk[0].lineno, chunk[-1].end_lineno
                if b - a + 1 < MIN_LINES: continue
                h = hashlib.md5("\n".join(norm_dump(chunk)).encode()).hexdigest()
                windows[h].append((rel, a, b))

print("=== exact normalized window clones (5 stmts, >=8 lines) ===")
groups = [sorted(set(l)) for l in windows.values() if len(set(l)) > 1]
groups.sort(key=lambda g: -max(b-a for _, a, b in g))
seen = set(); c = 0
for g in groups:
    key = tuple((r, a // 25) for r, a, b in g)
    if key in seen: continue
    seen.add(key)
    print(" | ".join(f"{r}:{a}-{b}" for r, a, b in g)); c += 1
    if c >= 80: break

print("\n=== near-duplicate functions (stmt-shingle jaccard>=0.6) ===")
def sh(lines):
    S = set(hashlib.md5(l.encode()).hexdigest() for l in lines)
    S |= set(hashlib.md5((a + "|" + b).encode()).hexdigest() for a, b in zip(lines, lines[1:]))
    return S
fs = [(f, sh(f[4])) for f in funcs if len(f[4]) >= 4]
inv = collections.defaultdict(list)
for idx, (f, S) in enumerate(fs):
    for x in S: inv[x].append(idx)
cnt = collections.Counter()
for x, ids in inv.items():
    if len(ids) > 30: continue
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            cnt[(ids[a], ids[b])] += 1
pairs = []
for (a, b), k in cnt.items():
    Sa, Sb = fs[a][1], fs[b][1]
    j = k / len(Sa | Sb)
    if j >= 0.6:
        fa, fb = fs[a][0], fs[b][0]
        if fa[0] == fb[0] and (fa[2] <= fb[2] <= fa[3] or fb[2] <= fa[2] <= fb[3]): continue
        pairs.append((j, fa, fb))
pairs.sort(key=lambda p: -(p[0] * min(p[1][3]-p[1][2], p[2][3]-p[2][2])))
for j, a, b in pairs[:60]:
    print(f"{j:.2f}  {a[0]}:{a[2]}-{a[3]} {a[1]}  <->  {b[0]}:{b[2]}-{b[3]} {b[1]}")
