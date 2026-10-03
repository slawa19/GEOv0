"""Per-test coverage analysis of the backend tier (programme 025, T2501). DEBUG PATH, NOT A GATE.

Usage: python scripts/test_asset/analyze.py <run_dir> [--out DIR] [--verdicts DIR] [--repo DIR]
  <run_dir>  output of scripts/test_asset/measure.ps1: `.coverage` (--cov=app --cov=tests --cov-branch
             --cov-context=test), `junit.xml`, `pytest.log`. Writes JSON + tables.md to --out
             (default <run_dir>/analysis) and prints summary.json.
  --verdicts a directory of zone verdict files (`zone-*.json`, format of
             specs/025-test-asset-consolidation/evidence-2026-09-28/verdicts/); adds crosscheck.json.

Promoted from specs/025-test-asset-consolidation/evidence-2026-09-28/{measure/analyze.py,
measure/report_tables.py, crosscheck.py} (dated evidence, kept unchanged). The algorithms are theirs.

EXIT CODES - a missing measurement is never reported as zero coverage (spec 025, P2-3):
  0  analysed;  2  usage;
  3  MISSING INPUT: `.coverage`, `junit.xml` or the verdict files do not exist;
  4  MISSING CONTEXT: the data has no per-test contexts, no `tests/` sentinel (so a test without a
     context cannot be told from a test that touched no app line), or a test that ran has no run
     context / a run context has no junit row. Outputs are still written; the names are in
     reconcile.json. A test is "zero app coverage" only when its run context exists and holds no
     app body line.

Line sets are "body lines" of app/: lines executed in a test's `run` phase, MINUS lines of
statements that are not inside a function body (module/class-level defs, imports, constants).
Those execute at import time; a module first imported lazily inside a test would otherwise
attribute its whole definition skeleton to that one test. Raw counts are kept too.

WHAT IT DOES NOT SEE (AGENTS.md section 12) - its silence is not evidence of any of these:
  * equal executed lines are not equal assertions: identical or subset sets prove nothing about
    what a test checks, its arrange, isolation or failure paths;
  * arcs are reduced to lines and parameter suffixes are stripped when verdicts are matched, so
    parameter identities, branches taken, SQL, database triggers and race schedules are invisible;
  * a Python subprocess is measured through pytest-cov's subprocess hook, but its lines carry no
    test context: they count as covered in app_files and never in a test's run set, so a test
    that drives app code through a child process looks like zero app coverage;
  * module- and session-scoped fixtures run in the setup phase of the FIRST test that requests
    them; setup lines are reported separately and never credited to a test's run set;
  * twin files are paired by the `_postgres` suffix only (the two files that still carry the
    former second dialect's suffix are not paired).
"""

import argparse
import ast
import collections
import glob
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import coverage

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("run_dir")
ap.add_argument("--out")
ap.add_argument("--verdicts")
ap.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
args = ap.parse_args()
run_dir, WT = args.run_dir, args.repo
out_dir = args.out or os.path.join(run_dir, "analysis")


def fail(code, msg):
    print(f"analyze.py: {'MISSING INPUT' if code == 3 else 'MISSING CONTEXT'}: {msg}", file=sys.stderr)
    sys.exit(code)


cov_path, junit_path = os.path.join(run_dir, ".coverage"), os.path.join(run_dir, "junit.xml")
for p in (cov_path, junit_path):
    if not os.path.isfile(p):
        fail(3, f"{p} does not exist - nothing was measured, this is not zero coverage")
zone_files = sorted(glob.glob(os.path.join(args.verdicts, "zone-*.json"))) if args.verdicts else []
if args.verdicts and not zone_files:
    fail(3, f"no zone-*.json verdict files in {args.verdicts}")
os.makedirs(out_dir, exist_ok=True)


def norm(p):
    p = p.replace("\\", "/")
    i = p.find("/app/")
    return p[i + 1:] if i >= 0 else p


def rel(p):
    return os.path.relpath(p, WT).replace("\\", "/")


