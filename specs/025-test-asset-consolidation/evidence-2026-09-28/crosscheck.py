"""Cross-check the zone verdicts against the measured per-test coverage.

Inputs: all_verdicts.json (merged zone verdicts), measure/*.json, the coverage sqlite database.
Output: crosscheck.json + a printed summary. Read-only with respect to the repository.
"""
import collections
import json
import pathlib
import re
import sqlite3
import sys

S = pathlib.Path(sys.argv[1])
COV = sys.argv[2]
M = S / "measure"

verdict_rows = json.loads((S / "all_verdicts.json").read_text(encoding="utf-8"))
by_test = json.loads((M / "by_test.json").read_text(encoding="utf-8"))
unique = json.loads((M / "unique_lines.json").read_text(encoding="utf-8"))["per_test"]


def func_of(nodeid: str) -> tuple[str, str]:
    file, _, rest = nodeid.partition("::")
    name = rest.split("::")[-1]
    name = re.sub(r"\[.*\]$", "", name)
    return file, name


# verdict per (file, function)
file_verdict = {}
func_verdict = {}
func_meta = {}
for row in verdict_rows:
    f = row["file"].replace("\\", "/")
    file_verdict[f] = row.get("verdict")
    for t in row.get("tests") or []:
        n = re.sub(r"\[.*\]$", "", str(t.get("name", "")).split("::")[-1])
        func_verdict[(f, n)] = t.get("verdict")
        func_meta[(f, n)] = t


def verdict_of(nodeid: str) -> str:
    f, n = func_of(nodeid)
    v = func_verdict.get((f, n))
    if v:
        return v
    fv = file_verdict.get(f, "UNKNOWN")
    return "KEEP(unlisted-in-MIXED)" if fv == "MIXED" else fv


# line sets per run context
con = sqlite3.connect(COV)
ctx = {cid: c[: -len("|run")] for cid, c in con.execute("select id, context from context") if c.endswith("|run")}
files = dict(con.execute("select id, path from file"))
sets = collections.defaultdict(set)
for fid, cid, a, b in con.execute("select file_id, context_id, fromno, tono from arc"):
    n = ctx.get(cid)
    if n is None:
        continue
    if a > 0:
        sets[n].add((fid, a))
    if b > 0:
        sets[n].add((fid, b))

tests = list(by_test.keys())
v_of = {t: verdict_of(t) for t in tests}
DELETE = {"DELETE-DUP", "DELETE-EMPTY", "DELETE-REMOVED"}


def uniq(t):
    u = unique.get(t)
    if isinstance(u, dict):
        return int(u.get("unique_lines", u.get("count", 0)) or 0)
    return int(u or 0)


summary = collections.OrderedDict()
cnt = collections.Counter(v_of.values())
dur = collections.Counter()
zero = collections.Counter()
for t in tests:
    dur[v_of[t]] += by_test[t].get("duration_total_s") or 0
    if not by_test[t].get("app_lines"):
        zero[v_of[t]] += 1
summary["collected_tests_by_verdict"] = dict(cnt)
summary["seconds_by_verdict"] = {k: round(v, 1) for k, v in dur.items()}
summary["zero_app_coverage_by_verdict"] = dict(zero)

# 1. true coverage loss of the deletions: lines covered by DELETE-* tests and by no surviving test
deleted = [t for t in tests if v_of[t] in DELETE]
moved = [t for t in tests if v_of[t] == "MOVE-OUT"]
survive = [t for t in tests if v_of[t] not in DELETE]
surv_lines = set().union(*(sets[t] for t in survive)) if survive else set()
del_lines = set().union(*(sets[t] for t in deleted)) if deleted else set()
lost = del_lines - surv_lines
lost_by_file = collections.Counter(files[fid].replace("\\", "/").split("/app/")[-1] for fid, _ in lost)
summary["deleted_tests"] = len(deleted)
summary["lines_lost_if_all_DELETE_applied"] = len(lost)
summary["lines_lost_by_app_file"] = dict(lost_by_file.most_common(25))

# which deleted tests are the sole coverers of the lost lines
culprits = collections.Counter()
for t in deleted:
    k = len(sets[t] & lost)
    if k:
        culprits[t] = k
