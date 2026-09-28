"""Per-test coverage analysis for the GEOv0 test-asset review (programme 025 measurement, DEBUG PATH).

Usage: python analyze.py <run_dir> [--out <dir>] [--copy-to <dir>] [--wt <worktree>]
  <run_dir> holds `.coverage` (pytest-cov, --cov-branch --cov-context=test), `junit.xml`, `pytest.log`.

Line sets are "body lines" of app/: lines executed in a test's `run` phase, MINUS lines of
statements that are not inside a function body (module/class-level defs, imports, constants).
Those execute at import time; a module first imported lazily inside a test would otherwise
attribute its whole definition skeleton to that one test. Raw counts are kept too.
"""
import ast
import collections
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import xml.etree.ElementTree as ET

import coverage
from coverage.numbits import numbits_to_nums

run_dir = sys.argv[1]
out_dir = run_dir
copy_to = None
WT = r"<snapshot>"
a = sys.argv[2:]
while a:
    k = a.pop(0)
    if k == "--out":
        out_dir = a.pop(0)
    elif k == "--copy-to":
        copy_to = a.pop(0)
    elif k == "--wt":
        WT = a.pop(0)
os.makedirs(out_dir, exist_ok=True)


def norm(p):
    p = p.replace("\\", "/")
    i = p.find("/app/")
    return p[i + 1:] if i >= 0 else p


# ---------- coverage db ----------
db = sqlite3.connect(os.path.join(run_dir, ".coverage"))
files = {fid: path for fid, path in db.execute("select id, path from file")}
contexts = {cid: c for cid, c in db.execute("select id, context from context")}


def import_level_lines(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return set()
    out = set()

    def walk(node, in_func):
        for ch in ast.iter_child_nodes(node):
            is_func = isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(ch, ast.stmt) and not in_func:
                start = min([ch.lineno] + [d.lineno for d in getattr(ch, "decorator_list", [])])
                if is_func or isinstance(ch, ast.ClassDef):
                    end = ch.body[0].lineno - 1 if ch.body else ch.end_lineno
                    out.update(range(start, max(start, end) + 1))
                elif isinstance(ch, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
                    out.add(start)
                else:
                    out.update(range(start, (ch.end_lineno or start) + 1))
            walk(ch, in_func or is_func)

    walk(tree, False)
    return out


import_lines = {fid: import_level_lines(p) for fid, p in files.items()}

raw_lines = collections.defaultdict(set)
body_lines = collections.defaultdict(set)
body_arcs = collections.Counter()
all_covered = collections.defaultdict(set)
for fid, cid, f, t in db.execute("select file_id, context_id, fromno, tono from arc"):
    il = import_lines[fid]
    body_arc = False
    for n in (f, t):
        if n > 0:
            raw_lines[cid].add((fid, n))
            all_covered[fid].add(n)
            if n not in il:
                body_lines[cid].add((fid, n))
                body_arc = True
    if body_arc:
        body_arcs[cid] += 1
for fid, cid, nb in db.execute("select file_id, context_id, numbits from line_bits"):
    for n in numbits_to_nums(nb):
        raw_lines[cid].add((fid, n))
        all_covered[fid].add(n)
        if n not in import_lines[fid]:
            body_lines[cid].add((fid, n))


def split_ctx(c):
    if "|" in c:
        return tuple(c.rsplit("|", 1))
    return c, ""


# ---------- junit (outcomes, total durations) ----------
outcome, jdur = {}, {}
for tc in ET.parse(os.path.join(run_dir, "junit.xml")).iter("testcase"):
    cls = tc.get("classname", "")
    name = tc.get("name", "")
    parts = cls.split(".")
    nid = None
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i]) + ".py"
        if os.path.exists(os.path.join(WT, cand)):
            nid = cand + "".join("::" + p for p in parts[i:]) + "::" + name
            break
    if nid is None:
        nid = cls + "::" + name
    st = "passed"
    for ch in tc:
        if ch.tag == "skipped":
            st = "xfailed" if ch.get("type") == "pytest.xfail" else "skipped"
        elif ch.tag == "failure":
            st = "failed"
        elif ch.tag == "error":
            st = "error" if st == "passed" else st + "+error"
    outcome[nid] = st
    jdur[nid] = float(tc.get("time", "0"))

# ---------- --durations phases from pytest.log ----------
phase_dur = collections.defaultdict(dict)
logp = os.path.join(run_dir, "pytest.log")
if os.path.exists(logp):
    raw = open(logp, "rb").read()
    txt = None
    for enc in ("utf-16", "utf-8"):
        try:
            txt = raw.decode(enc)
            if "durations" in txt or " call " in txt:
                break
        except Exception:
            continue
    for m in re.finditer(r"^([\d.]+)s (setup|call|teardown)\s+(\S.*?)\s*$", txt or "", re.M):
        phase_dur[m.group(3)][m.group(2)] = float(m.group(1))