# ---------- coverage data ----------
data = coverage.CoverageData(basename=cov_path)
data.read()
measured = sorted(data.measured_files())
contexts = data.measured_contexts()
if not any(c.endswith("|run") for c in contexts):
    fail(4, f"{cov_path} has no per-test run contexts - was it measured with --cov-context=test?")
if not any(rel(p).startswith("tests/") for p in measured):
    fail(4, f"{cov_path} has no tests/ sentinel - measure with --cov=tests (scripts/test_asset/measure.ps1)")
files = {p: p for p in measured if rel(p).startswith("app/")}


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
all_covered = collections.defaultdict(set)
for fid in files:
    il = import_lines[fid]
    for n, ctxs in data.contexts_by_lineno(fid).items():
        all_covered[fid].add(n)
        for cid in ctxs:
            raw_lines[cid].add((fid, n))
            if n not in il:
                body_lines[cid].add((fid, n))


def split_ctx(c):
    if "|" in c:
        return tuple(c.rsplit("|", 1))
    return c, ""


# ---------- junit (outcomes, total durations) ----------
outcome, jdur = {}, {}
for tc in ET.parse(junit_path).iter("testcase"):
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
for c in contexts:
    nid, ph = split_ctx(c)
    if ph == "run":
        tests.setdefault(nid, c)
    elif ph in ("setup", "teardown"):
        setup_td[nid][ph] |= body_lines[c]
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
        "has_run_context": cid is not None,
        "duration_total_s": jdur.get(nid),
        "duration_phases_s": phase_dur.get(nid, {}),
        "app_lines": len(s) if cid is not None else None,
        "app_lines_raw_incl_import_level": len(raw_lines[cid]) if cid is not None else None,
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
    "note": "run phase, body lines of app/; outcome passed/failed/error/xfailed (skipped excluded); "
            "only tests WITH a run context (a ran test without one is in reconcile.json, not here)",
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
    return re.sub(r"_postgres$", "", os.path.basename(path)[:-3])


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
            "(same basename after stripping _postgres, any directory). For each A: a smallest strict superset "
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

