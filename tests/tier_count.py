"""The backend tier counts what it collects (035 F-035-14, slice C1).

WHY. The tooling tier refuses a count that differs from `EXPECTED_CASES` (`tooling-tests/conftest.py`). The backend
tier had no such count: a deleted module, a `--ignore` or a `--deselect` on the canonical run ended green. Measured
2026-10-08 on `75dafc82`, direct pytest: one `--deselect` of one case, 3149 selected / 16 deselected, exit 0, and
nothing in the session noticed. This module is the whole mechanism: one constant, one comparison, one profile test;
`tests/conftest.py` calls it from `pytest_collection_finish`.

THE RULE. On the CANONICAL PROFILE the number of selected cases must equal `EXPECTED_SELECTED_ITEMS`, in EITHER
direction (a lost case and an unrecorded new case fail alike, AGENTS.md section 6). A mismatch ends the session with
exit 4 (`pytest.ExitCode.USAGE_ERROR`) at the end of collection, before any test body runs, so it also holds under
`--collect-only`.

WHAT THE CANONICAL PROFILE IS, decided from the session's own options and never from an environment variable that
can be forgotten:

* the marker expression is `not slow` - what `scripts/verify_local.ps1 -BackendOnly` passes by default - compared after
  runs of whitespace are collapsed (`not  slow` is the same expression); a differently spelled equivalent such as
  `(not slow)` is NOT recognised and silently turns the guard off for that run (see "What this does not see");
* the positional arguments, if any, name the whole `tests` directory and nothing narrower (no argument at all means
  `testpaths = tests`; `-BackendSelector tests` is the whole tier and IS counted). Any narrower path or a `path::node`,
  also after `--`, is a selector: a deliberate narrowing that is NOT counted (`-- tests` is still the whole tier).

Everything else that reduces or reorders the selection is NOT an exemption, so the rule is not vacuous:
`--deselect`, `--ignore`, `--ignore-glob`, `-k`, `--lf`, `--sw`. Run them with a path selector, or accept the refusal.

NOT COUNTED BY THIS RULE: a run with a selector narrower than `tests` (`-BackendSelector <path>`), and a run with no marker expression
(`-IncludeExpensive`, the wider profile: nothing is excluded, the 15 `slow` cases come back). The wide profile has no
constant of its own because the rule asks for one number; it is the canonical number plus the `slow` cases.

WHAT MOVES THE NUMBER BESIDES A TEST FILE (found by review, 2026-10-08; the first version of this file said "nothing").
The count is the CARDINALITY OF THE SET OF SELECTED ITEMS, and two parametrize lists are built at import from the
working tree. Found by reading every `parametrize` / `fixture(params=)` argument of `tests/**` back to its definition
(a scan of 2026-10-08; scans in `tests/` that only ASSERT inside a test body do not move the count):

* `tests/unit/test_p012_t1211_money_path_never_types_a_float.py` - `_float_sites()` walks `MONEY_MODULES`
  (`app/core/money_boundary.py`, `app/core/clearing`, `app/core/trustlines`, `app/core/balance`, `app/utils/money.py`,
  `app/utils/validation.py` and the other entries of that list; directories by `rglob("*.py")`) and parametrizes one case
  per `float` site found there: `TYPED` 18, `CONSTRUCTED` 6, `DECLARED` 3 on 2026-10-08. A PRODUCT edit that adds or removes
  a float typing, float construction or float-annotated attribute in those modules, or a new `.py` file in those
  directories (even an untracked one), changes the count without touching a backend test.
* `tests/unit/test_p012_t1211_money_rendering_conformance.py` - `_CASES` is `api/money-rendering-conformance.json`
  (`TABLE_PATH`): 44 cases of `test_to_money_str_conforms_to_the_shared_table`. Editing the JSON table changes the count.

So this is a guard of the set's cardinality, not of the list of tests; a change of one of these sources is a legitimate
reason to move the constant, and the refusal message says so. The scan searched for names defined from file reads, globs,
JSON, environment and platform, and for names imported from `app` / `scripts`; the other parametrize lists are literals or
constructed values (exception instances, datetimes). A scan is not a proof: a new parametrize list built from the tree is
invisible until it moves the count.

PLATFORM. `skipped` cases are COLLECTED cases, so the number is the same on every platform as long as collection does
not depend on the platform. NOT REFUTED by reading (grep over `tests/`): no `collect_ignore`, no
`pytest_ignore_collect`, no `allow_module_level` skip, no `importorskip` that removes a module, no platform branch in a
parametrize list; the only platform conditions are the three `skipif` listed below, which skip a collected case, and the
two sources above walk sorted `rglob` paths and read a committed JSON file. It is a reading and NOT measured on ubuntu:
the first `required-backend` run after this change is the measurement (AGENTS.md section 16, item 6).

CHANGING THE NUMBER. The set of selected items changed: a test was added, moved or deleted, or one of the sources above
moved. Change `EXPECTED_SELECTED_ITEMS` in the same commit with a dated line below saying which. That is the only way to
make the session green again:

* 2026-10-08, 035 slice C1 (F-035-14): first value, 3150 (3165 with the 15 `slow` cases). Measured with
  `python -m pytest --collect-only -q -m "not slow"` on `75dafc82`; on Windows the full run was
  `3144 passed, 2 skipped, 3 xfailed, 16 deselected` with one case deselected, which is 3149 + 1.
* 2026-10-08, 035 slice C1, merge of `origin/main` `0f248b9c`: 3150 -> 3219 (+69; 3234 with the 15 `slow` cases). The merge
  brought backend tests of 035 A1 (PR #166: `test_p035_a1_cycles_do_not_hold_the_event_loop.py`, `test_p035_b1_equivalent_query_code.py`,
  `tests/p035_support.py`) and 034 S1a (PR #167: three `test_p034_s1_*` modules, `test_p034_s1_launch_epoch_and_post_commit_publication.py`)
  plus additions to existing modules. The guard itself refused the first merged collection ("MORE than recorded", exit 4)
  - the case it is for. Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-08, 034 slice S1b: 3219 -> 3233 (+14; 3248 with the 15 `slow` cases). Four new modules of the slice:
  `tests/unit/test_p034_s1b_artifact_event_drops.py` (4), `tests/unit/test_p034_s1b_interact_silent_failures_are_logged.py` (2),
  `tests/unit/test_p034_s1b_heartbeat_failure_is_not_silent.py` (4) and
  `tests/integration/test_p034_s1b_payment_edge_patch_names_the_line_postgres.py` (4). Measured with
  `python -m pytest --collect-only -q -m "not slow"` on the branch merged with `origin/main` `4a1768d5`; the guard refused
  the collection first ("selected 3233 case(s), expected exactly 3219 ... MORE than recorded", exit 4).
* 2026-10-08, 035 slice A3 (F-035-2), on `origin/main` `dd019a31`: 3233 -> 3235 (+2). Two new cases in
  `tests/integration/test_p035_a3_trustlines_page_is_not_a_query_per_line.py` (the statement count of a page and the
  page against the single-line read). Measured with `python -m pytest --collect-only -q -m "not slow"` on the branch
  merged with `dd019a31` (on `4a1768d5` the same two cases read 3219 -> 3221, by the guard's own refusal).
* 2026-10-08, 035 slice A4 (F-035-3), merged after A3 (`origin/main` `9b19fc04`): 3235 -> 3239 (+4; on `dd019a31` alone it read 3233 -> 3237). Four new cases in
  `tests/integration/test_p035_a4_neutrality_reads_the_cycle_as_a_set.py` (the statement count, two counterexamples,
  the set read against the per-participant read). Measured with `python -m pytest --collect-only -q -m "not slow"` on
  the branch merged with `dd019a31` (on `4a1768d5` the same four cases read 3219 -> 3223, by the guard's own refusal).
* 2026-10-08, 034 slice S1c: 3233 -> 3239 (+6; 3254 with the 15 `slow` cases). One new module,
  `tests/unit/test_p034_s1c_simulator_state_dir.py` (6). The slice first carried a retention mechanism with a module of 14
  cases (the branch said 3247 then, never `main`); the mechanism and its tests were withdrawn after the section-15 review,
  so that number is gone with them. Measured with `python -m pytest --collect-only -q -m "not slow"` on the branch merged
  with `origin/main` `dd019a31`.
* 2026-10-08, 034 slice S1c, fix-delta of the review of `a08f85a4`: 3239 -> 3243 (+4; 3258 with the 15 `slow` cases). One
  new module, `tests/unit/test_p034_s1c_scripts_do_not_start_the_simulator_runtime.py`: one test parametrized over three
  script invocations, and one more. Measured with `python -m pytest --collect-only -q -m "not slow"`.
* 2026-10-08, 034 slice S1c merged after 035 A3 and A4 (`origin/main` `9cbc92ff`, 3239): 3239 -> 3249 (+10, the two S1c modules above; the numbers 3239 and 3243 in the two S1c lines are those of the branch on `dd019a31`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-08, 035 slice A5 (F-035-4), on `origin/main` `dd019a31`: 3233 -> 3234 (+1). One new case in
  `tests/integration/test_p035_a5_verify_does_not_lose_its_audit_row.py`. Measured with
  `python -m pytest --collect-only -q -m "not slow"` on the branch merged with `dd019a31` (on `4a1768d5` the same case
  read 3219 -> 3220, by the guard's own refusal).
* 2026-10-08, 035 slice A5, review of `a181b6c1`: 3234 -> 3236 (+2). The one case of
  `tests/integration/test_p035_a5_verify_does_not_lose_its_audit_row.py` became three (the failure on the first, the
  second and the third audit row). Measured with `python -m pytest --collect-only -q -m "not slow"`.
* 2026-10-08, 035 slice A5 merged after 035 A3, A4 and 034 S1c (`origin/main` at the merge of PR #175, 3249): 3249 -> 3252 (+3, the three A5 cases above; the numbers in the A5 lines are those of the branch on `dd019a31`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-08, 035 slice A6 (`T3511`), on `origin/main` `213c84e3`: 3252 -> 3254 (+2). Two new cases in
  `tests/integration/test_p035_a6_payment_targets_public_router_method.py`. The number is the guard's own refusal
  ("selected 3254 case(s), expected exactly 3252", `python -m pytest --collect-only -q -m "not slow"`).
* 2026-10-08, 034 slice S4 (F-034-14, the status part), on `origin/main` `213c84e3`: 3252 -> 3262 (+10; 3277 with the 15
  `slow` cases). One new module, `tests/integration/test_p034_s4_inject_status_follows_the_seeder_postgres.py`: six
  control cases, one anti-vacuum case, three cases of the reproducer. Measured with
  `python -m pytest --collect-only -q -m "not slow"`; the guard refused the collection first ("selected 3262 case(s),
  expected exactly 3252 ... MORE than recorded", exit 4).
* 2026-10-09, 034 slice S4 merged after 035 A6 (`origin/main` at the merge of PR #181, 3254): 3254 -> 3264 (+10, the S4 module above; the numbers in the S4 line are those of the branch on `213c84e3`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-08, 034 slice S3 (F-034-7, -12, -14), on `origin/main` `1faf261b` (without S4): 3252 -> 3253 (+1; 3268 with the
  15 `slow` cases). +2 `tests/integration/test_p034_s3_a_healthy_clearing_tick_logs_no_warning_postgres.py`, +2
  `tests/integration/test_p034_s3_total_debt_is_the_runs_own_postgres.py`, -1 `test_count_active_runs` (the method is
  removed), -2 in `tests/unit/test_topology_changed_no_empty_payload.py` (five cases of a removed function became three
  on the live emitter). Measured with `python -m pytest --collect-only -q -m "not slow"`; the guard refused first
  ("selected 3253 case(s), expected exactly 3252 ... MORE than recorded", exit 4).
* 2026-10-09, 034 slice S3 merged after 035 A6 and 034 S4 (`origin/main` `bfae3cda`, 3264): 3264 -> 3265 (+1, the S3 changes above; the numbers in the S3 line are those of the branch on `1faf261b`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 034 slice S4b (a non-finite initial limit of `add_participant`), on `origin/main` `bfae3cda`: 3264 -> 3274
  (+10; 3289 with the 15 `slow` cases). One new module,
  `tests/integration/test_p034_s4b_a_bad_initial_line_does_not_split_the_participant_postgres.py`: three control cases,
  six cases of the reproducer, one of the sibling `create_trustline`. Measured with
  `python -m pytest --collect-only -q -m "not slow"` (`3274/3289 tests collected`); the guard refused it first (exit 4).
* 2026-10-09, 034 slice S4b merged after 034 S3 (`origin/main` `3ce2c6e6`, 3265): 3265 -> 3275 (+10, the S4b module above; the numbers in the S4b line are those of the branch on `bfae3cda`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 035 slice A2a, on `origin/main` `3ce2c6e6`: 3265 -> 3275 (+10). New: 11 cases in
  `tests/integration/test_p035_a2a_seed_and_controls_read_the_planner.py` and 5 in
  `tests/unit/test_p017_t1711_seed_recipe_refuses.py` (the seed's two modes). Fewer: tests moved from the retired
  detectors to the planner lost the depth parametrization that had no meaning there - `test_p020_selection_retention_
  postgres.py` -4 (6 -> 3 and 2 -> 1), `test_p012_t1210_detector_union_default_tier.py` -2 (3 -> 1).
* 2026-10-09, 035 slice A2a, review of `4b833af5`: 3275 -> 3281 (+6). Four cases of the final acceptance in
  `tests/unit/test_p017_t1711_seed_recipe_refuses.py` (the surviving cycle at its declared amount) and two in
  `tests/integration/test_p035_a2a_seed_and_controls_read_the_planner.py` (the same on the real view; the control's
  step read from the equivalent). Both A2a numbers are restated from `3ce2c6e6`; the total, 3281, is measured with
  `python -m pytest --collect-only -q -m "not slow"` on the branch merged with it (on `bfae3cda` the same sixteen read
  3264 -> 3280).
* 2026-10-09, 035 slice A2a merged after 034 S4b (`origin/main` at the merge of PR #186, 3275): 3275 -> 3291 (+16, the A2a cases above; the numbers in the A2a lines are those of the branch on earlier bases). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 034 slice S2 (F-034-11), on `origin/main` `0d153390`: 3265 -> 3267 (+2). One new module,
  `tests/integration/test_p034_s2_payment_targets_route_answers_as_before.py` (the route byte for byte, and the refusals
  around it). Measured with `python -m pytest --collect-only -q -m "not slow"` (`3267/3282 tests collected`).
* 2026-10-09, 034 slice S2 merged after 034 S4b (`origin/main` `906cae90`, 3275): 3275 -> 3277 (+2, the S2 module above; the numbers in the S2 line are those of the branch on `0d153390`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 034 slice S2 merged after 035 A2a (`origin/main` `a0d25e7c`, 3291): 3291 -> 3293 (+2, the S2 module; the earlier S2 lines are those of the branch on `0d153390` and `906cae90`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 035 slice A2b (the detectors removed from `app/core/clearing/service.py`): 3291 -> 3273 (-18, tests of the removed contract: `test_a_long_cycle_appears_exactly_when_the_caller_asks_deep_enough` 5, `test_retention_each_depth_finds_its_lengths_and_nothing_longer` 5, `test_retention_same_length_ties_follow_the_full_identity` 2, `test_the_merged_answer_reports_one_cycle_once` 1, `test_within_a_length_the_largest_executable_cycle_comes_first` 1, `test_sql_and_dfs_produce_same_cycles` 1, `test_find_quadrangles_sql_rejects_repeated_vertex_b_equals_d` 1, `test_the_sql_producer_itself_is_scoped` 1, `test_the_expanding_bind_works_on_postgresql` 1; two more tests were narrowed and renamed, one case each, count unchanged). Each removed test has a dated note where it stood. Measured with the same command on base `a0d25e7c`.
* 2026-10-09, 035 slice A2b merged after 034 S2 (`origin/main` at the merge of PR #187, 3293): 3293 -> 3275 (-18, the detector-only cases named in the A2b line above; its numbers are those of the branch on `a0d25e7c`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 035 money storability past the decimal context (review of 034 S4b), on `origin/main` `a0d25e7c`:
  3291 -> 3355 (+64). One new module, `tests/integration/test_p035_money_storability_does_not_depend_on_the_decimal_
  context.py`: the rule itself (boundary table, values past the context, a narrow context), the simulator's three
  entries and ten cases of the two HTTP money doors. Measured with `python -m pytest --collect-only -q -m "not slow"`
  on the branch merged with `a0d25e7c` (on `906cae90` the same 64 read 3275 -> 3339, by the guard's own refusal).
* 2026-10-09, the money-storability fix merged after 034 S2 and 035 A2b (`origin/main` at the merge of PR #189, 3275): 3275 -> 3339 (+64, the module named in the line above; its numbers are those of the branch on `a0d25e7c`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 034 slice S2b (F-034-3, divergences 1 and 3), on `claude/p034-s2` `0b67a3cb` (3293): 3293 -> 3299 (+6).
  `tests/integration/test_p034_s2_clearing_done_amount_is_in_the_equivalents_step_postgres.py` (4: a control, two
  precisions, amounts finer than hundredths) and
  `tests/integration/test_p034_s2b_interact_clearing_done_is_published_once_postgres.py` (2). Measured with
  `python -m pytest --collect-only -q -m "not slow"` (`3299/3314 tests collected`).
* 2026-10-09, 034 slice S2b, fix-delta of the review of `ed3271fe`: 3299 -> 3303 (+4). Three cases of the new
  `tests/integration/test_p034_s2b_clearing_done_reads_the_precision_before_the_pass_postgres.py` and one more in
  `tests/integration/test_p034_s2b_interact_clearing_done_is_published_once_postgres.py`. Measured with
  `python -m pytest --collect-only -q -m "not slow"` (`3303/3318 tests collected`).
* 2026-10-09, 034 slice S2b merged after 035 A2b (`origin/main` `94891571`, 3275): 3275 -> 3285 (+10, the three S2b modules: 4 + 3 + 3; the S2b lines above are those of the branch before the merge). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 034 slice S2b merged after the money-storability fix (`origin/main` at the merge of PR #188, 3339): 3339 -> 3349 (+10, the three S2b modules above; the numbers in the S2b lines are those of the branch on earlier bases). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 035 slice A8 (public names for the staged owner, the payment read side moved to `app/core/payments/read.py`): 3339 -> 3352 (+13: `tests/unit/test_p035_a8_public_names_for_the_staged_owner.py` 12, `tests/integration/test_p035_a8_payment_read_side_answers_as_before.py` 1). Measured with the same command on base `7e21abc1`.
* 2026-10-09, 035 slice A8 merged after 034 S2b (`origin/main` at the merge of PR #191, 3349): 3349 -> 3362 (+13, the two A8 modules above; the numbers in the A8 line are those of the branch on `7e21abc1`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 036 slice A (`T3610`), on `origin/main` `71c66dda` (3349): 3349 -> 3369 (+20). New: `tests/unit/test_p036_a_episodes_projection.py` (6),
  `tests/integration/test_p036_a_scenario_detail_serves_the_story.py` (6), `tests/integration/test_p036_t3601_time_token_is_refused_at_upload.py` (4: the token, its control, and the two equivalent sources of the new event fields) and
  `tests/unit/test_p036_t3601_fixture_controls.py` (4, the pinned stress multipliers and the absent `params`). Measured with
  `python -m pytest --collect-only -q -m "not slow"` (`3369/3384 tests collected`); the guard refused the collection first (exit 4).
* 2026-10-09, 036 slice A fix-delta (review of `1afe0b09`), on `origin/main` `71c66dda`: 3369 -> 3459 (+90). New: `tests/integration/test_p036_a2_story_is_validated_at_upload.py` (51),
  `tests/integration/test_p036_a2_stored_story_is_refused_not_trimmed.py` (4), `tests/integration/test_p036_a_scenario_detail_conformance.py` (2);
  `tests/unit/test_p036_a_episodes_projection.py` 6 -> 39 (+33: the cases that pinned the silent drop were rewritten as refusals). The number is
  the guard's own refusal ("selected 3459 case(s), expected exactly 3369", `python -m pytest --collect-only -q -m "not slow"`, `3459/3474`).
* 2026-10-09, 036 slice A merged after 035 A8 and A7 (`origin/main` at the merge of PR #193, 3362): 3362 -> 3472 (+110, the 036 A modules above; the numbers in the 036 A lines are those of the branch on `71c66dda`). Measured with `python -m pytest --collect-only -q -m "not slow"` on the merged tree.
* 2026-10-09, 036 slice A second fix-delta (review of `c2d84180`), on `origin/main` `6738cec3` (3472): 3472 -> 3476 (+4). New: `tests/integration/test_p036_a3_upload_reads_no_database.py` (3: the trap's anti-vacuum, a
  control without a payment, the upload with a payment); `tests/unit/test_p036_a_episodes_projection.py` +4 (a focus edge with an empty start, an empty end, the `from_` spelling; an anchor in the `from_`
  spelling). Fewer: the three upload tests of the equivalent's step in `test_p036_a2_story_is_validated_at_upload.py` (the check moved to slice B, execution). Measured with `python -m pytest --collect-only -q -m "not slow"` (`3476/3491`).
* 2026-10-09, 036 slice B1 (`T3620`), on `origin/main` `24b09d83` (3476): 3476 -> 3497 (+21). New: `tests/integration/test_p036_b1_scripted_events_postgres.py` (18: the scripted payment, its time, its key, the
  restart, a failed tick, the core's refusals, the equivalents outside the run, the scripted clearing, the restart reset, F-036-6 with its control) and
  `tests/unit/test_p036_b1_scripted_events_are_spent_when_durable.py` (3). Measured with `python -m pytest --collect-only -q -m "not slow"` (`3497/3512`).

SKIPPED CASES OF THE CANONICAL RUN, NAMED (`-rs` shows them; compare the number in the CI log with this list):

* Windows (`os.name == "nt"`): 2 - `tests/unit/test_deployment_config.py::test_entrypoint_preserves_custom_command_after_migrations`
  (no POSIX shell) and `tests/unit/test_settings_guardrails.py` case under `skipif(os.name == "nt")` at line 260
  (Windows environment keys are case-insensitive). Measured 2026-10-08: `SKIPPED [1] ...test_deployment_config.py:131`,
  `SKIPPED [1] ...test_settings_guardrails.py:260`.
* Linux (`os.name == "posix"`): 1 - the case under `skipif(os.name != "nt")` in
  `tests/unit/test_p024_scenario_id_is_a_safe_path_segment.py` at line 188 (Windows device names). INFERRED from the
  conditions, not measured on Linux; the CI log of `required-backend` is the measurement.
* Two conditional skips that did not fire on the measured full run:
  `tests/contract/test_p011_responses_conform_to_the_canon.py:521-523` skips the AGGREGATE only when the session was vacuous
  (`_vacuity_skip_reason`); `:1506-1507` skips `test_the_aggregate_run_alone_no_longer_reports_success_over_zero_bodies`
  when `GEO_CONFORMANCE_NO_SUBPROCESS` is set (an escape hatch for that one case, not for the aggregate).
  Expected strict `xfail` cases: 3 on the Windows run (`xfailed`), not named here.

`tooling-tests/portable/test_p035_c_backend_tier_counts_what_it_collects.py` holds that each named skip is still
declared where this list says it is.

A COLLECTION ERROR IS NOT THIS GUARD'S TO JUDGE. pytest 7.4 calls `pytest_collection_finish` even when a module failed to
import, and aborts only afterwards (exit 2). A broken module has no cases, so the count would read "fewer" and blame a
loss for what is an import error (reproduced 2026-10-08 on `7edf4cb8`: exit 4 instead of 2). The hook therefore returns
without judging when the session recorded a collection error or a stop request (`session.testsfailed`, `shouldstop`,
`shouldfail`); pytest's own failure handling and error text stand (exit 2 in the ordinary invocation; exit 1 under
`--continue-on-collection-errors`). Reproducer and counter-check:
`tooling-tests/portable/test_p035_c_backend_tier_counts_what_it_collects.py`.

WHAT THIS DOES NOT SEE. A marker expression with the same meaning but another spelling (`(not slow)`, `not (slow)`): it is
not recognised, the run is not counted, and nothing says so. WHICH tests are present: a case replaced by another under the same number passes. Whether the
number was RIGHT when recorded: it is a measurement of the tier at the commit that changed it. A reconfiguration of the
runner itself (`-o addopts=...`, `-p no:...`, `PYTEST_ADDOPTS` that ends pytest before the end of collection): the
runner is not defended against reconfiguring itself (the same limit as `tooling-tests/conftest.py`). A collection that
ERRORS (import failure) is left to pytest and its own exit code and error text.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

#: THE EXPECTED NUMBER OF SELECTED CASES OF THE CANONICAL PROFILE (parametrised cases count one each).
#: Moves only by the dated lines in the module docstring.
EXPECTED_SELECTED_ITEMS = 3497

#: The marker expression `scripts/verify_local.ps1` passes without `-IncludeExpensive`.
CANONICAL_MARKEXPR = "not slow"

#: The directory whose collection is the whole tier (`pytest.ini` `testpaths`).
TIER_DIRECTORY = "tests"


def is_canonical_profile(
    *,
    markexpr: str | None,
    args: Sequence[str],
    invocation_dir: Path,
    root: Path,
) -> bool:
    """True when the session collects the whole tier under the canonical marker expression.

    `args` are the session's positional arguments after pytest has substituted `testpaths` for an empty list.
    A `path::node` argument is a selector; so is every path other than the tier directory itself.
    """

    if " ".join(str(markexpr or "").split()) != CANONICAL_MARKEXPR:
        return False
    if not args:
        return True
    tier = (root / TIER_DIRECTORY).resolve()
    for argument in args:
        if "::" in argument:
            return False
        if (invocation_dir / argument).resolve() != tier:
            return False
    return True


def count_problem(*, selected: int, expected: int = EXPECTED_SELECTED_ITEMS) -> str | None:
    """None when the count matches; otherwise the refusal text, for either direction.

    The text states the numbers as fact and lists the possible causes without choosing one: the count cannot tell a
    lost test from a changed parametrize source.
    """

    if selected == expected:
        return None
    direction = "FEWER than recorded" if selected < expected else "MORE than recorded"
    return (
        f"the canonical backend profile (whole tier, -m 'not slow', no path selector) selected {selected} case(s), "
        f"expected exactly {expected} (EXPECTED_SELECTED_ITEMS in tests/tier_count.py): {direction}. The count does "
        "not say why. Possible causes: a test module or case added, deleted or renamed; a --deselect, --ignore, -k or "
        "--lf on this run; a marker change; a change of a source that builds parametrize lists from the working tree "
        "(the float scan over the money modules, api/money-rendering-conformance.json - the full list is in the "
        "docstring of tests/tier_count.py). If the change is intended, set the constant in the same commit and add a "
        "dated line to that docstring saying which. To run a subset on purpose, give a path selector "
        "(scripts/verify_local.ps1 -BackendSelector <paths>) or run without -m (-IncludeExpensive); neither is counted."
    )
