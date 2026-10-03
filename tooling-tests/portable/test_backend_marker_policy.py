"""Form guards for the backend tier markers (U-G-1) and the documented commands (U-G-3).

WHAT THIS IS: a limited check of the FORM of documented commands in the ACTIVE operational
documents, by regular expressions over the lines of shell fenced blocks. It is not a shell
interpreter and not proof that the documents are right: a command that passes here can still be
wrong, and what decides is running the command the document shows, as written.

WHAT IT DOES NOT SEE: prose and inline code outside fenced blocks; fences in other languages;
variables (`$taskSlug` is not expanded); here-strings, script blocks and control flow; documents
not in `_ACTIVE_DOCS` (the EN/PL trees are frozen translations, `docs/README.md`); a
`TEST_DATABASE_URL` that is not a quoted literal; the reset-before-runner rule binds only when the URL
and the runner share one fence; an unterminated fence flips what is read after it. Dropped with the
former parser (025 `T2502.3`): the venv create/install/tool ORDER and the createdb name <-> URL name
match (a mismatch cannot reset a foreign database: the URL must be `geov0_test_*`). The shape of the
`required-backend` job is owned by `tooling-tests/portable/test_p017_required_gate_runs_on_postgres.py`.
Every rule below has a planted-fragment counter-check, so a rule that stops matching goes red.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_ACTIVE_DOCS = (
    "README.md",
    "AGENTS.md",
    "docs/ru/06-contributing.md",
    "docs/ru/10-testing-framework.md",
    "docs/ru/runbook-dev-wsl2-docker-no-desktop.md",
    "docs/ru/testing/quick-start-and-debugging.md",
)
# Only this guide promises `.\.venv\Scripts\python.exe` per tool; README activates the venv first.
_VENV_RULE_DOCS = {"docs/ru/06-contributing.md"}
_SHELL_FENCES = {"", "powershell", "pwsh", "ps1", "bash", "sh", "shell", "console"}
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)")
_START = r"(?:^|[\s;&|(])"
_DIRECT_PYTEST = re.compile(
    _START + r"(?:[^\s;&|(]*[\\/])?(?:pytest|py\.test)(?:\.exe)?(?=$|[\s'\"])"
    r"|-m\s*['\"]?pytest(?:\.__main__)?(?=$|[\s'\"])"
)
_BARE_PYTHON_TOOL = re.compile(
    _START + r"(?:python3?|py)(?:\.exe)?(?:\s+-[\d.]+)?\s+-m\s*(?!venv\b)\w"
)
_RUNNER = re.compile(r"scripts[\\/]verify_local\.ps1")
_OPTION = re.compile(r"(?<![\w-])-([A-Za-z]\w*)")
_TEST_URL = re.compile(r"TEST_DATABASE_URL\s*=\s*['\"]([^'\"]*)['\"]")
_RESET_ON = re.compile(r"GEO_TEST_ALLOW_DB_RESET\s*=\s*['\"]1['\"]")


def _runner_parameters() -> set[str]:
    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    block = runner.split("param(", 1)[1].split("\n)", 1)[0]
    return {name.lower() for name in re.findall(r"\]\s*\$(\w+)", block)}


def _shell_blocks(text: str) -> list[list[tuple[int, str]]]:
    """Command lines per shell fence; a trailing ` or \\ joins the next line, `#` lines skipped."""
    blocks, current, fence, pending = [], None, None, ""
    for number, line in enumerate(text.splitlines(), 1):
        match = _FENCE.match(line)
        if match and fence is None:
            fence = match.group(1)
            current = [] if match.group(2).lower() in _SHELL_FENCES else None
        elif fence is not None and line.strip().startswith(fence):
            blocks += [] if current is None else [current]
            fence, current, pending = None, None, ""
        elif current is not None and not line.strip().startswith("#"):
            joined = f"{pending} {line.strip()}".strip()
            pending = joined[:-1] if joined.endswith(("`", "\\")) else ""
            if not pending:
                current.append((number, joined))
    return blocks


def _violations(text: str, parameters: set[str], venv_rule: bool = True) -> list[str]:
    found = []
    for block in _shell_blocks(text):
        url_at = reset_at = runner_at = 0
        for n, command in block:
            if _DIRECT_PYTEST.search(command):
                found.append(f"{n}: pytest outside scripts/verify_local.ps1: {command}")
            if venv_rule and _BARE_PYTHON_TOOL.search(command):
                found.append(f"{n}: Python tool outside the prepared .venv: {command}")
            if _RUNNER.search(command):
                runner_at = runner_at or n
                options = _OPTION.findall(_RUNNER.split(command, 1)[1])
                unknown = sorted(o for o in options if o.lower() not in parameters)
                if unknown:
                    found.append(f"{n}: the runner declares no {unknown}: {command}")
            url = _TEST_URL.search(command)
            if url:
                url_at = url_at or n
                if not re.search(r"/geov0_test_[\w$]+$", url.group(1)):
                    found.append(f"{n}: test URL is not a geov0_test_* database: {command}")
            reset_at = reset_at or (n if _RESET_ON.search(command) else 0)
        if url_at and runner_at and not (reset_at and max(url_at, reset_at) < runner_at):
            found.append(f"{url_at}: no GEO_TEST_ALLOW_DB_RESET=1 before the runner")
    return found