summary["deleted_tests_touching_lost_lines"] = [
    {"test": t, "lost_lines_touched": k, "verdict": v_of[t]} for t, k in culprits.most_common(60)
]

# 2. the same if MOVE-OUT tests also leave the backend tier
surv2 = [t for t in survive if v_of[t] != "MOVE-OUT"]
s2 = set().union(*(sets[t] for t in surv2)) if surv2 else set()
mv = set().union(*(sets[t] for t in moved)) if moved else set()
lost_mv = (mv - s2)
summary["moved_out_tests"] = len(moved)
summary["extra_lines_lost_from_backend_tier_by_MOVE_OUT"] = len(lost_mv)
summary["move_out_lost_by_app_file"] = dict(
    collections.Counter(files[fid].replace("\\", "/").split("/app/")[-1] for fid, _ in lost_mv).most_common(15)
)

# 3. DELETE-DUP against the named stronger test
index = collections.defaultdict(list)
for t in tests:
    f, n = func_of(t)
    index[n].append(t)
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
    b = set().union(*(sets[t] for t in cand)) if cand else set()
    diff = a - b
    dup_report.append(
        {
            "test": f"{f}::{n}",
            "stronger": strong,
            "stronger_found": len(cand),
            "own_lines": len(a),
            "stronger_lines": len(b),
            "not_in_stronger": len(diff) if cand else None,
            "not_in_stronger_by_file": dict(
                collections.Counter(files[fid].replace("\\", "/").split("/app/")[-1] for fid, _ in diff).most_common(5)
            )
            if cand
            else None,
        }
    )
summary["delete_dup_checked"] = len(dup_report)
summary["delete_dup_stronger_not_found"] = sum(1 for r in dup_report if not r["stronger_found"])
summary["delete_dup_subset_ok"] = sum(1 for r in dup_report if r["stronger_found"] and r["not_in_stronger"] == 0)
summary["delete_dup_not_subset"] = sum(1 for r in dup_report if r["stronger_found"] and r["not_in_stronger"])

# 4. MERGE: unique lines that a table test must keep covering
merge = [t for t in tests if v_of[t] == "MERGE"]
summary["merge_tests"] = len(merge)
summary["merge_tests_with_unique_lines"] = sum(1 for t in merge if uniq(t) > 0)
summary["merge_unique_lines_total"] = sum(uniq(t) for t in merge)

# 5. KEEP with zero app coverage, by file
kz = collections.Counter()
for t in tests:
    if v_of[t].startswith("KEEP") and not by_test[t].get("app_lines"):
        kz[func_of(t)[0]] += 1
summary["keep_with_zero_app_coverage_top_files"] = dict(kz.most_common(25))
summary["unknown_verdict_tests"] = sum(1 for t in tests if v_of[t] == "UNKNOWN")

out = {"summary": summary, "delete_dup": dup_report}
(S / "crosscheck.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=True, indent=1))
print("--- DELETE-DUP not subset / not found:")
for r in dup_report:
    if (not r["stronger_found"]) or r["not_in_stronger"]:
        print(json.dumps(r, ensure_ascii=True))

# 6. which MOVE-OUT tests are sole coverers
mc = collections.Counter()
for t in moved:
    k = len(sets[t] & lost_mv)
    if k:
        mc[t] = k
print("--- MOVE-OUT sole coverers:")
for t, k in mc.most_common(15):
    print(k, t)
# 7. lost lines detail for non-adaptive deletions
for t in ["tests/unit/test_simulator_sse_replay.py::test_subscribe_replays_events_after_last_event_id",
          "tests/unit/test_payment_timeouts.py::test_payment_commit_timeout_returns_committed_when_tx_already_committed",
          "tests/integration/test_p019_pay_retries_a_debt_version_conflict_postgres.py::test_pay_retries_a_debt_version_conflict_on_a_fresh_attempt_and_counts_one_commit"]:
    print(t.split("::")[-1], sorted((files[f].replace("\\","/").split("/app/")[-1], l) for f, l in (sets[t] & lost)))
