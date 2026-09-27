import json,collections,sys
d=json.load(open(sys.argv[1]))
byh=collections.defaultdict(list)
for t in d:
    if t['nbody']>=2: byh[t['hash']].append(t)
g=[v for v in byh.values() if len({x['file'] for x in v})>1 or len(v)>1]
print('identical-body groups:',len(g),'tests involved',sum(len(v) for v in g))
for v in sorted(g,key=lambda v:-(v[0]['end']-v[0]['line'])):
    print(f"  len={v[0]['end']-v[0]['line']+1}", [f"{x['file']}:{x['line']} {x['name']}" for x in v])
byn=collections.defaultdict(set)
for t in d: byn[t['name']].add(t['file'])
dn={k:v for k,v in byn.items() if len(v)>1}
print('same test name in >1 file:',len(dn))
for k,v in sorted(dn.items()): print('  ',k,sorted(v))
