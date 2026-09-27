import os, sys, json
sys.path.insert(0, r"<repo>")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:y@127.0.0.1:1/none")
import yaml
from app.main import app
gen = app.openapi()
canon = yaml.safe_load(open(r"<repo>\api\openapi.yaml", encoding="utf-8"))
paths = [("/api/v1/clearing/auto","post"),("/api/v1/integrity/status","get"),("/api/v1/admin/equivalents/{code}/integrity-hold/clear","post"),("/api/v1/admin/config","patch"),("/api/v1/admin/clearing/cycles","get"),("/api/v1/admin/liquidity/summary","get"),("/api/v1/admin/equivalents/{code}","delete"),("/health","get"),("/api/v1/admin/participants","get"),("/api/v1/integrity/verify","post")]
cp = canon["paths"]
def canon_key(p):
    q = p.replace("/api/v1","",1)
    return q if q in cp else p
for p, m in paths:
    g = gen["paths"].get(p, {}).get(m)
    c = cp.get(canon_key(p), {}).get(m)
    gs = sorted((g or {}).get("responses", {}).keys()); cs = sorted((c or {}).get("responses", {}).keys())
    gp = sorted(x["name"] for x in (g or {}).get("parameters", []) ); cpp = sorted(x.get("name", x.get("$ref","")) for x in (c or {}).get("parameters", []))
    print(p, m, "gen" if g else "NOGEN", "canon" if c else "NOCANON", "resp gen", gs, "canon", cs, "| params gen", gp, "canon", cpp)
