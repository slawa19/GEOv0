# T1509 — closing external review of programme 015 (DRAFT: run only after T1549, T1548, T1535, T1523 land)

## Run checklist for the orchestrator (AGENTS.md §15, docs/external-review-runbook.md §3–§4)

1. **Freeze the target.** `<BASE>` = the commit before programme 015's code work started. Candidate found 2026-09-14:
   the spec first appears in `42c2257` (2026-08-24, "docs(015): the money core audit…"), so `42c2257^`. **Confirm at run
   time** with `git log --first-parent main` that no other programme's merge sits between that base and the first 015
   code commit — the branch `claude/012-money-s1` also carries 012 work, and a range that includes another programme's
   delta is not a review of 015. If it does, take the merge-base right after 012's closure instead and say so in the
   ledger. `<HEAD>` = the exact merged HEAD after `T1523`. Announce both freezes: no commits into the frozen tree and no
   edits to this prompt once the run starts.
2. **Credential-free standalone clone**, not the main checkout and not a worktree (their shared `.git/config` may carry a
   credential): `git clone --no-hardlinks <local path> <scratch>/r1509` then `git -C <scratch>/r1509 checkout <HEAD>`,
   verify `git -C ... rev-parse HEAD`, verify no remote credential in its config. Output stored outside the repository.
3. **Binary and model.** On 2026-09-14 `codex` on PATH and `C:\Users\admin\tools\node-v22.12.0-win-x64\codex.cmd` both
   report `codex-cli 0.154.0`. Re-check `--version` at run time; list models with `codex debug models`; pass the model
   explicitly with `-m` and record it as **requested** (not resolved). The in-session reviews so far ran without `-m`, so
   they recorded no model — that is acceptable for working reviews, not for this closing one.
4. **Command:** `codex exec --sandbox read-only -m <MODEL> -c model_reasoning_effort="high" -o <out>/t1509_final.md - <
   <prompt>` from inside the clone. Record the exact command, exit code, and that `-o` is non-empty and ends with the
   verdict markers.
5. **UNVERIFIED, not "no findings":** non-zero exit, timeout, empty/truncated `-o`, missing markers, or an out-of-memory
   crash (seen twice on this machine on 2026-09-14). One recorded fallback is allowed; after it fails the programme stays
   unverified and only the owner can accept the risk.
6. **Environment limits are evidence limits:** read-only means the reviewer ran no gates; record that local gate numbers
   remain the only gate evidence.
7. **Acceptance (§15 + §19.5):** reproduce every class-1 claim by execution myself; a confirmed class-1 is fixed as **one
   task** before closure with one fix-delta round; everything else goes to `specs/BACKLOG.md` with date and recipient.
   `READY-TO-CLOSE: NO` resting only on class 2 does not hold the programme.

## Prompt (frozen at run time)

```
You are the independent closing reviewer of programme 015 "financial-core-verification" in the GEOv0 repository, in this
read-only clone at <HEAD>. Review <BASE>..<HEAD>. Answer in English.

READING BUDGET: this machine has had out-of-memory crashes of this tool. Use git diff --stat, targeted diffs and grep -n
with small line ranges; do not read very large files (the programme spec, AGENTS.md) in full.

Goal of the programme (the owner's): zero sum of all debts, 100% reliability and idempotency of transactions. It is not a
banking application.

Read first, by grep and small ranges:
- AGENTS.md §19.5 (grep "### 19.5"): the stop rule. Every finding you report MUST carry its class:
  CLASS-1 = money or debt moves not as the protocol says, AND you can name a reproduction (a test or a concrete sequence
  of requests); CLASS-2 = everything else (mechanism properties, completeness of records, documentation, CI, an
  adversary inside the process, a hypothetical path with no entry in the application).
- specs/015-financial-core-verification/spec.md section "F. Закрытие" (grep "F. Закрытие", read ~40 lines) and the
  task table rows (grep "^| \`T15").
- docs/ru/02-protocol-spec.md §11 (grep "## 11", read the integrity and reaction parts).

What the programme claims to have delivered — attack these claims, do not re-describe them:
1. Operator equivalent stop (T1544): no money moves in a deactivated equivalent — payment prepare/commit, clearing,
   staged simulator payments, real-mode inject; refusal 409/E008, not retryable; replay of a committed payment still
   answers its result.
2. Reconciliation baseline and criterion (a) (5a): detection of debt change made around the application; one read
   snapshot; results stored as transitions; hosted only by the scheduled loop; never leaks into checks/alerts/audit.
3. Criterion (b) (5b): per operation kind — clearing and payment v2 full recomputation over recorded pre-state; v1
   structural only; inject subset; one batched pre-state read immediately before the envelope.
4. Reaction and hold (5c): confirmed FAILED under the owner lock sets a hold enforced at the T1544 refusal points; admin
   clear only after a later PASSED; evidence FK RESTRICT.
5. T1549: PostgreSQL acceptance at the application's SERIALIZABLE.
6. T1548: a tx_id replay without a stored fingerprint answers 409 and moves no money.
7. T1535: a bare alembic upgrade head works on a fresh database.
8. T1523: the bounded matrix (≤ 8 cells) and one real crash/restart proof.

For each claim: is it true at <HEAD>? Can money or debt move in a way the claim says it cannot? Name the path:line and a
reproduction for anything you call CLASS-1.

Also check, briefly:
- zero-sum of debts: any enabled writer of `debts` that bypasses the journal envelope, the owner lock, or the refusal;
- idempotency: any path where a retried or replayed request applies money twice or reports success without the effect;
- the verification mechanism itself: can a check pass vacuously on the data it is given (a sample chosen to agree)?

Output — marker lines first, then findings, most severe first:

READY-TO-CLOSE: YES | NO
CLASS-1-COUNT: <n>
CLASS-2-COUNT: <n>
CLAIMS: <claim number>=<HOLDS|BROKEN|NOT-ESTABLISHED>, ... for 1-8
DIRECTION: ON-GOAL | DRIFTED:<why>

Each finding: CLASS-1 or CLASS-2; VERDICT-CONFIRMED or VERDICT-PLAUSIBLE; SCOPE-IN-SCOPE / EDGE-CASE-ONLY /
OVERENGINEERED; path:line; for CLASS-1 the reproduction. Write "not established" where reading cannot settle it.
READY-TO-CLOSE is NO only if at least one CLASS-1 finding is VERDICT-CONFIRMED.
```
