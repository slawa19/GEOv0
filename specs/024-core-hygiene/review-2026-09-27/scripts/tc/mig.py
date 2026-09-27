import os,re,glob,ast
os.environ.setdefault('ENV','test');os.environ.setdefault('ENVIRONMENT','test');os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://x:x@127.0.0.1:1/none')
from app.db.base import Base
import app.db.models  # noqa
meta_names={}
cols={}
for t in Base.metadata.sorted_tables:
    cols[t.name]=set(c.name for c in t.columns)
    for i in t.indexes: meta_names[i.name]=('index',t.name)
    for c in t.constraints:
        if c.name: meta_names[str(c.name)]=(type(c).__name__,t.name)
print('ORM tables',len(cols),'named idx/constraints',len(meta_names))
created={};dropped=set()
mig_tables=set();dropped_tables=set()
files=sorted(glob.glob('migrations/versions/*.py'))
for f in files:
    src=open(f,encoding='utf8').read()
    tree=ast.parse(src)
    up=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='upgrade']
    if not up: continue
    # include helper functions called from upgrade: simply scan whole module except downgrade
    body=[n for n in tree.body if not (isinstance(n,ast.FunctionDef) and n.name=='downgrade')]
    s=ast.unparse(ast.Module(body=body,type_ignores=[]))
    for m in re.finditer(r"op\.create_index\(\s*(?:op\.f\()?['\"](\w+)",s): created[m.group(1)]=f
    for m in re.finditer(r"op\.create_(?:check|unique|foreign_key|primary_key)(?:_constraint)?\(\s*(?:op\.f\()?['\"](\w+)",s): created[m.group(1)]=f
    for m in re.finditer(r"name=(?:op\.f\()?['\"](\w+)['\"]",s): created[m.group(1)]=f
    for m in re.finditer(r"(?:CREATE (?:UNIQUE )?INDEX(?: IF NOT EXISTS)?|ADD CONSTRAINT)\s+\"?(\w+)",s,re.I): created[m.group(1)]=f
    for m in re.finditer(r"op\.drop_(?:index|constraint)\(\s*(?:op\.f\()?['\"](\w+)",s): dropped.add(m.group(1))
    for m in re.finditer(r"DROP (?:INDEX|CONSTRAINT)(?: IF EXISTS)?\s+\"?(\w+)",s,re.I): dropped.add(m.group(1))
    for m in re.finditer(r"op\.create_table\(\s*['\"](\w+)",s): mig_tables.add(m.group(1))
    for m in re.finditer(r"op\.drop_table\(\s*['\"](\w+)",s): dropped_tables.add(m.group(1))
live={k:v for k,v in created.items() if k not in dropped}
print('migration-created names',len(created),'dropped',len(dropped),'live',len(live))
print('ORM names not created by any migration:')
for k,v in sorted(meta_names.items()):
    if k not in created: print('  ',k,v)
print('ORM names that migrations dropped (and not re-created after):')
for k,v in sorted(meta_names.items()):
    if k in dropped and k in created: print('  (created&dropped)',k,v)
print('Migration-live names absent from ORM:')
for k,v in sorted(live.items()):
    if k not in meta_names: print('  ',k,v)
print('tables: ORM-only',sorted(set(cols)-(mig_tables-dropped_tables)),' mig-only',sorted((mig_tables-dropped_tables)-set(cols)))
