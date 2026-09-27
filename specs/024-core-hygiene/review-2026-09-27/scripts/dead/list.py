import json,ast,sys
o=json.load(open(sys.argv[1]))
cache={}
def deco(f,line):
    if f not in cache: cache[f]=ast.parse(open('<repo>/'+f,encoding='utf-8').read())
    for n in ast.walk(cache[f]):
        if getattr(n,'lineno',None)==line and isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            return [ast.unparse(d)[:40] for d in n.decorator_list]
    return []
for x in sorted(o,key=lambda x:(x['cat'],x['file'],x['line'])):
    if x['cat']=='live': continue
    d=deco(x['file'],x['line'])
    print(x['cat'],x['kind'],f"{x['file']}:{x['line']}",x['name'],x['owner'] or '', 'DECO='+str(d) if d else '', 'T='+str(list(x['tests'])[:3]) if x['tests'] else '', 'S='+str(list(x['strcom'])[:3]) if x['strcom'] else '', 'D='+str(x['docs'][:3]) if x['docs'] else '')
