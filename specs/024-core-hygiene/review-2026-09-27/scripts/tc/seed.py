import ast,subprocess,collections,re
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if f.endswith('.py')]
helpers=[];inline=0;inline_files=set();eqp=[]
for f in files:
    t=ast.parse(open(f,encoding='utf8').read())
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
            calls=[ast.unparse(c.func) for c in ast.walk(n) if isinstance(c,ast.Call)]
            hp='Participant' in calls; ht='TrustLine' in calls; he='Equivalent' in calls
            if hp and ht and he:
                if n.name.startswith('test'): inline+=1; inline_files.add(f)
                else: helpers.append((f,n.lineno,n.name,n.end_lineno-n.lineno+1))
print('non-test helper functions that build Equivalent+Participant+TrustLine:',len(helpers),'in',len({h[0] for h in helpers}),'files; total lines',sum(h[3] for h in helpers))
print('test functions building all three inline:',inline,'in',len(inline_files),'files')
c=collections.Counter(h[2] for h in helpers)
print(c.most_common(15))
for h in helpers[:0]: print(h)
