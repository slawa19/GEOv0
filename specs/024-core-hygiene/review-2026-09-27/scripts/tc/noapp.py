import ast,subprocess,re,json,sys,collections
d=json.load(open(sys.argv[1])); tc=collections.Counter(t['file'] for t in d)
files=[f for f in subprocess.check_output(['git','ls-files','tests'],text=True).split() if re.search(r'/test_[^/]*\.py$',f)]
rows=[]
for f in files:
    src=open(f,encoding='utf8').read(); t=ast.parse(src)
    mods=set()
    for n in ast.walk(t):
        if isinstance(n,ast.Import): mods|={a.name for a in n.names}
        if isinstance(n,ast.ImportFrom) and n.module: mods.add(n.module)
    app=any(m=='app' or m.startswith('app.') for m in mods)
    fix=bool(re.search(r'\b(db_session|client|committed_database|committed_session)\b',src))
    reads=len(re.findall(r'read_text\(|\.ps1|\.yml|\.md\b|openapi\.yaml',src))
    rows.append((f,app,fix,reads,src.count('\n')))
noapp=[r for r in rows if not r[1]]
print('test files not importing app.*:',len(noapp),'lines',sum(r[4] for r in noapp),'tests',sum(tc[r[0]] for r in noapp))
for r in sorted(noapp,key=lambda r:-r[4]): print(f'  {r[4]:5} t={tc[r[0]]:3} fix={int(r[2])} {r[0]}')
