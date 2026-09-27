"""Collect top-level/method/class-attr definitions in app/ and count references repo-wide.
Categories: a = no refs anywhere; b = refs only in tests/; c = refs only in strings/comments/docs;
d = only re-exported via __init__/__all__; live = has code refs outside tests."""
import ast, io, os, re, subprocess, sys, tokenize, json
from collections import defaultdict
ROOT = r"<repo>"
os.chdir(ROOT)
files = subprocess.check_output(["git","ls-files"],text=True).splitlines()
py = [f for f in files if f.endswith(".py")]
code_roots = ("app/","tests/","scripts/","migrations/","seeds/","admin-fixtures/","fixtures/")
text_ext = (".md",".yaml",".yml",".json",".ps1",".ts",".vue",".ini",".toml",".cfg",".txt")
name_tok = defaultdict(lambda: defaultdict(int))   # name -> file -> NAME token count
strcom = defaultdict(lambda: defaultdict(int))     # name -> file -> occurrences inside str/comment
word = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
for f in py:
    if not f.startswith(code_roots): continue
    try: src = open(f, encoding="utf-8").read()
    except Exception: continue
    try:
        for t in tokenize.generate_tokens(io.StringIO(src).readline):
            if t.type == tokenize.NAME: name_tok[t.string][f]+=1
            elif t.type in (tokenize.STRING, tokenize.COMMENT) or (hasattr(tokenize,"FSTRING_MIDDLE") and t.type==tokenize.FSTRING_MIDDLE):
                for w in word.findall(t.string): strcom[w][f]+=1
    except Exception as e: print("tokfail",f,e,file=sys.stderr)
docs_occ = defaultdict(set)
for f in files:
    if f.endswith(text_ext) and not f.startswith(("archive/","_audit")) and "node_modules" not in f:
        if f.startswith(("admin-ui/public","simulator-ui/v2/public")): continue
        try: s=open(f,encoding="utf-8",errors="ignore").read()
        except Exception: continue
        for w in set(word.findall(s)): docs_occ[w].add(f)
defs=[]  # (name, kind, file, line, owner)
exports=defaultdict(set)
for f in py:
    if not f.startswith("app/"): continue
    tree=ast.parse(open(f,encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): defs.append((n.name,"func",f,n.lineno,None))
        elif isinstance(n,ast.ClassDef):
            defs.append((n.name,"class",f,n.lineno,None))
            for m in n.body:
                if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)) and not (m.name.startswith("__") and m.name.endswith("__")):
                    decos=[ast.unparse(d) for d in m.decorator_list]
                    defs.append((m.name,"method",f,m.lineno,n.name+("|"+",".join(decos) if decos else "")))
        elif isinstance(n,(ast.Assign,ast.AnnAssign)):
            tg = n.targets if isinstance(n,ast.Assign) else [n.target]
            for t in tg:
                if isinstance(t,ast.Name):
                    if t.id=="__all__":
                        try:
                            for v in ast.literal_eval(n.value): exports[v].add(f)
                        except Exception: pass
                    else: defs.append((t.id,"const",f,n.lineno,None))
        elif isinstance(n,(ast.Import,ast.ImportFrom)) and f.endswith("__init__.py"):
            for a in n.names: exports[a.asname or a.name].add(f)
out=[]
for name,kind,f,line,owner in defs:
    if kind=="method" and name in ("model_config",): continue
    refs=dict(name_tok.get(name,{}))
    # remove the definition token itself
    refs[f]=refs.get(f,0)-1
    if refs[f]<=0: refs.pop(f)
    # remove re-export occurrences in __init__ (import lines) - approximate: __init__ files count separately
    init_refs={k:v for k,v in refs.items() if k.endswith("__init__.py") and k.startswith("app/")}
    other={k:v for k,v in refs.items() if k not in init_refs}
    app_refs={k:v for k,v in other.items() if not k.startswith("tests/")}
    test_refs={k:v for k,v in other.items() if k.startswith("tests/")}
    sc={k:v for k,v in strcom.get(name,{}).items()}
    dd=sorted(docs_occ.get(name,set()))
    if app_refs: cat="live"
    elif test_refs: cat="b"
    elif init_refs: cat="d"
    elif sc or dd: cat="c"
    else: cat="a"
    out.append(dict(name=name,kind=kind,file=f,line=line,owner=owner,cat=cat,app=app_refs,tests=test_refs,init=init_refs,strcom=sc,docs=dd[:6]))
json.dump(out,open(sys.argv[1],"w"),indent=1)
from collections import Counter
print(Counter((o["kind"],o["cat"]) for o in out))
