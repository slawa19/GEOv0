import ast, os, collections
g = collections.defaultdict(list)
for dp, dn, fn in os.walk("app"):
    if "__pycache__" in dp: continue
    for f in fn:
        if not f.endswith(".py"): continue
        p = os.path.join(dp, f).replace("\\", "/")
        t = ast.parse(open(p, encoding="utf-8").read())
        for n in ast.walk(t):
            if isinstance(n, ast.ExceptHandler):
                typ = ast.unparse(n.type) if n.type else "BARE"
                body = "\n".join(ast.unparse(s) for s in n.body)
                if len(body) < 25: continue
                g[(typ, body)].append(f"{p}:{n.lineno}")
rows = sorted(g.items(), key=lambda kv: -len(kv[1]))
for (typ, body), locs in rows[:25]:
    if len(locs) < 3: break
    print(len(locs), typ, "::", body.replace("\n", " ; ")[:160])
    print("    ", ", ".join(locs[:8]), "..." if len(locs) > 8 else "")
