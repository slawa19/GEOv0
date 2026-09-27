import ast,os,subprocess,importlib.util
os.chdir(r"<repo>")
files=[f for f in subprocess.check_output(["git","ls-files","scripts","migrations","admin-fixtures","seeds","tests"],text=True,encoding="utf-8").splitlines() if f.endswith(".py")]
def modfile(mod):
    p=mod.replace(".","/")
    for c in (p+".py",p+"/__init__.py"):
        if os.path.exists(c): return c
    return None
cache={}
def names(path):
    if path in cache: return cache[path]
    t=ast.parse(open(path,encoding="utf-8").read()); s=set()
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): s.add(n.name)
        elif isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store): s.add(n.id)
        elif isinstance(n,ast.alias): s.add((n.asname or n.name).split(".")[0])
        elif isinstance(n,ast.arg): pass
    cache[path]=s; return s
for f in files:
    t=ast.parse(open(f,encoding="utf-8").read())
    for n in ast.walk(t):
        if isinstance(n,ast.ImportFrom) and n.module and n.module.startswith("app") and not n.level:
            mf=modfile(n.module)
            if not mf: print(f"{f}:{n.lineno} MISSING MODULE {n.module}"); continue
            for a in n.names:
                if a.name=="*": continue
                if modfile(n.module+"."+a.name): continue
                if a.name not in names(mf): print(f"{f}:{n.lineno} MISSING NAME {n.module}.{a.name}")
        elif isinstance(n,ast.Import):
            for a in n.names:
                if a.name.startswith("app") and not modfile(a.name): print(f"{f}:{n.lineno} MISSING MODULE {a.name}")
