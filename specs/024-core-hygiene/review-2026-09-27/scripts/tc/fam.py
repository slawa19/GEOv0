import json,re,collections,subprocess,sys
d=json.load(open(sys.argv[1]))
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if f.endswith('.py')]
fam=collections.defaultdict(lambda:[0,0,0])
tcount=collections.Counter(t['file'] for t in d)
for f in files:
    b=f.split('/')[-1]
    m=re.match(r'test_(p0\d\d|p1)_',b)
    k=m.group(1) if m else ('helper' if not b.startswith('test_') else 'unprefixed')
    n=sum(1 for _ in open(f,encoding='utf8'))
    fam[k][0]+=1;fam[k][1]+=n;fam[k][2]+=tcount[f]
for k,v in sorted(fam.items(),key=lambda x:-x[1][1]): print(f'{k:12} files={v[0]:4} lines={v[1]:6} tests={v[2]}')
# biggest files
sz=sorted(((sum(1 for _ in open(f,encoding='utf8')),f) for f in files),reverse=True)[:25]
for n,f in sz: print(n,tcount[f],f)
# docstring/comment share
tot=0;com=0;doc=0
import ast,io,tokenize
for f in files:
    src=open(f,encoding='utf8').read(); lines=src.count('\n')+1; tot+=lines
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type==tokenize.COMMENT: com+=1
    t=ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n,(ast.Module,ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.body and isinstance(n.body[0],ast.Expr) and isinstance(n.body[0].value,ast.Constant) and isinstance(n.body[0].value.value,str):
            e=n.body[0]; doc+=e.end_lineno-e.lineno+1
print('total',tot,'comment lines',com,'docstring lines',doc)
