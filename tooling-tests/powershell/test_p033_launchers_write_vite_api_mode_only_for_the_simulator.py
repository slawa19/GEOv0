"""033 A, item 6: `Update-EnvLocal` of the two launchers writes `VITE_API_MODE` only into the Simulator UI's file.

On `0c24f030` both launchers wrote `VITE_API_MODE=real` into the Admin UI's `.env.local` as well. The Admin UI reads no
such variable since 032 S4 removed its mock mode (`admin-ui/src/api/singleClient.guard.test.ts` forbids the name), so
the line was inert and misleading. The Simulator UI still reads it (`simulator-ui/v2/src/composables/useSimulatorApp.ts`),
so the counter-cases below must keep it there: a launcher that dropped the variable everywhere would pass the Admin
cases and break the simulator.

The function is lifted from each launcher by AST (as `test_run_full_stack_database_url_redaction.py` does) and run on a
file in a temporary directory. What this does not see: the whole launcher run - only `Update-EnvLocal` is executed,
and the call sites (`run_full_stack.ps1` passes `-IsSimulator $true` for the simulator's file only; `run_local.ps1`
configures the Admin UI alone) are read, not driven.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_FULL_STACK = _ROOT / "scripts" / "run_full_stack.ps1"
_RUN_LOCAL = _ROOT / "scripts" / "run_local.ps1"

_BASE = "http://127.0.0.1:18000"
_STALE = "# kept\nVITE_API_MODE=mock\nVITE_API_BASE_URL=http://old.test\nVITE_ADMIN_TOKEN=keep-me\n"


def _powershells() -> tuple[Path, ...]:
    candidates = [shutil.which(n) for n in ("pwsh", "pwsh.exe", "powershell", "powershell.exe")]
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidates.append(str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"))
    unique: dict[str, Path] = {}
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            unique[str(Path(candidate).resolve()).casefold()] = Path(candidate).resolve()
    return tuple(unique.values())


_POWERSHELLS = _powershells()
_IDS = [p.name + "-" + str(i) for i, p in enumerate(_POWERSHELLS)]

_COMMAND = r"""
$source = Get-Content -LiteralPath $env:P033_SCRIPT -Raw
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw ($parseErrors | ForEach-Object { $_.Message } | Out-String) }
$fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Update-EnvLocal' }, $true)
if ($null -eq $fn) { throw 'Update-EnvLocal not found' }
Invoke-Expression $fn.Extent.Text
if ($env:P033_SIMULATOR -eq '1') {
    Update-EnvLocal -Path $env:P033_ENV_FILE -BaseUrl $env:P033_BASE -IsSimulator $true
} else {
    Update-EnvLocal -Path $env:P033_ENV_FILE -BaseUrl $env:P033_BASE
}
"""


def _update(powershell: Path, script: Path, tmp_path: Path, *, simulator: bool, initial: str | None) -> list[str]:
    env_file = tmp_path / ".env.local"
    if initial is not None:
        env_file.write_text(initial, encoding="utf-8", newline="\n")
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _COMMAND],
        cwd=_ROOT,
        env={**os.environ, "P033_SCRIPT": str(script), "P033_ENV_FILE": str(env_file), "P033_BASE": _BASE,
             "P033_SIMULATOR": "1" if simulator else "0"},
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return env_file.read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_IDS)
@pytest.mark.parametrize("script", (_FULL_STACK, _RUN_LOCAL), ids=("run_full_stack", "run_local"))
@pytest.mark.parametrize("initial", (None, _STALE), ids=("no-file", "stale-file"))
def test_the_admin_ui_env_local_carries_no_vite_api_mode(powershell: Path, script: Path, initial, tmp_path) -> None:
    lines = _update(powershell, script, tmp_path, simulator=False, initial=initial)

    assert not [line for line in lines if line.strip().startswith("VITE_API_MODE")], lines
    assert lines.count(f"VITE_API_BASE_URL={_BASE}") == 1, lines
    if initial is not None:  # what the launcher does not own survives
        assert "VITE_ADMIN_TOKEN=keep-me" in lines and "# kept" in lines, lines
        assert "VITE_API_BASE_URL=http://old.test" not in lines, lines


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_IDS)
@pytest.mark.parametrize("initial", (None, _STALE), ids=("no-file", "stale-file"))
def test_the_simulator_ui_env_local_keeps_vite_api_mode_real(powershell: Path, initial, tmp_path) -> None:
    lines = _update(powershell, _FULL_STACK, tmp_path, simulator=True, initial=initial)

    assert lines.count("VITE_API_MODE=real") == 1, lines
    assert lines.count(f"VITE_API_BASE_URL={_BASE}") == 1, lines
    assert lines.count(f"VITE_GEO_BACKEND_ORIGIN={_BASE}") == 1, lines
