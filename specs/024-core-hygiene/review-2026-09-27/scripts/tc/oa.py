import json,yaml,sys
S=sys.argv[1]
gen=json.load(open(S+r'\gen_openapi.json'))
can=yaml.safe_load(open('api/openapi.yaml',encoding='utf8'))
def res(doc,s,depth=0):
    while isinstance(s,dict) and '$ref' in s:
        p=s['$ref'].split('/')[1:]; x=doc
        for k in p: x=x[k]
        s=x
    return s
def props(doc,s,depth=0):
    s=res(doc,s)
    if not isinstance(s,dict) or depth>2: return {}
    out={}
    for comb in ('allOf',):
        for sub in s.get(comb,[]): out.update(props(doc,sub,depth))
    for k,v in (s.get('properties') or {}).items():
        v=res(doc,v)
        out[k]=v
    return out
cp=set(can['paths']); gp={p.removeprefix('/api/v1') for p in gen['paths'] if p.startswith('/api/v1/')}
print('canon-only paths',sorted(cp-gp)); print('gen-only paths',sorted(gp-cp))
print('non-/api/v1 generated paths',sorted(p for p in gen['paths'] if not p.startswith('/api/v1/')))
n=0;diffs=[]
for p in sorted(cp&gp):
    for m,op in can['paths'][p].items():
        if m not in ('get','post','put','patch','delete'): continue
        gop=gen['paths']['/api/v1'+p].get(m)
        if not gop: continue
        for code in ('200','201'):
            c=(op.get('responses',{}).get(code) or {}); g=(gop.get('responses',{}).get(code) or {})
            c=res(can,c); g=res(gen,g)
            cs=((c.get('content') or {}).get('application/json') or {}).get('schema')
            gs=((g.get('content') or {}).get('application/json') or {}).get('schema')
            if cs is None and gs is None: continue
            n+=1
            cpp=props(can,cs) if cs else {}; gpp=props(gen,gs) if gs else {}
            cr=set((res(can,cs) or {}).get('required',[]) if cs else []); gr=set((res(gen,gs) or {}).get('required',[]) if gs else [])
            if set(cpp)!=set(gpp) or cr!=gr:
                diffs.append((m.upper(),p,code,sorted(set(cpp)-set(gpp)),sorted(set(gpp)-set(cpp)),sorted(cr-gr),sorted(gr-cr)))
print('compared success responses',n,'with top-level field/required diffs',len(diffs))
for d in diffs: print(' ',d)
