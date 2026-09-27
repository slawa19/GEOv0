import re,os,collections
lines=open('specs/017-postgres-only-engine/t1706-inventory.md',encoding='utf8').read().splitlines()
d=None;rows=[]
for l in lines:
    m=re.match(r'^### (tests/\S*)',l)
    if m: d=m.group(1); continue
    if d and len(l.split("|"))>=6 and l.startswith('| ') and '.py' in l.split('|')[1]:
        c=[x.strip().replace('*','') for x in l.split('|')]
        f=c[1]
        p=f if f.startswith('tests/') else d.rstrip('/')+'/'+f
        rows.append((p,c[2],c[3],c[4]))
print(len(rows))
cnt=collections.Counter()
for p,b,v,m in rows:
    ex=os.path.exists(p); cnt[(v,ex)]+=1
    if v!='keep': print(v,'EXISTS' if ex else 'GONE',b,p)
print(cnt)
import subprocess
tree=set(subprocess.check_output(['git','ls-files','tests'],text=True).split())
tests={t for t in tree if re.search(r'(^|/)test_[^/]*\.py$',t)}
inv={r[0] for r in rows}
new=sorted(tests-inv); print('NEW since inventory',len(new)); [print(' ',x) for x in new]
print('gone keep', [r[0] for r in rows if r[2]=='keep' and not os.path.exists(r[0])])
