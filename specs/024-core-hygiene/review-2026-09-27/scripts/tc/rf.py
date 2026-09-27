import ast,subprocess,importlib,os
os.environ.setdefault('ENV','test');os.environ.setdefault('ENVIRONMENT','test');os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://x:x@127.0.0.1:1/none')
from app.config import settings
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if f.endswith('.py')]
miss=[];ok=0;other=[]
for f in files:
    t=ast.parse(open(f,encoding='utf8').read())
    imports={}
    for n in ast.walk(t):
        if isinstance(n,ast.ImportFrom) and n.module:
            for a in n.names: imports[a.asname or a.name]=(n.module,a.name)
        if isinstance(n,ast.Import):
            for a in n.names: imports[a.asname or a.name.split('.')[0]]=(a.name,None)
    for n in ast.walk(t):
        if isinstance(n,ast.Call) and ast.unparse(n.func).endswith('setattr') and any(k.arg=='raising' and isinstance(k.value,ast.Constant) and k.value.value is False for k in n.keywords):
            a=n.args
            target=None
            try:
                if len(a)>=2 and isinstance(a[1],ast.Constant):
                    objsrc=ast.unparse(a[0]); name=a[1].value
                    root=objsrc.split('.')[0]
                    if root in imports:
                        mod,attr=imports[root]
                        base=importlib.import_module(mod)
                        obj=getattr(base,attr) if attr else base
                        for part in objsrc.split('.')[1:]: obj=getattr(obj,part)
                        target=(obj,name)
                elif len(a)>=1 and isinstance(a[0],ast.Constant):
                    path=a[0].value; mod,name=path.rsplit('.',1)
                    target=(importlib.import_module(mod),name)
            except Exception as e:
                other.append((f,n.lineno,ast.unparse(n)[:100],repr(e)[:60])); continue
            if target is None: other.append((f,n.lineno,ast.unparse(n)[:100],'unresolved')); continue
            if hasattr(target[0],target[1]): ok+=1
            else: miss.append((f,n.lineno,ast.unparse(n)[:120]))
print('ok',ok,'MISSING',len(miss),'unresolved',len(other))
for m in miss: print('  MISSING',m)
for m in other: print('  ?',m)
