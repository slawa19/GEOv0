import ast,subprocess,re,collections
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if f.endswith('.py')]
defs=[];uses=collections.Counter();usefix=collections.Counter()
srcs={}
for f in files:
    src=open(f,encoding='utf8').read(); srcs[f]=src; t=ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
            isfix=any('fixture' in ast.unparse(d) for d in n.decorator_list)
            auto=any('autouse=True' in ast.unparse(d) for d in n.decorator_list)
            nm=n.name
            for d in n.decorator_list:
                m=re.search(r"name=['\"](\w+)",ast.unparse(d))
                if m: nm=m.group(1)
            if isfix: defs.append((f,n.lineno,nm,auto))
            for a in n.args.args+n.args.kwonlyargs: uses[a.arg]+=1
        if isinstance(n,ast.Call) and ast.unparse(n.func).endswith('usefixtures'):
            for a in n.args:
                if isinstance(a,ast.Constant): uses[a.value]+=1
print('fixture defs',len(defs),'autouse',sum(1 for d in defs if d[3]))
# names reused via assignment e.g. pg_client = make_pg_client_fixture()
unused=[d for d in defs if not d[3] and uses[d[2]]<= (1 if False else 0)]
# a fixture's own def args don't count its name; name uses in other functions' args
for d in defs:
    if d[3]: continue
    if uses[d[2]]==0: print('UNUSED',f'{d[0]}:{d[1]}',d[2])
dn=collections.defaultdict(list)
for d in defs: dn[d[2]].append(f'{d[0]}:{d[1]}')
print('--- fixture names defined in >2 places')
for k,v in sorted(dn.items(),key=lambda x:-len(x[1])):
    if len(v)>2: print(len(v),k,v[:6])