# ---------- assemble per test ----------
tests = {}
setup_td = collections.defaultdict(lambda: {"setup": set(), "teardown": set()})
for cid, c in contexts.items():
    nid, ph = split_ctx(c)
    if ph == "run":
        tests.setdefault(nid, cid)
    elif ph in ("setup", "teardown"):
        setup_td[nid][ph] |= body_lines[cid]
all_ids = set(outcome) | set(tests)


def sha(s):
    items = sorted((norm(files[f]), n) for f, n in s)
    return hashlib.sha1("\n".join(f"{p}:{n}" for p, n in items).encode()).hexdigest()


by_test, test_sets = {}, {}
for nid in sorted(all_ids):
    cid = tests.get(nid)
    s = body_lines[cid] if cid is not None else set()
    test_sets[nid] = s
    per_file = collections.Counter(norm(files[f]) for f, _ in s)
    by_test[nid] = {
        "test_file": nid.split("::")[0],
        "outcome": outcome.get(nid, "unknown(no junit row)"),
        "duration_total_s": jdur.get(nid),
        "duration_phases_s": phase_dur.get(nid, {}),
        "app_lines": len(s),
        "app_lines_raw_incl_import_level": len(raw_lines[cid]) if cid is not None else 0,
        "app_arcs": body_arcs[cid] if cid is not None else 0,
        "lines_sha1": sha(s) if s else None,
        "app_files": dict(sorted(per_file.items())),
        "setup_app_lines": len(setup_td[nid]["setup"]),
        "teardown_app_lines": len(setup_td[nid]["teardown"]),
    }


