import subprocess,re,os,ast,difflib,collections
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if re.search(r'/test_[^/]*\.py$',f)]
base=collections.defaultdict(list)
for f in files:
    b=os.path.basename(f)[:-3]
    k=re.sub(r'_(postgres|sqlite)$','',b)
    base[k].append(f)
def fnsrc(f):
    t=ast.parse(open(f,encoding='utf8').read()); out={}
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test'):
            b=n.body
            if b and isinstance(b[0],ast.Expr) and isinstance(getattr(b[0],'value',None),ast.Constant): b=b[1:]
            out[n.name]=ast.unparse(ast.Module(body=b,type_ignores=[]))
    return out
for k,v in base.items():
    if len(v)>1:
        a,b=v[0],v[1]
        sa,sb=fnsrc(a),fnsrc(b)
        la=sum(1 for _ in open(a,encoding='utf8')); lb=sum(1 for _ in open(b,encoding='utf8'))
        common=set(sa)&set(sb)
        print(f'{k}: {a}({la}l,{len(sa)}t) <> {b}({lb}l,{len(sb)}t) same-names={len(common)}')
        # best-match similarity
        for n,s in sa.items():
            best=max(((difflib.SequenceMatcher(None,s,s2).ratio(),n2) for n2,s2 in sb.items()),default=(0,None))
            if best[0]>0.6: print(f'    {best[0]:.2f} {n} ~ {best[1]}')
