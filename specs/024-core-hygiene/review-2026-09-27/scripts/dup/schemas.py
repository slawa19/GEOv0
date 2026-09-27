import ast, os, collections
groups = collections.defaultdict(list)
for dp, dn, fn in os.walk("app"):
    if "__pycache__" in dp: continue
    for f in fn:
        if not f.endswith(".py"): continue
        p = os.path.join(dp, f).replace("\\", "/")
        t = ast.parse(open(p, encoding="utf-8").read())
        for n in t.body:
            if isinstance(n, ast.ClassDef):
                fields = []
                for s in n.body:
                    if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
                        fields.append((s.target.id, ast.unparse(s.annotation)))
                if len(fields) >= 3:
                    groups[tuple(sorted(fields))].append(f"{p}:{n.lineno} {n.name}")
for k, v in groups.items():
    if len(v) > 1:
        print(len(k), v)
# near: same field-name set, different annotations
names = collections.defaultdict(list)
for k, v in groups.items():
    names[tuple(sorted(x[0] for x in k))].extend(v)
print("--- same names, any annotation ---")
for k, v in names.items():
    if len(v) > 1 and len(k) >= 3:
        print(len(k), v)
