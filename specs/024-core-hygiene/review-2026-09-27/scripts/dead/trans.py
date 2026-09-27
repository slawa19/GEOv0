import ast,os,subprocess
from collections import defaultdict
os.chdir(r"<repo>")
files=[f for f in subprocess.check_output(["git","ls-files"],text=True,encoding="utf-8").splitlines() if f.endswith(".py") and f.startswith(("app/","scripts/","migrations/","seeds/","admin-fixtures/"))]
# refs: name -> list of (file,line)
refs=defaultdict(list); defs={}
for f in files:
    t=ast.parse(open(f,encoding="utf-8").read())
    for n in ast.walk(t):
        if isinstance(n,ast.Name) and not isinstance(n.ctx,ast.Store): refs[n.id].append((f,n.lineno))
        elif isinstance(n,ast.Attribute): refs[n.attr].append((f,n.lineno))
        elif isinstance(n,ast.alias): refs[n.name.split(".")[-1]].append((f,n.lineno))
    if f.startswith("app/"):
        for n in t.body:
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                if not n.decorator_list: defs.setdefault(n.name,[]).append((f,n.lineno,n.end_lineno,None))
                if isinstance(n,ast.ClassDef):
                    for m in n.body:
                        if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)) and not m.decorator_list and not m.name.startswith("__"):
                            defs.setdefault(m.name,[]).append((f,m.lineno,m.end_lineno,n.name))
defs={k:v for k,v in defs.items() if len(v)==1}
dead=set(k for k,v in defs.items() if not refs.get(k))
changed=True
while changed:
    changed=False
    ranges=[(defs[d][0][0],defs[d][0][1],defs[d][0][2]) for d in dead]
    for k,v in defs.items():
        if k in dead: continue
        live=[r for r in refs.get(k,[]) if not any(r[0]==f and a<=r[1]<=b for f,a,b in ranges)]
        if not live: dead.add(k); changed=True
for d in sorted(dead,key=lambda x:defs[x][0]):
    f,a,b,o=defs[d][0]; print(f"{f}:{a} {(o+'.' if o else '')+d} ({b-a+1} lines)")
