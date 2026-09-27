import importlib.util, sys
sys.path.insert(0, r"<repo>")
from app.db import journal_triggers as jt
spec = importlib.util.spec_from_file_location("m029", r"<repo>\migrations\versions\029_debt_journal_by_the_database.py")
m = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(m)
except Exception as e:
    print("load err", e)
names = [n for n in dir(m) if n.isupper() or n.startswith("_") and n[1:].isupper()]
for n in dir(m):
    v = getattr(m, n)
    if isinstance(v, str) and "FUNCTION" in v:
        fn = v.split("FUNCTION ")[1].split("(")[0]
        same = jt.JOURNAL_FUNCTIONS.get(fn) == v
        print(n, fn, same)
    if isinstance(v, (tuple,list)):
        pass
print("seq same", m._CREATE_SEQUENCE == jt._CREATE_SEQUENCE)
