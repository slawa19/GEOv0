import ast,os,glob
os.chdir(r"<repo>")
for f in sorted(glob.glob("app/**/*.py",recursive=True)):
    t=ast.parse(open(f,encoding="utf-8").read())
    body=[n for n in t.body if not (isinstance(n,ast.Expr) and isinstance(getattr(n,'value',None),ast.Constant))]
    imps=[n for n in body if isinstance(n,(ast.Import,ast.ImportFrom))]
    defs=[n for n in body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    if body and len(defs)<=1 and len(imps)>=1 and (len(imps)/len(body))>=0.5:
        print(f"{f}: stmts={len(body)} imports={len(imps)} defs={[d.name for d in defs]} lines={len(open(f,encoding='utf-8').read().splitlines())}")
