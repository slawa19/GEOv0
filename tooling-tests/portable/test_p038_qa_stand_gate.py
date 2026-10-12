"""038 S2: the Simulator UI QA stand is wired into the required gate, in the files' own words.

WHAT THIS PROVES. Four files agree: `simulator-ui/v2/package.json` (the scripts `test:e2e:qa*`),
`simulator-ui/v2/e2e/playwright.qa.config.ts` (the stand's Playwright config),
`simulator-ui/v2/e2e/qa/run-stand.mjs` (the gate entry that counts what ran) and
`.github/workflows/quality.yml`. Namely:

* the stand's scripts run THE STAND'S config and its `qa-stand` project, not the snapshot stand's, and reach the
  counted project through the runner only: `test:e2e:qa` is the runner's debug scope, which is marked as not a gate
  and never prints the line of a counted verdict (039 slice A, 2026-10-12; until then it was a bare `playwright test`);
* the stand's config does not put the product in test mode and clears `navigator.webdriver`: under
  either, sparks are not spawned, the render loop never sleeps, dragging and edge hover are off, and
  every result of the stand would be about another product (spec 038, F-038-1);
* the required job `ui-smoke` - every pull request, no condition - has a step that runs the gate
  entry, and that step is neither conditional nor allowed to fail;
* the gate entry compares the number of tests that ran with a recorded number and refuses a skip;
* that step has a `timeout-minutes` of its own, under the job's, and the HTML report of the step is
  uploaded on every run (`if: always()` on the UPLOAD step only), so that the evidence of a red or a
  timed-out step survives it (039 slice A, 2026-10-12);
* no file of the counted project imports the stand's wall-clock instrument (`perf-measure.ts`): the
  counted project is counts on a fake clock, and a machine-dependent number must not decide a pull
  request (039 slice A, R14);
* the scheduled job runs the whole stand and uploads its HTML report; since 2026-10-12 (039 slice A) the same job
  records the owner's videos (`owner.qavideo.ts`), and the stand's report (the gallery), the report of the video run
  and the raw videos leave it in ONE artifact, uploaded on every run after both steps; no pull-request job records
  videos. Not seen: that the artifact really holds three videos - that is the log of the step and the artifact itself;
* two things the spec forbids are absent: `waitForTimeout` in the stand, and a product branch on
  "am I under the QA stand".

WHAT THIS DOES NOT PROVE (AGENTS.md section 11: a guard checks FORM, not truth). Reading text cannot
show that a single test ran on CI, that Chromium started without a GPU, that the product was really
out of test mode in the page (the stand checks that itself, in the browser: `e2e/qa/stand.ts`,
`assertUserMode`), or that the recorded number is the right one. Those are established by the log
of the step:

    gh run view <run-id> --log --job "Active UI Chromium smoke"

where the last lines of the step read `run-stand [gate]: ran N (...), skipped 0, recorded N`, and
locally by:

    npm --prefix simulator-ui/v2 run test:e2e:qa:gate

WHAT SURVIVED THE ADVERSARIAL PASS ON a7f7e1a6 AND IS STILL NOT SEEN HERE (2026-10-11), by name: `continue-on-error`
on the JOB, or given as an expression instead of `true`; a `shell:` that swallows the exit code; a `needs:` that
hangs the required job on a scheduled one; `paths-ignore` on the trigger; and the CONTENT of the browser flags -
`NO_GPU_ARGS = []` passes this file. The last one is checked where it can be, in the browser: `boot()` of the stand
refuses a page whose root says `data-gpu="1"` (`e2e/qa/stand.ts`). A `|| true` appended to the command of the step IS
seen (corrected 2026-10-12, BACKLOG 038-2 (g): this text used to name it a blind spot): the command of the step is
compared whole and exactly one such step is required.

ABOUT THE TIMEOUT AND `always()` (2026-10-12). What is checked is that the number exists and is under the job's, and
that `always()` sits on the upload step and not on the gate step. Whether a step that times out fails the job, and
whether a later `always()` step leaves the job red, is the behaviour of GitHub Actions and is NOT executed here; and
that the number is ENOUGH for the step is shown only by the duration in the log of the step. The wall-clock check sees
an import of `perf-measure` or a call of its two functions - not a threshold on `performance.now()` written by hand.

Every check goes through `violations()` so that the counter-check at the bottom can feed it broken
inputs and see it complain (section 9, anti-vacuum).
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_UI = _ROOT / "simulator-ui" / "v2"
_STAND_CONFIG = "e2e/playwright.qa.config.ts"
_GATE_COMMAND = "npm --prefix simulator-ui/v2 run test:e2e:qa:gate"
_FULL_COMMAND = "npm --prefix simulator-ui/v2 run test:e2e:qa:report"
_VIDEO_COMMAND = "npm --prefix simulator-ui/v2 run test:e2e:qa:video"
_SCHEDULE_ONLY_IF = "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"


def _inputs() -> dict[str, Any]:
    return {
        "workflow": yaml.safe_load((_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")),
        "package": json.loads((_UI / "package.json").read_text(encoding="utf-8")),
        "config": (_UI / "e2e" / "playwright.qa.config.ts").read_text(encoding="utf-8"),
        "runner": (_UI / "e2e" / "qa" / "run-stand.mjs").read_text(encoding="utf-8"),
        "verdict": (_UI / "e2e" / "qa" / "run-verdict.mjs").read_text(encoding="utf-8"),
        "video_config": (_UI / "e2e" / "qa" / "playwright.video.config.ts").read_text(encoding="utf-8"),
        # The files of the counted project, by the glob of its `testMatch` (`**/*.qa.ts` under `e2e/qa`).
        "counted": {p.name: p.read_text(encoding="utf-8") for p in sorted((_UI / "e2e" / "qa").rglob("*.qa.ts"))},
    }


def _runs(job: dict, command: str) -> list[dict]:
    return [
        step
        for step in job.get("steps", [])
        if isinstance(step, dict) and " ".join(str(step.get("run", "")).split()) == command
    ]


def violations(inputs: dict[str, Any]) -> list[str]:
    found: list[str] = []
    scripts = inputs["package"].get("scripts", {})
    config: str = inputs["config"]
    runner: str = inputs["runner"]
    workflow = inputs["workflow"]
    jobs = workflow.get("jobs", {})

    # 1. The scripts run the stand's config and project.
    #    039 slice A (2026-10-12): the counted project is reached through the runner ONLY. `test:e2e:qa` used to be a bare
    #    `playwright test --project=qa-stand`: a run nobody judged, whose "N passed" read like a result of the stand. It
    #    is the runner's debug scope now, and no other script may run the counted project past the runner.
    if scripts.get("test:e2e:qa") != "node e2e/qa/run-stand.mjs debug":
        found.append(f"script test:e2e:qa is not the debug entry of the runner: {scripts.get('test:e2e:qa')!r}")
    for name, command in scripts.items():
        if "qa-stand" in str(command) or (_STAND_CONFIG in str(command) and "--project=qa-perf" not in str(command)):
            found.append(f"script {name} runs the counted project past the runner (run-stand.mjs): {command!r}")
    for needle in ("export const DEBUG_BANNER = 'DEBUG-ONLY, NOT A GATE'", "if (v.counted) console.log(", "recorded: debug ? null : EXPECTED[scope]"):
        if needle not in runner:
            found.append(f"run-stand.mjs lost the mark of its debug scope: {needle}")
    if scripts.get("test:e2e:qa:gate") != "node e2e/qa/run-stand.mjs gate":
        found.append(f"script test:e2e:qa:gate is not the gate entry: {scripts.get('test:e2e:qa:gate')!r}")
    if scripts.get("test:e2e:qa:report") != "node e2e/qa/run-stand.mjs full":
        found.append(f"script test:e2e:qa:report is not the full entry: {scripts.get('test:e2e:qa:report')!r}")
    for needle in (f"--config={_STAND_CONFIG}", "--project=qa-stand", "--grep-invert=", "'@extended'"):
        if needle not in runner:
            found.append(f"run-stand.mjs does not pass {needle}")

    # 2. The stand's config keeps the product in the user's mode.
    if not re.search(r"VITE_TEST_MODE:\s*'0'", config):
        found.append("the stand config does not start its server with VITE_TEST_MODE '0'")
    if re.search(r"VITE_TEST_MODE:\s*['\"]1['\"]", config):
        found.append("the stand config turns test mode on")
    if "--disable-blink-features=AutomationControlled" not in config:
        found.append("the stand config does not clear navigator.webdriver")
    if not re.search(r"name:\s*'qa-stand',\s*testMatch:\s*'\*\*/\*\.qa\.ts'", config):
        found.append("the qa-stand project does not collect **/*.qa.ts")

    # 3. The gate entry counts and refuses.
    if not re.search(r"export const EXPECTED = \{ gate: [1-9]\d*, full: [1-9]\d* \}", runner):
        found.append("run-stand.mjs has no recorded EXPECTED numbers")
    verdict: str = inputs["verdict"]
    for needle in ("verdict({", "process.exit(v.code)", "--retries=0"):
        if needle not in runner:
            found.append(f"run-stand.mjs lost its refusal: {needle}")
    for needle in (
        "counted && ran !== want", "ran === 0", "stats.skipped !== 0", "stats.flaky !== 0", "expectedFailures !== 0",
        "const counted = run.scope !== 'debug'",
    ):
        if needle not in verdict:
            found.append(f"run-verdict.mjs lost its refusal: {needle}")
    if not re.search(r"\bretries:\s*0\b", config):
        found.append("the stand config does not pin retries to 0")

    # 4. The required job runs the gate entry, unconditionally.
    triggers = workflow.get("on", workflow.get(True)) or {}
    if "pull_request" not in triggers:
        found.append("the workflow does not run on pull requests")
    smoke = jobs.get("ui-smoke", {})
    if "if" in smoke:
        found.append(f"job ui-smoke is conditional: {smoke['if']}")
    gate_steps = _runs(smoke, _GATE_COMMAND)
    if len(gate_steps) != 1:
        found.append(f"job ui-smoke has {len(gate_steps)} steps that run the gate entry, expected exactly one")
    for step in gate_steps:
        if step.get("if") is not None:
            found.append(f"the gate step is conditional: {step['if']}")
        if step.get("continue-on-error") in (True, "true"):
            found.append("the gate step is allowed to fail")
        # 039 slice A: a bound of its own, under the job's - a hung browser fails THIS step, named, and leaves the job
        # the time to upload the report.
        limit, job_limit = step.get("timeout-minutes"), smoke.get("timeout-minutes")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            found.append(f"the gate step has no timeout-minutes of its own: {limit!r}")
        elif not isinstance(job_limit, int) or limit >= job_limit:
            found.append(f"the gate step's timeout-minutes ({limit}) is not under the job's ({job_limit!r})")
    steps = [s for s in smoke.get("steps", []) if isinstance(s, dict)]
    reports = [
        i for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("actions/upload-artifact@") and s.get("with", {}).get("name") == "simulator-qa-stand-report"
    ]
    if len(reports) != 1 or steps[reports[0]].get("if") != "always()":
        found.append("job ui-smoke does not upload the report of the gate step on every run (one upload step, if: always())")
    elif gate_steps and reports[0] < steps.index(gate_steps[0]):
        found.append("job ui-smoke uploads the report of the gate step before the step has run")
    ports = [str(step.get("env", {}).get("PW_E2E_PORT")) for step in smoke.get("steps", []) if isinstance(step, dict) and "PW_E2E_PORT" in step.get("env", {})]
    if len(ports) != len(set(ports)):
        found.append(f"steps of ui-smoke share a Playwright port: {ports}")

    # 5. The scheduled job runs the whole stand and keeps its report.
    full = jobs.get("simulator-qa-stand", {})
    if full.get("if") != _SCHEDULE_ONLY_IF:
        found.append("job simulator-qa-stand is not the scheduled/manual job")
    if len(_runs(full, _FULL_COMMAND)) != 1:
        found.append("job simulator-qa-stand does not run the full entry exactly once")
    full_steps = [s for s in full.get("steps", []) if isinstance(s, dict)]
    uploads = [s for s in full_steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    if not any(s.get("if") == "always()" for s in uploads):
        found.append("job simulator-qa-stand does not upload the HTML report on every run")
    # 5a. 039 slice A (2026-10-12): the same job records the owner's videos, and the gallery (attachments of the stand's
    #     HTML report) and the videos leave it in ONE artifact. The directories are taken from the steps' own `env`: a
    #     Playwright run of the stand writes its report into `<output dir>-report` (`run-stand.mjs`, the video config).
    video_runs = _runs(full, _VIDEO_COMMAND)
    if len(video_runs) != 1:
        found.append(f"job simulator-qa-stand has {len(video_runs)} steps that record the owner's videos, expected exactly one")
    for step in video_runs:
        if step.get("continue-on-error") not in (None, False):
            found.append("the video step of simulator-qa-stand is allowed to fail")
    if len(uploads) != 1:
        found.append(f"job simulator-qa-stand uploads {len(uploads)} artifacts: the gallery and the videos must leave it in one")
    else:
        upload = uploads[0]
        paths = [line.strip() for line in str(upload.get("with", {}).get("path", "")).splitlines() if line.strip()]
        wanted = {
            "the stand's HTML report (the gallery)": [str(s.get("env", {}).get("GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR", "?")) + "-report" for s in _runs(full, _FULL_COMMAND)],
            "the HTML report of the video run": [str(s.get("env", {}).get("GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR", "?")) + "-report" for s in video_runs],
            "the raw videos": [str(s.get("env", {}).get("GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR", "?")) for s in video_runs],
        }
        for what, directories in wanted.items():
            for directory in directories:
                if directory not in paths:
                    found.append(f"the one artifact of simulator-qa-stand does not hold {what}: {directory} is not among {paths}")
        if len(set(paths)) != len(paths) or len({d for ds in wanted.values() for d in ds}) != 3:
            found.append(f"the stand run and the video run of simulator-qa-stand do not write into three different directories: {wanted}")
        if upload.get("if") != "always()":
            found.append("the one artifact of simulator-qa-stand is not uploaded on every run")
        if any(full_steps.index(step) > full_steps.index(upload) for step in video_runs + _runs(full, _FULL_COMMAND)):
            found.append("job simulator-qa-stand uploads its artifact before a step that fills it has run")
    full_ports = [str(s.get("env", {}).get("PW_E2E_PORT")) for s in full_steps if "PW_E2E_PORT" in s.get("env", {})]
    if len(full_ports) != len(set(full_ports)):
        found.append(f"steps of simulator-qa-stand share a Playwright port: {full_ports}")
    if scripts.get("test:e2e:qa:video") != "playwright test --config=e2e/qa/playwright.video.config.ts":
        found.append(f"script test:e2e:qa:video does not run the video config: {scripts.get('test:e2e:qa:video')!r}")
    video_config: str = inputs["video_config"]
    if not re.search(r"video:\s*\{\s*mode:\s*'on'", video_config) or "'**/*.qavideo.ts'" not in video_config:
        found.append("the video config does not record a video of every *.qavideo.ts test (video mode 'on')")
    for job_id, job in jobs.items():
        if not isinstance(job, dict) or job.get("if") == _SCHEDULE_ONLY_IF:
            continue
        for step in job.get("steps", []):
            run = str(step.get("run", "")) if isinstance(step, dict) else ""
            if re.search(r"qa:video|qavideo|playwright\.video\.config", run):
                found.append(f"job {job_id}, which runs on pull requests, records the owner's videos: {' '.join(run.split())}")
    # 6. The perf project (wall clock, a real-time fps threshold) never blocks a pull request.
    perf = jobs.get("simulator-qa-perf", {})
    if perf.get("if") != _SCHEDULE_ONLY_IF:
        found.append("job simulator-qa-perf is not the scheduled/manual job")
    perf_runs = [s for s in perf.get("steps", []) if isinstance(s, dict) and "test:e2e:qa:perf" in str(s.get("run", ""))]
    if len(perf_runs) != 2 or sum(1 for s in perf_runs if s.get("env", {}).get("QA_STAND_SERVER") == "preview") != 1:
        found.append("job simulator-qa-perf does not run the perf project once on the dev server and once on a production build")
    for job_id, job in jobs.items():
        if not isinstance(job, dict) or job.get("if") == _SCHEDULE_ONLY_IF:
            continue
        for step in job.get("steps", []):
            run = str(step.get("run", "")) if isinstance(step, dict) else ""
            if re.search(r"qa:perf|qa-perf|qaperf", run):
                found.append(f"job {job_id}, which runs on pull requests, runs the perf project: {' '.join(run.split())}")
    if "--project=qa-perf" in runner or ".qaperf" in runner:
        found.append("run-stand.mjs, the entry of the required step, selects the perf project")
    # 7. The counted project holds no wall-clock window (039 slice A, R14: the deterministic budgets moved in, the
    #    frame-time measurements must not follow them).
    counted: dict[str, str] = inputs["counted"]
    if len(counted) < 10:
        found.append(f"only {len(counted)} files of the counted project were found; the wall-clock scan would be over nothing")
    for name, text in counted.items():
        if re.search(r"perf-measure|\bmeasureWindow\s*\(|\bsummarizeWindow\s*\(", text):
            found.append(f"{name}, a file of the counted project, uses the wall-clock window of perf-measure.ts")
    return found


def test_the_stand_is_wired_into_the_required_gate() -> None:
    assert violations(_inputs()) == []


def _without_gate_step(i: dict) -> None:
    job = i["workflow"]["jobs"]["ui-smoke"]
    job["steps"] = [s for s in job["steps"] if s not in _runs(job, _GATE_COMMAND)]


def _gate_step(i: dict) -> dict:
    return _runs(i["workflow"]["jobs"]["ui-smoke"], _GATE_COMMAND)[0]


_MUTATIONS = {
    "the gate step is removed": (_without_gate_step, "expected exactly one"),
    "the gate step runs on schedule only": (lambda i: _gate_step(i).update({"if": "github.event_name == 'schedule'"}), "conditional"),
    "the gate step may fail": (lambda i: _gate_step(i).update({"continue-on-error": True}), "allowed to fail"),
    "the job is narrowed to the schedule": (lambda i: i["workflow"]["jobs"]["ui-smoke"].update({"if": _SCHEDULE_ONLY_IF}), "job ui-smoke is conditional"),
    "the step runs the plain stand script, uncounted": (
        lambda i: _gate_step(i).update({"run": "npm --prefix simulator-ui/v2 run test:e2e:qa"}), "expected exactly one"),
    "the plain stand script runs Playwright past the runner again": (
        lambda i: i["package"]["scripts"].update({"test:e2e:qa": "playwright test --config=e2e/playwright.qa.config.ts --project=qa-stand"}),
        "is not the debug entry of the runner"),
    "another script runs the counted project past the runner": (
        lambda i: i["package"]["scripts"].update({"test:e2e:qa:quick": "playwright test --config=e2e/playwright.qa.config.ts --project=qa-stand"}),
        "script test:e2e:qa:quick runs the counted project past the runner"),
    "a script runs the stand config with no project named": (
        lambda i: i["package"]["scripts"].update({"test:e2e:qa:all": "playwright test --config=e2e/playwright.qa.config.ts"}),
        "script test:e2e:qa:all runs the counted project past the runner"),
    "the debug run prints the line of a counted verdict": (
        lambda i: i.update(runner=i["runner"].replace("if (v.counted) console.log(", "if (true) console.log(")), "lost the mark of its debug scope"),
    "every scope is judged as a debug run": (
        lambda i: i.update(verdict=i["verdict"].replace("const counted = run.scope !== 'debug'", "const counted = false")), "lost its refusal"),
    "the gate script bypasses the counting entry": (
        lambda i: i["package"]["scripts"].update({"test:e2e:qa:gate": "playwright test --config=e2e/playwright.qa.config.ts --project=qa-stand"}),
        "is not the gate entry"),
    "the config turns test mode on": (lambda i: i.update(config=i["config"].replace("VITE_TEST_MODE: '0'", "VITE_TEST_MODE: '1'")), "test mode"),
    "the config announces WebDriver": (
        lambda i: i.update(config=i["config"].replace("--disable-blink-features=AutomationControlled", "")), "navigator.webdriver"),
    "the verdict stops comparing the count": (lambda i: i.update(verdict=i["verdict"].replace("counted && ran !== want", "false")), "lost its refusal"),
    "the verdict tolerates a skip": (lambda i: i.update(verdict=i["verdict"].replace("stats.skipped !== 0", "false")), "lost its refusal"),
    "the verdict tolerates a flaky test": (lambda i: i.update(verdict=i["verdict"].replace("stats.flaky !== 0", "false")), "lost its refusal"),
    "the verdict lets an expected failure into the gate": (
        lambda i: i.update(verdict=i["verdict"].replace("expectedFailures !== 0", "false")), "lost its refusal"),
    "the entry ignores the verdict": (lambda i: i.update(runner=i["runner"].replace("process.exit(v.code)", "process.exit(0)")), "lost its refusal"),
    "the config allows retries": (lambda i: i.update(config=i["config"].replace("retries: 0", "retries: 2")), "retries"),
    "the entry allows retries": (lambda i: i.update(runner=i["runner"].replace("--retries=0", "--retries=2")), "lost its refusal"),
    "the perf job runs on every pull request": (lambda i: i["workflow"]["jobs"]["simulator-qa-perf"].pop("if"), "runs the perf project"),
    "the required step also runs the perf project": (
        lambda i: _gate_step(i).update({"run": _GATE_COMMAND + " && npm --prefix simulator-ui/v2 run test:e2e:qa:perf"}), "runs the perf project"),
    "the perf job loses its production build": (
        lambda i: [s.get("env", {}).pop("QA_STAND_SERVER", None) for s in i["workflow"]["jobs"]["simulator-qa-perf"]["steps"]], "once on a production build"),
    "the gate report is uploaded only when the step failed": (
        lambda i: [s.update({"if": "failure()"}) for s in i["workflow"]["jobs"]["ui-smoke"]["steps"] if s.get("with", {}).get("name") == "simulator-qa-stand-report"],
        "does not upload the report of the gate step on every run"),
    "the gate step has no timeout of its own": (lambda i: _gate_step(i).pop("timeout-minutes"), "no timeout-minutes of its own"),
    "the gate step may run as long as the job": (
        lambda i: _gate_step(i).update({"timeout-minutes": i["workflow"]["jobs"]["ui-smoke"]["timeout-minutes"]}), "is not under the job's"),
    "a wall-clock window enters the counted project": (
        lambda i: i["counted"].update({"frame.qa.ts": "import { measureWindow } from './perf-measure.js'\n"}), "uses the wall-clock window"),
    "the scheduled job runs on every pull request": (lambda i: i["workflow"]["jobs"]["simulator-qa-stand"].pop("if"), "not the scheduled/manual job"),
    "the scheduled job no longer records the videos": (
        lambda i: i["workflow"]["jobs"]["simulator-qa-stand"].update(steps=[s for s in _full_steps(i) if s not in _runs(_full_job(i), _VIDEO_COMMAND)]),
        "0 steps that record the owner's videos"),
    "the video step may fail": (lambda i: _runs(_full_job(i), _VIDEO_COMMAND)[0].update({"continue-on-error": True}), "video step of simulator-qa-stand is allowed to fail"),
    "the videos leave the job in an artifact of their own": (
        lambda i: _full_steps(i).append({"uses": "actions/upload-artifact@v4", "if": "always()", "with": {"name": "videos", "path": "${{ runner.temp }}/simulator-qa-video"}}),
        "uploads 2 artifacts"),
    "the one artifact holds the gallery only": (
        lambda i: _full_upload(i)["with"].update(path="${{ runner.temp }}/simulator-qa-stand-report"), "does not hold the HTML report of the video run"),
    "the one artifact holds the videos only": (
        lambda i: _full_upload(i)["with"].update(path="${{ runner.temp }}/simulator-qa-video-report\n${{ runner.temp }}/simulator-qa-video\n"),
        "does not hold the stand's HTML report (the gallery)"),
    "the video run writes over the report of the stand": (
        lambda i: _runs(_full_job(i), _VIDEO_COMMAND)[0]["env"].update(GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR="${{ runner.temp }}/simulator-qa-stand"),
        "do not write into three different directories"),
    "the one artifact is uploaded only when the job is green": (lambda i: _full_upload(i).pop("if"), "is not uploaded on every run"),
    "the artifact is uploaded before the videos are recorded": (
        # The video step goes last, behind the upload.
        lambda i: _full_steps(i).append(_full_steps(i).pop(_full_steps(i).index(_runs(_full_job(i), _VIDEO_COMMAND)[0]))),
        "before a step that fills it has run"),
    "a pull-request job records the videos": (
        lambda i: i["workflow"]["jobs"]["ui-smoke"]["steps"].append({"run": _VIDEO_COMMAND}), "which runs on pull requests, records the owner's videos"),
    "the video config records only on failure": (
        lambda i: i.update(video_config=i["video_config"].replace("mode: 'on'", "mode: 'retain-on-failure'")), "does not record a video of every"),
}


def _full_job(i: dict) -> dict:
    return i["workflow"]["jobs"]["simulator-qa-stand"]


def _full_steps(i: dict) -> list:
    return _full_job(i)["steps"]


def _full_upload(i: dict) -> dict:
    return next(s for s in _full_steps(i) if str(s.get("uses", "")).startswith("actions/upload-artifact@"))


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_the_guard_notices_a_stand_that_left_the_gate(name: str) -> None:
    """COUNTER-CHECK. The control is the test above: on the committed files the finder is silent."""

    mutate, expected = _MUTATIONS[name]
    mutated = copy.deepcopy(_inputs())
    mutate(mutated)
    found = violations(mutated)
    assert any(expected in line for line in found), f"mutation '{name}' went unnoticed; findings: {found}"


def test_the_verdict_of_the_gate_entry_holds_on_made_up_runs() -> None:
    """The verdict is a pure function; this runs it on twenty-five made-up runs (a wrong count, a skip, a flaky test, an
    expected failure in the gate, no test at all, no result, malformed exclusions; since 2026-10-12 the debug scope -
    uncounted, and red on a failure, an empty filter, a skip, a flaky test - and a counted scope left without its
    number) through node.

    It does not run Playwright: that the JSON report of the installed Playwright has the assumed shape is shown only
    by a real run of the gate.
    """

    node = shutil.which("node")
    assert node, "node is not on PATH: the verdict of the QA stand gate cannot be exercised (this is not a pass)"
    done = subprocess.run([node, "e2e/qa/run-verdict.selfcheck.mjs"], cwd=_UI, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    summary = re.search(r"^(\d+) of (\d+) cases$", done.stdout, re.MULTILINE)
    assert summary and summary.group(1) == summary.group(2) and int(summary.group(2)) >= 25, done.stdout


def _stand_sources() -> list[Path]:
    return sorted((_UI / "e2e" / "qa").rglob("*.ts")) + [_UI / "e2e" / "playwright.qa.config.ts"]


def test_the_stand_never_waits_by_time_and_the_product_never_asks_whether_it_is_under_the_stand() -> None:
    """Two prohibitions of spec 038 (Verification plan, "forbidden"), as a named policy guard.

    It reads calls, not comments. It does not see a sleep spelled another way (`setTimeout` in a
    `page.evaluate`), nor a product branch that keys on something the stand happens to set.
    """

    sources = _stand_sources()
    assert len(sources) > 10, "the stand's sources were not found; the scan below would be over nothing"
    sleeping = [p.name for p in sources if re.search(r"\.waitForTimeout\s*\(", p.read_text(encoding="utf-8"))]
    assert sleeping == [], f"waitForTimeout in the QA stand: {sleeping}; wait for a condition instead"

    product = [p for p in (_UI / "src").rglob("*") if p.suffix in {".ts", ".vue"}]
    assert len(product) > 100, "the product sources were not found"
    branching = [str(p.relative_to(_UI)) for p in product if re.search(r"isQaStand|__qaSse|__qaRaf|QA_STAND", p.read_text(encoding="utf-8"))]
    assert branching == [], f"the product refers to the QA stand: {branching}"
