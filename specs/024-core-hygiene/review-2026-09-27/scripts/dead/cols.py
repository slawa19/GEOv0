import ast,os,subprocess,re
from collections import defaultdict
os.chdir(r"<repo>")
files=subprocess.check_output(["git","ls-files","app","scripts"],text=True,encoding="utf-8").splitlines()
attr=defaultdict(set); kw=defaultdict(set); strs=defaultdict(set)
for f in files:
    if not f.endswith(".py"): continue
    t=ast.parse(open(f,encoding="utf-8").read())
    for n in ast.walk(t):
        if isinstance(n,ast.Attribute): attr[n.attr].add(f)
        elif isinstance(n,ast.keyword) and n.arg: kw[n.arg].add(f)
        elif isinstance(n,ast.Constant) and isinstance(n.value,str):
            for w in re.findall(r"[a-z_][a-z0-9_]*",n.value): strs[w].add(f)
models=[f for f in files if f.startswith("app/db/models/") and f.endswith(".py")]
for f in models:
    t=ast.parse(open(f,encoding="utf-8").read())
    for c in t.body:
        if not isinstance(c,ast.ClassDef): continue
        for s in c.body:
            if isinstance(s,ast.AnnAssign) and isinstance(s.target,ast.Name):
                name=s.target.id
                src=ast.unparse(s.value) if s.value else ""
                if "mapped_column" not in src and "relationship" not in src: continue
                ext=lambda d:{x for x in d[name] if x!=f}
                r=ext(attr); w=ext(kw); st=ext(strs)
                if len(r)<=1 or not w:
                    print(f"{c.name}.{name:28s} {f}:{s.lineno} reads={sorted(r)[:3]} writes_kw={sorted(w)[:2]} strs={len(st)}")
