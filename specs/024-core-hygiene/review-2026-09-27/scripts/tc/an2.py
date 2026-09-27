import json,collections,sys,re
d=json.load(open(sys.argv[1]))
tr=[t for t in d if t['asserts']>0 and t['trivial']==t['asserts'] and t['raises']==0 and t['helpers']==0]
print('only-trivial-assert tests:',len(tr))
for t in tr: print(f"  {t['file']}:{t['line']} {t['name']} asserts={t['asserts']}")
# skip / xfail
print('--- decorators skip/xfail')
c=collections.Counter()
for t in d:
    for x in t['decos']+t['modmarks']:
        for k in ('skip','skipif','xfail','slow','postgres','parametrize','asyncio','timeout','filterwarnings','usefixtures'):
            if re.search(r'mark\.'+k+r'\b',x): c[k]+=1
print(c)
for t in d:
    for x in t['decos']:
        if re.search(r'mark\.(skip|skipif|xfail)\b',x): print(f"  {t['file']}:{t['line']} {t['name']} :: {x[:150]}")
print('--- runtime skip/xfail inside tests')
for t in d:
    if t['skip'] or t['xfail']: print(f"  {t['file']}:{t['line']} {t['name']} skip={t['skip']} xfail={t['xfail']}")
print('--- module pytestmark')
mm=collections.Counter()
for f in {t['file']:t['modmarks'] for t in d}.items():
    for x in f[1]: mm[x[:120]]+=1
for k,v in mm.most_common(): print(v,k)
