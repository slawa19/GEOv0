import ast,os,subprocess
from collections import defaultdict
os.chdir(r"<repo>")
files=[f for f in subprocess.check_output(["git","ls-files","app","scripts","tests"],text=True,encoding="utf-8").splitlines() if f.endswith(".py")]
defs=defaultdict(list); calls=defaultdict(list)
for f in files:
    t=ast.parse(open(f,encoding="utf-8").read())
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and f.startswith("app/"):
            defs[n.name].append((f,n))
        elif isinstance(n,ast.Call):
            fn=n.func; name=fn.attr if isinstance(fn,ast.Attribute) else (fn.id if isinstance(fn,ast.Name) else None)
            if name: calls[name].append((f,n))
for name,dl in defs.items():
    if len(dl)!=1: continue
    f,n=dl[0]
    if any(isinstance(d,ast.Attribute) and d.attr in ("get","post","patch","delete","put","websocket","middleware") for d in n.decorator_list) or any(isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr in ("get","post","patch","delete","put","websocket","middleware","field_validator","model_validator") for d in n.decorator_list): continue
    cs=calls.get(name,[])
    prod=[c for c in cs if not c[0].startswith("tests/")]
    if not prod: continue
    a=n.args
    kwonly=list(zip(a.kwonlyargs,a.kw_defaults))
    pos=a.posonlyargs+a.args
    posdef=list(zip(pos[len(pos)-len(a.defaults):],a.defaults))
    for arg,d in kwonly+posdef:
        if d is None: continue
        pname=arg.arg
        idx=[i for i,p in enumerate(pos) if p.arg==pname]
        used_prod=False; used_test=False
        for cf,c in cs:
            hit=any(k.arg==pname for k in c.keywords) or any(k.arg is None for k in c.keywords)
            if idx and not hit:
                off=1 if pos and pos[0].arg in ("self","cls") and isinstance(c.func,ast.Attribute) else 0
                if len(c.args)+off>idx[0] or any(isinstance(x,ast.Starred) for x in c.args): hit=True
            if hit:
                if cf.startswith("tests/"): used_test=True
                else: used_prod=True
        if not used_prod:
            print(f"{f}:{n.lineno} {name}({pname}={ast.unparse(d)[:30]}) prod_calls={len(prod)} tests_override={used_test}")