def dump(name, obj):
    p = os.path.join(out_dir, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
    return p


RAN = ("passed", "failed", "error", "xfailed")  # xfailed tests execute app code too
ran = [n for n in by_test if by_test[n]["outcome"] in RAN]
zero = sorted(n for n in ran if by_test[n]["app_lines"] == 0)
zero_set = set(zero)
zero_obj = {
    "note": "run phase, body lines of app/; outcome passed/failed/error/xfailed (skipped excluded)",
    "count": len(zero),
    "by_file": collections.Counter(by_test[n]["test_file"] for n in zero).most_common(),
    "tests": zero,
}

groups = collections.defaultdict(list)
for n in ran:
    if by_test[n]["lines_sha1"]:
        groups[by_test[n]["lines_sha1"]].append(n)
clusters = [
    {"sha1": h, "size": len(v), "app_lines": by_test[v[0]]["app_lines"],
     "test_files": sorted({by_test[x]["test_file"] for x in v}),
     "cross_file": len({by_test[x]["test_file"] for x in v}) > 1, "tests": sorted(v)}
    for h, v in groups.items() if len(v) >= 2
]
clusters.sort(key=lambda c: (-c["size"], -c["app_lines"]))
size_hist = collections.Counter(c["size"] for c in clusters)


def twin_key(path):
    return re.sub(r"_(postgres|sqlite)$", "", os.path.basename(path)[:-3])


tfile_tests = collections.defaultdict(list)
for n in ran:
    tfile_tests[by_test[n]["test_file"]].append(n)
twins = collections.defaultdict(set)
for tf in tfile_tests:
    twins[twin_key(tf)].add(tf)
twin_groups = {k: sorted(v) for k, v in twins.items() if len(v) >= 2}

subset_rows = {}


def scan(pool, scope):
    pool = sorted((n for n in pool if test_sets[n]), key=lambda n: len(test_sets[n]))
    for i, A in enumerate(pool):
        sa = test_sets[A]
        for B in pool[i + 1:]:
            sb = test_sets[B]
            if len(sb) > len(sa) and sa < sb:
                cur = subset_rows.get(A)
                if cur is None or len(sb) < cur["superset_app_lines"]:
                    subset_rows[A] = {"test": A, "app_lines": len(sa), "smallest_superset": B,
                                      "superset_app_lines": len(sb),
                                      "extra_lines_in_superset": len(sb) - len(sa), "scope": scope}
                break


for tf, ns in tfile_tests.items():
    scan(list(ns), "same_file")
for k, tfs in twin_groups.items():
    scan([n for tf in tfs for n in tfile_tests[tf]], "twin_files:" + k)
subsets = {
    "note": "A strict-subset-of B on run-phase app body lines; scanned within one test file and within twin files "
            "(same basename after stripping _postgres/_sqlite, any directory). For each A: a smallest strict superset "
            "(ties broken by scan order). Tests with identical sets are in identical_sets.json, not here.",
    "twin_groups": twin_groups, "count": len(subset_rows),
    "pairs": sorted(subset_rows.values(), key=lambda r: r["test"]),
}

line_owners = collections.defaultdict(list)
for n in ran:
    for x in test_sets[n]:
        line_owners[x].append(n)
uniq = collections.Counter()
file_owner_sets = collections.defaultdict(set)
for x, owners in line_owners.items():
    if len(owners) == 1:
        uniq[owners[0]] += 1
    tfs = {by_test[o]["test_file"] for o in owners}
    if len(tfs) == 1:
        file_owner_sets[next(iter(tfs))].add(x)

cov = coverage.Coverage(data_file=os.path.join(run_dir, ".coverage"), config_file=False)
cov.load()
owned_by_file = collections.defaultdict(set)
for (fid, n) in line_owners:
    owned_by_file[fid].add(n)
app_files = {}
for fid, path in files.items():
    try:
        _, stmts, _, missing, _ = cov.analysis2(path)
    except Exception:
        continue
    body_stmts = [l for l in stmts if l not in import_lines[fid]]
    app_files[norm(path)] = {
        "statements": len(stmts),
        "body_statements": len(body_stmts),
        "not_covered_by_anything": len(missing),
        "body_not_covered_by_anything": len([l for l in missing if l not in import_lines[fid]]),
        "body_covered_only_outside_run_phase": len([l for l in body_stmts if l in all_covered[fid] and l not in owned_by_file[fid]]),
    }
unique_obj = {
    "note": "run phase, body lines of app/. per_test: lines covered by exactly one test. per_test_file: lines covered "
            "only by tests of that file. app_files.not_covered_by_anything: statements missed by every context incl. "
            "import time and fixtures (coverage analysis2); body_covered_only_outside_run_phase: executed only at "
            "import/setup/teardown, by no test's run phase. Only files imported during the run are listed.",
    "per_test": {n: uniq.get(n, 0) for n in sorted(ran)},
    "per_test_file": {tf: len(file_owner_sets.get(tf, ())) for tf in sorted(tfile_tests)},
    "app_files": dict(sorted(app_files.items())),
}

tier_time = sum((by_test[n]["duration_total_s"] or 0) for n in by_test)
all_tf = collections.defaultdict(list)
for n in by_test:
    all_tf[by_test[n]["test_file"]].append(n)
btf = {}
for tf, ns in all_tf.items():
    dur = sum((by_test[n]["duration_total_s"] or 0) for n in ns)
    lines = set().union(*(test_sets[n] for n in ns))
    btf[tf] = {
        "tests": len(ns),
        "outcomes": dict(collections.Counter(by_test[n]["outcome"] for n in ns)),
        "duration_s": round(dur, 3),
        "share_of_tier_time": round(dur / tier_time, 5) if tier_time else None,
        "app_lines": len(lines),
        "unique_app_lines": len(file_owner_sets.get(tf, ())),
        "tests_with_zero_app_lines": sum(1 for n in ns if n in zero_set),
        "twin_group": twin_key(tf) if twin_key(tf) in twin_groups else None,
    }
btf = dict(sorted(btf.items(), key=lambda kv: -kv[1]["duration_s"]))

orphan = sorted(set(tests) - set(outcome))
missing_ctx = sorted(n for n in ran if n not in tests)
summary = {
    "junit_tests": len(outcome),
    "outcomes": dict(collections.Counter(outcome.values())),
    "run_contexts": len(tests),
    "ran_tests": len(ran),
    "tier_time_s_sum_junit": round(tier_time, 1),
    "zero_app_tests": len(zero),
    "identical_clusters": len(clusters),
    "tests_in_identical_clusters": sum(c["size"] for c in clusters),
    "cross_file_clusters": sum(1 for c in clusters if c["cross_file"]),
    "cluster_size_hist": dict(sorted(size_hist.items())),
    "subset_tests": len(subset_rows),
    "subset_tests_by_scope": dict(collections.Counter(r["scope"].split(":")[0] for r in subset_rows.values())),
    "twin_groups": len(twin_groups),
    "test_files": len(btf),
    "test_files_zero_unique": sum(1 for v in btf.values() if v["unique_app_lines"] == 0),
    "tests_zero_unique": sum(1 for n in ran if uniq.get(n, 0) == 0),
    "app_files_measured": len(files),
    "app_body_statements": sum(v["body_statements"] for v in app_files.values()),
    "app_body_not_covered": sum(v["body_not_covered_by_anything"] for v in app_files.values()),
    "run_contexts_not_in_junit": len(orphan),
    "ran_tests_without_run_context": len(missing_ctx),
}
paths = [
    dump("by_test.json", by_test),
    dump("zero_app_coverage.json", zero_obj),
    dump("identical_sets.json", {"size_hist": dict(sorted(size_hist.items())), "clusters": clusters}),
    dump("subsets.json", subsets),
    dump("unique_lines.json", unique_obj),
    dump("by_test_file.json", btf),
    dump("summary.json", summary),
    dump("reconcile.json", {"run_contexts_not_in_junit": orphan, "ran_tests_without_run_context": missing_ctx}),
]
print(json.dumps(summary, indent=1))
if copy_to:
    os.makedirs(copy_to, exist_ok=True)
    for p in paths:
        shutil.copy(p, copy_to)
