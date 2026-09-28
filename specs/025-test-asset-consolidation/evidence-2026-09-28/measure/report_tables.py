"""Emit the numeric tables of REPORT.md from analyze.py output. Usage: python report_tables.py <analysis_dir>"""
import collections
import json
import os
import sys

A = sys.argv[1]
L = lambda n: json.load(open(os.path.join(A, n), encoding="utf-8"))
d, b, s, u, idc = L("by_test.json"), L("by_test_file.json"), L("summary.json"), L("unique_lines.json"), L("identical_sets.json")
out = []
p = out.append
p("### Top-30 test files by time (sum of junit total per test)\n")
p("| # | file | tests | time, s | share | app lines | unique lines | zero-app tests |")
p("|---|---|---|---|---|---|---|---|")
for i, (f, v) in enumerate(list(b.items())[:30], 1):
    p(f"| {i} | `{f}` | {v['tests']} | {v['duration_s']:.1f} | {v['share_of_tier_time']*100:.2f}% | {v['app_lines']} | {v['unique_app_lines']} | {v['tests_with_zero_app_lines']} |")
p("\n### Top-30 test files with zero unique app lines (by time)\n")
p("| # | file | tests | time, s | app lines | zero-app tests | twin group |")
p("|---|---|---|---|---|---|---|")
zu = [(f, v) for f, v in b.items() if v["unique_app_lines"] == 0]
for i, (f, v) in enumerate(zu[:30], 1):
    p(f"| {i} | `{f}` | {v['tests']} | {v['duration_s']:.1f} | {v['app_lines']} | {v['tests_with_zero_app_lines']} | {v['twin_group'] or ''} |")
zu_zero = sum(1 for f, v in zu if v["app_lines"] == 0)
p(f"\nOf {len(zu)} zero-unique files, {zu_zero} cover no app body line at all (guards/scripts/tooling tests); "
  f"{len(zu) - zu_zero} cover app lines that other files also cover. Their summed time: {sum(v['duration_s'] for f, v in zu):.1f} s.")
p("\n### Identical-coverage cluster sizes\n")
p("| cluster size | clusters | tests |")
p("|---|---|---|")
for k, v in idc["size_hist"].items():
    p(f"| {k} | {v} | {int(k) * v} |")
p("\n### Largest 15 identical-coverage clusters\n")
p("| size | app lines | files |")
p("|---|---|---|")
for c in idc["clusters"][:15]:
    p(f"| {c['size']} | {c['app_lines']} | " + ", ".join(f"`{x}`" for x in c["test_files"][:4]) + (" ..." if len(c["test_files"]) > 4 else "") + " |")
p("\n### Top-10 tests by time\n")
p("| time, s | phases | app lines | setup app lines | test |")
p("|---|---|---|---|---|")
for n, v in sorted(d.items(), key=lambda kv: -(kv[1]["duration_total_s"] or 0))[:10]:
    ph = ", ".join(f"{k} {x}" for k, x in v["duration_phases_s"].items())
    p(f"| {v['duration_total_s']:.2f} | {ph} | {v['app_lines']} | {v['setup_app_lines']} | `{n}` |")
ran = [n for n, v in d.items() if v["outcome"] in ("passed", "failed", "error", "xfailed")]
hist = collections.Counter()
for n in ran:
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
print("\n".join(out))
