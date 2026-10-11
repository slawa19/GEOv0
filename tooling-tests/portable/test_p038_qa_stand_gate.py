"""038 S2: the Simulator UI QA stand is wired into the required gate, in the files' own words.

WHAT THIS PROVES. Four files agree: `simulator-ui/v2/package.json` (the scripts `test:e2e:qa*`),
`simulator-ui/v2/e2e/playwright.qa.config.ts` (the stand's Playwright config),
`simulator-ui/v2/e2e/qa/run-stand.mjs` (the gate entry that counts what ran) and
`.github/workflows/quality.yml`. Namely:

* the stand's scripts run THE STAND'S config and its `qa-stand` project, not the snapshot stand's;
* the stand's config does not put the product in test mode and clears `navigator.webdriver`: under
  either, sparks are not spawned, the render loop never sleeps, dragging and edge hover are off, and
  every result of the stand would be about another product (spec 038, F-038-1);
* the required job `ui-smoke` - every pull request, no condition - has a step that runs the gate
  entry, and that step is neither conditional nor allowed to fail;
* the gate entry compares the number of tests that ran with a recorded number and refuses a skip;
* the scheduled job runs the whole stand and uploads its HTML report;
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

Every check goes through `violations()` so that the counter-check at the bottom can feed it broken
inputs and see it complain (section 9, anti-vacuum).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_UI = _ROOT / "simulator-ui" / "v2"
_STAND_CONFIG = "e2e/playwright.qa.config.ts"
_GATE_COMMAND = "npm --prefix simulator-ui/v2 run test:e2e:qa:gate"
_FULL_COMMAND = "npm --prefix simulator-ui/v2 run test:e2e:qa:report"
_SCHEDULE_ONLY_IF = "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"


def _inputs() -> dict[str, Any]:
    return {
        "workflow": yaml.safe_load((_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")),
        "package": json.loads((_UI / "package.json").read_text(encoding="utf-8")),
        "config": (_UI / "e2e" / "playwright.qa.config.ts").read_text(encoding="utf-8"),
        "runner": (_UI / "e2e" / "qa" / "run-stand.mjs").read_text(encoding="utf-8"),
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
    script = scripts.get("test:e2e:qa", "")
    if f"--config={_STAND_CONFIG}" not in script:
        found.append(f"script test:e2e:qa does not run {_STAND_CONFIG}: {script!r}")
    if "--project=qa-stand" not in scripts.get("test:e2e:qa", ""):
        found.append("script test:e2e:qa does not select the qa-stand project")
    if scripts.get("test:e2e:qa:gate") != "node e2e/qa/run-stand.mjs gate":
        found.append(f"script test:e2e:qa:gate is not the gate entry: {scripts.get('test:e2e:qa:gate')!r}")
    if scripts.get("test:e2e:qa:report") != "node e2e/qa/run-stand.mjs full":
        found.append(f"script test:e2e:qa:report is not the full entry: {scripts.get('test:e2e:qa:report')!r}")
    for needle in (f"--config={_STAND_CONFIG}", "--project=qa-stand", "--grep-invert=@extended"):
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
    for needle in ("ran !== want", "stats.skipped !== 0", "process.exit(code)"):
        if needle not in runner:
            found.append(f"run-stand.mjs lost its refusal: {needle}")

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
    ports = [str(step.get("env", {}).get("PW_E2E_PORT")) for step in smoke.get("steps", []) if isinstance(step, dict) and "PW_E2E_PORT" in step.get("env", {})]
    if len(ports) != len(set(ports)):
        found.append(f"steps of ui-smoke share a Playwright port: {ports}")

    # 5. The scheduled job runs the whole stand and keeps its report.
    full = jobs.get("simulator-qa-stand", {})
    if full.get("if") != _SCHEDULE_ONLY_IF:
        found.append("job simulator-qa-stand is not the scheduled/manual job")
    if len(_runs(full, _FULL_COMMAND)) != 1:
        found.append("job simulator-qa-stand does not run the full entry exactly once")
    uploads = [s for s in full.get("steps", []) if isinstance(s, dict) and str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    if not any(s.get("if") == "always()" for s in uploads):
        found.append("job simulator-qa-stand does not upload the HTML report on every run")
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
    "the script runs the snapshot config": (
        lambda i: i["package"]["scripts"].update({"test:e2e:qa": "playwright test --project=qa-stand"}), "does not run e2e/playwright.qa.config.ts"),
    "the gate script bypasses the counting entry": (
        lambda i: i["package"]["scripts"].update({"test:e2e:qa:gate": "playwright test --config=e2e/playwright.qa.config.ts --project=qa-stand"}),
        "is not the gate entry"),
    "the config turns test mode on": (lambda i: i.update(config=i["config"].replace("VITE_TEST_MODE: '0'", "VITE_TEST_MODE: '1'")), "test mode"),
    "the config announces WebDriver": (
        lambda i: i.update(config=i["config"].replace("--disable-blink-features=AutomationControlled", "")), "navigator.webdriver"),
    "the entry stops comparing the count": (lambda i: i.update(runner=i["runner"].replace("ran !== want", "false")), "lost its refusal"),
    "the entry tolerates a skip": (lambda i: i.update(runner=i["runner"].replace("stats.skipped !== 0", "false")), "lost its refusal"),
    "the scheduled job runs on every pull request": (lambda i: i["workflow"]["jobs"]["simulator-qa-stand"].pop("if"), "not the scheduled/manual job"),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_the_guard_notices_a_stand_that_left_the_gate(name: str) -> None:
    """COUNTER-CHECK. The control is the test above: on the committed files the finder is silent."""

    mutate, expected = _MUTATIONS[name]
    mutated = copy.deepcopy(_inputs())
    mutate(mutated)
    found = violations(mutated)
    assert any(expected in line for line in found), f"mutation '{name}' went unnoticed; findings: {found}"


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
