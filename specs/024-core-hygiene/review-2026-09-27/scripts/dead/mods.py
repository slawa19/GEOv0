import ast,os,subprocess
os.chdir(r"<repo>")
files=subprocess.check_output(["git","ls-files"],text=True,encoding="utf-8").splitlines()
py=[f for f in files if f.endswith(".py") and f.startswith(("app/","tests/","scripts/","migrations/","seeds/","admin-fixtures/"))]
imp={}
for f in py:
    t=ast.parse(open(f,encoding="utf-8").read())
    s=set()
    for n in ast.walk(t):
        if isinstance(n,ast.Import):
            for a in n.names: s.add(a.name)
        elif isinstance(n,ast.ImportFrom) and n.module:
            base=n.module
            if n.level:  # relative
                pkg=f.replace("/",".")[:-3].split(".")[:-n.level] if not f.endswith("__init__.py") else f.replace("/",".")[:-3].split(".")[:-(n.level)]
                base=".".join(pkg+[n.module]) if n.module else ".".join(pkg)
            s.add(base)
            for a in n.names: s.add(base+"."+a.name)
        elif isinstance(n,ast.ImportFrom) and n.level:
            pkg=f.replace("/",".")[:-3].split(".")[:-n.level]
            for a in n.names: s.add(".".join(pkg+[a.name]))
        elif isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith("app."):
            s.add(n.value)
    imp[f]=s
for f in py:
    if not f.startswith("app/"): continue
    mod=f[:-3].replace("/",".")
    if mod.endswith(".__init__"): continue
    users_app=[g for g,s in imp.items() if g!=f and any(x==mod or x.startswith(mod+".") for x in s) and not g.startswith("tests/")]
    users_t=[g for g,s in imp.items() if g!=f and any(x==mod or x.startswith(mod+".") for x in s) and g.startswith("tests/")]
    if len(users_app)<=1:
        print(f"{mod:55s} app_importers={users_app} tests={len(users_t)}")
