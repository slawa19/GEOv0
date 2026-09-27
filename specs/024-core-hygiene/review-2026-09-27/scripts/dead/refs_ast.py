import ast, io, os, re, subprocess, sys, tokenize, json
from collections import defaultdict
ROOT = r"<repo>"; OUT=os.path.abspath(sys.argv[1]); os.chdir(ROOT)
files = subprocess.check_output(["git","ls-files"],text=True,encoding="utf-8").splitlines()
py=[f for f in files if f.endswith(".py")]
code_roots=("app/","tests/","scripts/","migrations/","seeds/","admin-fixtures/","fixtures/")
name_tok=defaultdict(lambda: defaultdict(int)); strcom=defaultdict(lambda: defaultdict(int))
word=re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
trees={}
for f in py:
    if not f.startswith(code_roots): continue
    src=open(f,encoding="utf-8").read()
    try: tree=ast.parse(src)
    except Exception as e: print("parsefail",f,e,file=sys.stderr); continue
    trees[f]=tree
    for n in ast.walk(tree):
        if isinstance(n,ast.Name):
            if not isinstance(n.ctx,ast.Store): name_tok[n.id][f]+=1
        elif isinstance(n,ast.Attribute): name_tok[n.attr][f]+=1
        elif isinstance(n,ast.alias):
            name_tok[n.name.split(".")[-1]][f]+=1
            for part in n.name.split("."): name_tok[part][f]+=0
        elif isinstance(n,ast.keyword) and n.arg: pass
        elif isinstance(n,ast.Constant) and isinstance(n.value,str):
            for w in word.findall(n.value): strcom[w][f]+=1
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type==tokenize.COMMENT:
            for w in word.findall(t.string): strcom[w][f]+=1
defs=[]
for f in py:
    if not f.startswith("app/"): continue
    tree=trees[f]
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): defs.append((n.name,"func",f,n.lineno,None,[ast.unparse(d)[:50] for d in n.decorator_list]))
        elif isinstance(n,ast.ClassDef):
            defs.append((n.name,"class",f,n.lineno,None,[]))
            for m in n.body:
                if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)) and not (m.name.startswith("__") and m.name.endswith("__")):
                    defs.append((m.name,"method",f,m.lineno,n.name,[ast.unparse(d)[:50] for d in m.decorator_list]))
        elif isinstance(n,(ast.Assign,ast.AnnAssign)):
            for t in (n.targets if isinstance(n,ast.Assign) else [n.target]):
                if isinstance(t,ast.Name) and t.id!="__all__": defs.append((t.id,"const",f,n.lineno,None,[]))
out=[]
for name,kind,f,line,owner,decos in defs:
    refs=dict(name_tok.get(name,{}))
    refs={k:v for k,v in refs.items() if v>0}
    init={k:v for k,v in refs.items() if k.endswith("__init__.py") and k.startswith("app/")}
    other={k:v for k,v in refs.items() if k not in init}
    app_refs={k:v for k,v in other.items() if not k.startswith("tests/")}
    test_refs={k:v for k,v in other.items() if k.startswith("tests/")}
    sc=dict(strcom.get(name,{}))
    if decos: cat="live-deco"
    elif app_refs: cat="live"
    elif test_refs: cat="b"
    elif init: cat="d"
    elif sc: cat="c"
    else: cat="a"
    out.append(dict(name=name,kind=kind,file=f,line=line,owner=owner,decos=decos,cat=cat,app=app_refs,tests=test_refs,init=init,strcom=sc))
json.dump(out,open(OUT,"w"),indent=1)
from collections import Counter
print(Counter(o["cat"] for o in out)); print(len(out),"defs")
for x in sorted(out,key=lambda x:(x['cat'],x['file'],x['line'])):
    if x['cat'].startswith('live'): continue
    print(x['cat'],x['kind'],f"{x['file']}:{x['line']}",(x['owner']+'.' if x['owner'] else '')+x['name'],'T='+','.join(list(x['tests'])[:2]) if x['tests'] else '','S='+','.join(list(x['strcom'])[:2]) if x['strcom'] else '')