cov = coverage.Coverage(data_file=cov_path, config_file=False)
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
    body_stmts = [ln for ln in stmts if ln not in import_lines[fid]]
    app_files[norm(path)] = {
        "statements": len(stmts),
        "body_statements": len(body_stmts),
        "not_covered_by_anything": len(missing),
        "body_not_covered_by_anything": len([ln for ln in missing if ln not in import_lines[fid]]),
        "body_covered_only_outside_run_phase": len([ln for ln in body_stmts if ln in all_covered[fid] and ln not in owned_by_file[fid]]),
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
dump("by_test.json", by_test)
dump("zero_app_coverage.json", zero_obj)
dump("identical_sets.json", {"size_hist": dict(sorted(size_hist.items())), "clusters": clusters})
dump("subsets.json", subsets)
dump("unique_lines.json", unique_obj)
dump("by_test_file.json", btf)
dump("summary.json", summary)
dump("reconcile.json", {"run_contexts_not_in_junit": orphan, "ran_tests_without_run_context": missing_ctx})

# ---------- tables.md (was report_tables.py) ----------
d, b, s, u, idc = by_test, btf, summary, unique_obj, {"size_hist": dict(sorted(size_hist.items())), "clusters": clusters}
out = []
p = out.append
p("### Top-30 test files by time (sum of junit total per test)\n")
p("| # | file | tests | time, s | share | app lines | unique lines | zero-app tests |")
p("|---|---|---|---|---|---|---|---|")
for i, (f, v) in enumerate(list(b.items())[:30], 1):
    p(f"| {i} | `{f}` | {v['tests']} | {v['duration_s']:.1f} | {v['share_of_tier_time']*100:.2f}% | {v['app_lines']} | {v['unique_app_lines']} | {v['tests_with_zero_app_lines']} |")
p("\n### Identical-coverage cluster sizes\n")
p("| cluster size | clusters | tests |")
p("|---|---|---|")
for k, v in idc["size_hist"].items():
    p(f"| {k} | {v} | {int(k) * v} |")
ranc = {n for n in ran if d[n]["has_run_context"]}
hist = collections.Counter()
for n in ranc:
    x = d[n]["app_lines"]
    hist["0" if x == 0 else "1-50" if x <= 50 else "51-500" if x <= 500 else "501-2000" if x <= 2000 else ">2000"] += 1
p("\n### Distribution of per-test run-phase app body lines\n")
p("| app lines | tests |")
p("|---|---|")
for k in ["0", "1-50", "51-500", "501-2000", ">2000"]:
    p(f"| {k} | {hist[k]} |")
p("\n### App files with most body statements not covered by anything\n")
p("| app file | body statements | not covered |")
p("|---|---|---|")
for f, v in sorted(u["app_files"].items(), key=lambda kv: -kv[1]["body_not_covered_by_anything"])[:15]:
    p(f"| `{f}` | {v['body_statements']} | {v['body_not_covered_by_anything']} |")
open(os.path.join(out_dir, "tables.md"), "w", encoding="utf-8").write("\n".join(out) + "\n")
print(json.dumps(summary, indent=1))

if zone_files:
    # ---------- crosscheck of the zone verdicts (was crosscheck.py) ----------
    verdict_rows = [r for zf in zone_files for r in json.load(open(zf, encoding="utf-8"))]
    tests_all = list(by_test.keys())
    sets = {t: raw_lines[tests[t]] if t in tests else set() for t in tests_all}

    def func_of(nodeid):
        file, _, rest = nodeid.partition("::")
        name = rest.split("::")[-1]
        name = re.sub(r"\[.*\]$", "", name)
        return file, name

    file_verdict, func_verdict, func_meta = {}, {}, {}
    for row in verdict_rows:
        f = row["file"].replace("\\", "/")
        file_verdict[f] = row.get("verdict")
        for t in row.get("tests") or []:
            n = re.sub(r"\[.*\]$", "", str(t.get("name", "")).split("::")[-1])
            func_verdict[(f, n)] = t.get("verdict")
            func_meta[(f, n)] = t

    def verdict_of(nodeid):
        f, n = func_of(nodeid)
        v = func_verdict.get((f, n))
        if v:
            return v
        fv = file_verdict.get(f, "UNKNOWN")
        return "KEEP(unlisted-in-MIXED)" if fv == "MIXED" else fv

    v_of = {t: verdict_of(t) for t in tests_all}
    DELETE = {"DELETE-DUP", "DELETE-EMPTY", "DELETE-REMOVED"}
    summary = collections.OrderedDict()
    cnt = collections.Counter(v_of.values())
    dur, zero, not_ran = collections.Counter(), collections.Counter(), collections.Counter()
    for t in tests_all:
        dur[v_of[t]] += by_test[t].get("duration_total_s") or 0
        if t not in ranc:
            not_ran[v_of[t]] += 1
        elif by_test[t]["app_lines"] == 0:
            zero[v_of[t]] += 1
    summary["collected_tests_by_verdict"] = dict(cnt)
    summary["seconds_by_verdict"] = {k: round(v, 1) for k, v in dur.items()}
    summary["zero_app_coverage_by_verdict"] = dict(zero)
    summary["not_ran_or_no_context_by_verdict"] = dict(not_ran)
    live_files = {by_test[t]["test_file"] for t in tests_all}
    vfiles = {r["file"].replace("\\", "/") for r in verdict_rows}
    summary["verdict_test_files_not_collected_now"] = sorted(
        f for f in vfiles - live_files if os.path.basename(f).startswith("test_"))

    def app_file(fid):
        return norm(files[fid]).split("app/", 1)[-1]

    deleted = [t for t in tests_all if v_of[t] in DELETE]
    moved = [t for t in tests_all if v_of[t] == "MOVE-OUT"]
    survive = [t for t in tests_all if v_of[t] not in DELETE]
    surv_lines = set().union(*(sets[t] for t in survive)) if survive else set()
    del_lines = set().union(*(sets[t] for t in deleted)) if deleted else set()
    lost = del_lines - surv_lines
    summary["deleted_tests"] = len(deleted)
    summary["lines_lost_if_all_DELETE_applied"] = len(lost)
    summary["lines_lost_by_app_file"] = dict(collections.Counter(app_file(fid) for fid, _ in lost).most_common(25))
    culprits = collections.Counter({t: len(sets[t] & lost) for t in deleted if sets[t] & lost})
    summary["deleted_tests_touching_lost_lines"] = [
        {"test": t, "lost_lines_touched": k, "verdict": v_of[t]} for t, k in culprits.most_common(60)
    ]

    surv2 = [t for t in survive if v_of[t] != "MOVE-OUT"]
    s2 = set().union(*(sets[t] for t in surv2)) if surv2 else set()
    mv = set().union(*(sets[t] for t in moved)) if moved else set()
    lost_mv = mv - s2
    summary["moved_out_tests"] = len(moved)
    summary["extra_lines_lost_from_backend_tier_by_MOVE_OUT"] = len(lost_mv)
    summary["move_out_lost_by_app_file"] = dict(collections.Counter(app_file(fid) for fid, _ in lost_mv).most_common(15))
    summary["move_out_sole_coverers"] = [
        {"test": t, "lines": sorted(f"{app_file(fid)}:{n}" for fid, n in sets[t] & lost_mv)}
        for t in sorted(moved, key=lambda t: -len(sets[t] & lost_mv)) if sets[t] & lost_mv
    ]

    index = collections.defaultdict(list)
    for t in tests_all:
        index[func_of(t)[1]].append(t)
    dup_report = []
    for (f, n), meta in func_meta.items():
        if meta.get("verdict") != "DELETE-DUP":
            continue
        strong = str(meta.get("stronger") or "")
        mine = [t for t in index.get(n, []) if t.startswith(f)]
        sname = re.sub(r"\[.*\]$", "", strong.split("::")[-1]) if "::" in strong else ""
        sfile = strong.split("::")[0].split("/")[-1] if "::" in strong else ""
        cand = [t for t in index.get(sname, []) if (not sfile) or sfile in t]
        a = set().union(*(sets[t] for t in mine)) if mine else set()
        bb = set().union(*(sets[t] for t in cand)) if cand else set()
        diff = a - bb
        dup_report.append({
            "test": f"{f}::{n}", "stronger": strong, "own_found": len(mine), "stronger_found": len(cand),
            "own_lines": len(a), "stronger_lines": len(bb),
            "not_in_stronger": len(diff) if cand and mine else None,
            "not_in_stronger_by_file": dict(collections.Counter(app_file(fid) for fid, _ in diff).most_common(5))
            if cand and mine else None,
        })
    summary["delete_dup_checked"] = len(dup_report)
    summary["delete_dup_own_not_found"] = sum(1 for r in dup_report if not r["own_found"])
    summary["delete_dup_stronger_not_found"] = sum(1 for r in dup_report if not r["stronger_found"])
    summary["delete_dup_subset_ok"] = sum(1 for r in dup_report if r["not_in_stronger"] == 0)
    summary["delete_dup_not_subset"] = sum(1 for r in dup_report if r["not_in_stronger"])

    merge = [t for t in tests_all if v_of[t] == "MERGE" and t in ranc]
    summary["merge_tests_ran"] = len(merge)
    summary["merge_tests_with_unique_lines"] = sum(1 for t in merge if unique_obj["per_test"][t] > 0)
    summary["merge_unique_lines_total"] = sum(unique_obj["per_test"][t] for t in merge)
    kz = collections.Counter(func_of(t)[0] for t in ranc if v_of[t].startswith("KEEP") and by_test[t]["app_lines"] == 0)
    summary["keep_with_zero_app_coverage_top_files"] = dict(kz.most_common(25))
    summary["unknown_verdict_tests"] = cnt.get("UNKNOWN", 0)
    dump("crosscheck.json", {"summary": summary, "delete_dup": dup_report})
    print(json.dumps(summary, ensure_ascii=True, indent=1))

if orphan or missing_ctx:
    fail(4, f"{len(missing_ctx)} ran tests have no run context, {len(orphan)} run contexts have no junit row "
            f"- names in {os.path.join(out_dir, 'reconcile.json')}; they are NOT counted as zero coverage")
