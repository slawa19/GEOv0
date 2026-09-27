import ast,subprocess,re,sys,collections
pats={k:re.compile(v,re.I) for k,v in {
 'PREPARED':r'\bPREPARED\b','prepare_locks':r'prepare_lock|PrepareLock','PaymentEngine':r'PaymentEngine|payments\.engine|_run_uow_with_retry|_apply_flow',
 'interlock':r'interlock','journal_py':r'ledger\.journal\b|ledger import journal','sqlite':r'sqlite','dialect':r'dialect','recovery':r'core\.recovery|reaper',
 'repair_ep':r'net-mutual-debts|cap-debts|net_mutual|cap_debts'}.items()}
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if f.endswith('.py')]
res=collections.defaultdict(list)
for f in files:
    src=open(f,encoding='utf8').read(); t=ast.parse(src)
    doc=set()
    for n in ast.walk(t):
        if isinstance(n,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.body and isinstance(n.body[0],ast.Expr) and isinstance(n.body[0].value,ast.Constant) and isinstance(n.body[0].value.value,str):
            doc.add(id(n.body[0].value))
    for n in ast.walk(t):
        s=None
        if isinstance(n,ast.Name): s=n.id
        elif isinstance(n,ast.Attribute): s=n.attr
        elif isinstance(n,ast.alias): s=n.name
        elif isinstance(n,ast.ImportFrom): s=n.module or ''
        elif isinstance(n,ast.Constant) and isinstance(n.value,str) and id(n) not in doc and len(n.value)<200: s=n.value
        if s:
            for k,p in pats.items():
                if p.search(s): res[k].append((f,getattr(n,'lineno',0),s[:90]))
for k,v in res.items():
    fs=collections.Counter(x[0] for x in v)
    print(f'== {k}: {len(v)} code refs in {len(fs)} files')
    for f,c in fs.most_common(40): 
        ex=[x for x in v if x[0]==f][:2]
        print(f'   {c:3} {f}  e.g. :{ex[0][1]} {ex[0][2]!r}')
