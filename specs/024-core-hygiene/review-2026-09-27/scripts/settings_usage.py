import ast, re, subprocess, os
ROOT = r"<repo>"
src = open(os.path.join(ROOT, "app", "config.py"), encoding="utf-8").read()
tree = ast.parse(src)
fields = []
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and node.name == "Settings":
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.append(stmt.target.id)
def count(pattern, paths):
    out = subprocess.run(["git", "grep", "-n", "-E", pattern, "--"] + paths, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return [l for l in out.splitlines() if not l.startswith("app/config.py")]
print(f"{'field':45} app  tests  scripts+docs")
for f in fields:
    if f.startswith("_"):
        continue
    a = count(rf"\b{f}\b", ["app"])
    t = count(rf"\b{f}\b", ["tests"])
    s = count(rf"\b{f}\b", ["scripts", "docs", "docker", ".env.example", "docker-compose.yml", "README.md"])
    flag = "  <-- unread in app" if not a else ""
    print(f"{f:45} {len(a):3}  {len(t):5}  {len(s):5}{flag}")
    if a and len(a) <= 3:
        for l in a:
            print("      ", l[:150])