def test_active_docs_show_only_documented_command_forms() -> None:
    parameters = _runner_parameters()
    assert {"taskslug", "backendonly", "backendselector"} <= parameters  # param block was read
    violations, commands = [], []
    for name in _ACTIVE_DOCS:
        text = (_ROOT / name).read_text(encoding="utf-8")
        found = _violations(text, parameters, venv_rule=name in _VENV_RULE_DOCS)
        violations += [f"{name}:{v}" for v in found]
        own = [c for block in _shell_blocks(text) for _, c in block]
        # Anti-vacuum per document: each listed document still shows the runner in a fence it reads.
        assert any(_RUNNER.search(c) for c in own), f"{name}: no fenced runner command was read"
        commands += own
    assert violations == []
    # Anti-vacuum: the fences were read, and the URL rule had real examples to judge.
    assert sum(bool(_RUNNER.search(c)) for c in commands) >= len(_ACTIVE_DOCS)
    assert sum(bool(_TEST_URL.search(c)) for c in commands) >= 3


_URL = '$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_$slug"\n'
_RESET = '$env:GEO_TEST_ALLOW_DB_RESET = "1"\n'
_RUN = ".\\scripts\\verify_local.ps1 -TaskSlug $slug -BackendOnly `\n  -BackendSelector t.py\n"
_TOOL = ".\\.venv\\Scripts\\python.exe -m ruff check app migrations\n"


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        (_URL + _RESET + _RUN + "python -m pytest tests/unit -q\n", "pytest outside"),
        (_URL + _RESET + _RUN + "& .\\.venv\\Scripts\\pytest.exe -q\n", "pytest outside"),
        (_URL + _RESET + _RUN + "py -3.11 -mpytest -q\n", "pytest outside"),
        (_URL + _RESET + _RUN + "python -m ruff check app\n", "prepared .venv"),
        (_URL + _RESET + _RUN + ".\\scripts\\verify_local.ps1 -BackendMarker x\n", "declares no"),
        (_URL + _RESET + _RUN.replace("-BackendOnly", "-NoSuchSwitch"), "declares no"),
        (_URL.replace("geov0_test_$slug", "geov0") + _RESET + _RUN, "not a geov0_test_*"),
        (_URL + _RUN + _RESET, "no GEO_TEST_ALLOW_DB_RESET=1"),
        (_URL + _RUN, "no GEO_TEST_ALLOW_DB_RESET=1"),
    ],
)
def test_each_command_form_rule_goes_red_on_a_planted_fragment(body: str, rule: str) -> None:
    parameters = _runner_parameters()
    assert _violations(f"```powershell\n{_URL}{_RESET}{_RUN}{_TOOL}```\n", parameters) == []
    found = _violations(f"```powershell\n{body}{_TOOL}```\n", parameters)
    assert any(rule in v for v in found), found
    assert _violations(f"Run `{body}` by hand.\n", parameters) == []  # stated blind spot: prose


# U-G-1: with `--strict-markers` an unregistered marker cannot be applied (collection fails), so
# the obligation is: no empty tier is registered or filtered, and strict markers stay on.
def _empty_tier_violations(ini: str, runner: str, marker: str) -> list[str]:
    # The option line itself (an addopts continuation), not any mention: pytest.ini also names it in a comment.
    found = [] if re.search(r"(?m)^[ \t]+--strict-markers[ \t]*\r?$", ini) else ["pytest.ini lost --strict-markers"]
    if re.search(rf"(?m)^\s*{marker}(?:\([^)]*\))?\s*:", ini):
        found.append(f"pytest.ini registers {marker}")
    if f"not {marker}" in runner:
        found.append(f"verify_local.ps1 filters {marker}")
    if "'-m', 'not slow'" not in runner:  # anti-vacuum: the filter being checked was located
        found.append("verify_local.ps1 marker filter not found")
    return found


@pytest.mark.parametrize("marker", ["e2e", "scenario"])
def test_no_empty_backend_tier_is_registered_or_filtered(marker: str) -> None:
    ini = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    runner = (_ROOT / "scripts" / "verify_local.ps1").read_text(encoding="utf-8")
    assert _empty_tier_violations(ini, runner, marker) == []
    planted_ini = ini.replace("markers =\n", f"markers =\n    {marker}: planted\n", 1)
    # Keeps the `'-m', 'not slow'` anchor, so only the filter rule itself can fire.
    planted_runner = runner.replace("'-m', 'not slow'", f"'-m', 'not slow', 'and not {marker}'", 1)
    assert planted_ini != ini and planted_runner != runner
    assert _empty_tier_violations(planted_ini, runner, marker) != []
    assert _empty_tier_violations(ini, planted_runner, marker) == [f"verify_local.ps1 filters {marker}"]
    # Remove only the option line; the comment that mentions the flag stays, as in the real file.
    no_option = re.sub(r"(?m)^[ \t]+--strict-markers[ \t]*\r?\n", "", ini, count=1)
    assert no_option != ini and "--strict-markers" in no_option
    assert _empty_tier_violations(no_option, runner, marker) == ["pytest.ini lost --strict-markers"]
