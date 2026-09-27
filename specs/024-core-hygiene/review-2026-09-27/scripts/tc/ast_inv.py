import ast,subprocess,json,hashlib,re,collections,sys
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if f.endswith('.py')]
out=[]
def deco_names(n):
    r=[]
    for d in n.decorator_list:
        r.append(ast.unparse(d)[:120])
    return r
def norm(fn):
    # normalized body: strip docstring, rename identifiers? keep structure
    body=fn.body
    if body and isinstance(body[0],ast.Expr) and isinstance(getattr(body[0],'value',None),ast.Constant) and isinstance(body[0].value.value,str):
        body=body[1:]
    m=ast.Module(body=body,type_ignores=[])
    s=ast.dump(m,annotate_fields=False,include_attributes=False)
    return hashlib.sha1(s.encode()).hexdigest()[:12], len(body)
helpers_assert=re.compile(r'^(assert_|_assert|check_|_check|expect|_expect|verify|_verify|require)')
for f in files:
    src=open(f,encoding='utf8').read()
    try: t=ast.parse(src)
    except Exception as e: print('PARSEFAIL',f,e,file=sys.stderr); continue
    mod_marks=[]
    for n in t.body:
        if isinstance(n,ast.Assign) and any(getattr(x,'id',None)=='pytestmark' for x in n.targets):
            mod_marks.append(ast.unparse(n.value)[:200])
    # module-level helper function names
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test'):
            cls=None
            asserts=0; raises=0; helper_calls=0; skip=0; xfail=0; trivial=0; calls=set()
            for x in ast.walk(n):
                if isinstance(x,ast.Assert):
                    asserts+=1
                    tst=x.test
                    u=ast.unparse(tst)
                    if re.fullmatch(r'.+ is not None',u) or re.fullmatch(r'isinstance\(.*\)',u) or re.fullmatch(r'[\w\.\[\]\'"]+',u) or re.fullmatch(r'len\(.+\) > 0',u):
                        trivial+=1
                if isinstance(x,ast.Call):
                    fn=ast.unparse(x.func)
                    calls.add(fn)
                    last=fn.split('.')[-1]
                    if last in('raises','warns') : raises+=1
                    if helpers_assert.match(last) or last.startswith('assert'): helper_calls+=1
                    if fn in('pytest.skip',): skip+=1
                    if fn in('pytest.xfail',): xfail+=1
                    if fn in('pytest.fail',): helper_calls+=1
                if isinstance(x,ast.With) or isinstance(x,ast.AsyncWith):
                    for it in x.items:
                        u=ast.unparse(it.context_expr)
                        if 'raises' in u or 'RaisesGroup' in u: raises+=1
            h,nb=norm(n)
            out.append(dict(file=f,line=n.lineno,name=n.name,end=n.end_lineno,decos=deco_names(n),modmarks=mod_marks,asserts=asserts,raises=raises,helpers=helper_calls,skip=skip,xfail=xfail,trivial=trivial,hash=h,nbody=nb,calls=sorted(calls)[:60]))
json.dump(out,open(sys.argv[1],'w'))
print(len(out),'test functions in',len(files),'py files')
