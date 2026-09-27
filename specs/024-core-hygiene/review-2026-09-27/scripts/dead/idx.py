import ast,os,glob,re
os.chdir(r"<repo>")
created={}; dropped=set(); tables_c=set(); tables_d=set()
for f in sorted(glob.glob("migrations/versions/*.py")):
    t=ast.parse(open(f,encoding="utf-8").read())
    for fn in t.body:
        if not (isinstance(fn,ast.FunctionDef) and fn.name=="upgrade"): continue
        for n in ast.walk(fn):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=="op":
                a=n.args
                nm=a[0].value if a and isinstance(a[0],ast.Constant) else None
                if n.func.attr=="create_index" and nm: created[nm]=f; dropped.discard(nm)
                elif n.func.attr=="drop_index" and nm: dropped.add(nm); created.pop(nm,None)
                elif n.func.attr=="create_table" and nm: tables_c.add(nm); tables_d.discard(nm)
                elif n.func.attr=="drop_table" and nm: tables_d.add(nm); tables_c.discard(nm)
# ORM index names
src="\n".join(open(p,encoding="utf-8").read() for p in glob.glob("app/db/**/*.py",recursive=True))
orm_idx=set(re.findall(r'Index\(\s*["\']([a-z0-9_]+)',src))
orm_idx|=set(re.findall(r'name=["\'](ix_[a-z0-9_]+|uq_[a-z0-9_]+)',src))
orm_tables=set(re.findall(r'__tablename__\s*=\s*["\']([a-z_]+)',src))|set(re.findall(r'Table\(\s*["\']([a-z_]+)',src))
print("tables in migrations not in ORM:",sorted(tables_c-orm_tables))
print("tables in ORM not in migrations:",sorted(orm_tables-tables_c))
print("migration indexes not named in ORM:")
for k,v in sorted(created.items()):
    if k not in orm_idx: print("  ",k,v)
