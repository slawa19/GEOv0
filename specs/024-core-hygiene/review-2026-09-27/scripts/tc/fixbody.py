import ast,subprocess,collections,hashlib,sys
names=set(sys.argv[1:])
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if f.endswith('.py')]
g=collections.defaultdict(list)
for f in files:
    t=ast.parse(open(f,encoding='utf8').read())
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names and any('fixture' in ast.unparse(d) for d in n.decorator_list):
            b=n.body
            if b and isinstance(b[0],ast.Expr) and isinstance(getattr(b[0],'value',None),ast.Constant): b=b[1:]
            s=ast.unparse(ast.Module(body=b,type_ignores=[]))
            g[(n.name,hashlib.sha1(s.encode()).hexdigest()[:8])].append(f"{f}:{n.lineno}")
for k,v in sorted(g.items()): print(k,len(v),v)
