import json,collections,sys,re
d=json.load(open(sys.argv[1]))
# no assertion at all
noas=[t for t in d if t['asserts']==0 and t['raises']==0 and t['helpers']==0]
print('tests w/o assert/raises/assert-helper:',len(noas))
# group by whether they call something that could assert: calls to module-local funcs
for t in noas[:80]:
    print(f"  {t['file']}:{t['line']} {t['name']} body={t['nbody']} calls={[c for c in t['calls'] if not c.startswith(('pytest','str','len','list','dict','set','int'))][:6]}")
