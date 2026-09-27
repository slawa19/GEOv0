import ast,subprocess,os
os.chdir(r"<repo>")
t=ast.parse(open("app/config.py",encoding="utf-8").read())
cls=[n for n in t.body if isinstance(n,ast.ClassDef) and n.name=="Settings"][0]
for n in cls.body:
    if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name):
        name=n.target.id
        def g(paths):
            r=subprocess.run(["git","grep","-n","-w",name,"--"]+paths,capture_output=True,text=True,encoding="utf-8",errors="replace").stdout.splitlines()
            return [x for x in r if not x.startswith("app/config.py")]
        a=g(["app"]); ts=g(["tests"]); sc=g(["scripts","migrations","docker","docker-compose.yml","docker-compose.dev.yml",".github"]); ui=g(["admin-ui/src","simulator-ui/v2/src"])
        cfgself=[x for x in subprocess.run(["git","grep","-n","-w",name,"--","app/config.py"],capture_output=True,text=True,encoding="utf-8",errors="replace").stdout.splitlines()]
        print(f"{name:45s} line={n.lineno:4d} app={len(a):3d} cfg_self={len(cfgself)} tests={len(ts):3d} ops={len(sc):3d} ui={len(ui):3d}  " + (" | ".join(x[:90] for x in a[:2])))
