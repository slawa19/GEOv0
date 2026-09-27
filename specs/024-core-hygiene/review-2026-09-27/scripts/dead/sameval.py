import ast,os,subprocess
from collections import defaultdict
os.chdir(r"<repo>")
files=[f for f in subprocess.check_output(["git","ls-files","app","scripts"],text=True,encoding="utf-8").splitlines() if f.endswith(".py")]
defs=defaultdict(list); calls=defaultdict(list)
for f in files:
    t=ast.parse(open(f,encoding="utf-8").read())
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and f.startswith("app/"): defs[n.name].append((f,n))
        elif isinstance(n,ast.Call):
            fn=n.func; name=fn.attr if isinstance(fn,ast.Attribute) else (fn.id if isinstance(fn,ast.Name) else None)
            if name: calls[name].append((f,n))
for name,dl in defs.items():
    if len(dl)!=1: continue
    f,n=dl[0]; cs=[c for c in calls.get(name,[])]
    if len(cs)<1: continue
    params=[a.arg for a in n.args.args+n.args.kwonlyargs if a.arg not in("self","cls")]
    for p in params:
        vals=[]; ok=True
        for cf,c in cs:
            kv=[k for k in c.keywords if k.arg==p]
            if any(k.arg is None for k in c.keywords): ok=False;break
            if kv: vals.append(ast.unparse(kv[0].value))
            else:
                pos=[a.arg for a in n.args.args]
                off=1 if pos and pos[0] in("self","cls") and isinstance(c.func,ast.Attribute) else 0
                if p in pos and pos.index(p)-off < len(c.args) and pos.index(p)-off>=0:
                    vals.append(ast.unparse(c.args[pos.index(p)-off]))
                else: vals.append("<default>")
        if not ok or not vals: continue
        if len(set(vals))==1 and vals[0] in ("True","False","None") and len(cs)>=2:
            print(f"{f}:{n.lineno} {name}({p}) always {vals[0]} over {len(cs)} calls")
