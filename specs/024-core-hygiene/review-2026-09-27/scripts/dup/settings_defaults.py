import re, subprocess, sys
sys.path.insert(0, ".")
from app.config import Settings
fields = Settings.model_fields
out = subprocess.run(["git","grep","-n","-o",r'getattr(settings, "[A-Z_0-9]*", [^)]*)',"app"],capture_output=True,text=True).stdout
for line in out.splitlines():
    m = re.search(r'getattr\(settings, "([A-Z_0-9]+)", (.*)\)$', line)
    if not m: continue
    name, dflt = m.group(1), m.group(2).strip()
    loc = line.split(":getattr")[0]
    if name not in fields:
        print("NOT-A-FIELD", loc, name, dflt); continue
    cd = fields[name].default
    try:
        v = eval(dflt)
    except Exception:
        v = dflt
    if v != cd:
        print("DIFF", loc, name, "getattr-default=", repr(v), "config-default=", repr(cd))
