from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_RUN_FULL_STACK = _ROOT / "scripts" / "run_full_stack.ps1"


def _powershell_executables() -> tuple[Path, ...]:
    candidates: list[str] = []
    for name in ("pwsh", "pwsh.exe", "powershell", "powershell.exe"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidates.append(
            str(
                Path(system_root)
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
        )

    unique: dict[str, Path] = {}
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            unique[str(path.resolve()).casefold()] = path.resolve()
    return tuple(unique.values())


_POWERSHELLS = _powershell_executables()
_POWERSHELL_IDS = [
    path.name + "-" + str(index) for index, path in enumerate(_POWERSHELLS)
]


def _run_powershell(
    executable: Path,
    command: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PHASE7_SCRIPT_PATH": str(_RUN_FULL_STACK),
        **(extra_env or {}),
    }
    result = subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{executable} failed with exit code {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


_AST_SETUP = r"""
$source = Get-Content -LiteralPath $env:PHASE7_SCRIPT_PATH -Raw
$tokens = $null
$parseErrors = $null
$scriptAst = [System.Management.Automation.Language.Parser]::ParseInput(
    $source,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw ($parseErrors | ForEach-Object { $_.Message } | Out-String)
}
function Get-LauncherFunctionText {
    param([string]$Name)
    $functionAst = $scriptAst.Find(
        {
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq $Name
        },
        $true
    )
    if ($null -eq $functionAst) {
        throw "Launcher function not found: $Name"
    }
    return $functionAst.Extent.Text
}
"""


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_database_display_value_excludes_userinfo_and_query_secrets(
    powershell: Path,
) -> None:
    sentinel_password = "phase7-sentinel-password"
    sentinel_query_secret = "phase7-sentinel-query-secret"
    raw_url = (
        "postgresql+asyncpg://phase7-user:"
        f"{sentinel_password}@db.example.test:5432/geov0"
        f"?sslmode=require&access_token={sentinel_query_secret}"
    )
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-SafeDatabaseDisplayUrl')
$display = Get-SafeDatabaseDisplayUrl -DatabaseUrl $env:PHASE7_DATABASE_URL
[Console]::Out.Write($display)
"""
    )

    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_DATABASE_URL": raw_url},
    )

    assert result.stdout == "postgresql+asyncpg://db.example.test:5432/geov0"
    assert "phase7-user" not in result.stdout
    assert sentinel_password not in result.stdout
    assert sentinel_query_secret not in result.stdout
    assert "access_token" not in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_database_display_value_handles_sqlite_without_native_arguments(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-SafeDatabaseDisplayUrl')
$display = Get-SafeDatabaseDisplayUrl `
    -DatabaseUrl 'sqlite+aiosqlite:///./.local-run/geov0.db?token=must-not-print'
[Console]::Out.Write($display)
"""
    )

    result = _run_powershell(powershell, command)

    assert result.stdout == "sqlite+aiosqlite:///./.local-run/geov0.db"
    assert "must-not-print" not in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_env_overrides_never_print_values_or_invalid_raw_entries(
    powershell: Path,
) -> None:
    secret_url = (
        "postgresql+asyncpg://operator:phase7-private@db.test/geov0?token=hidden"
    )
    invalid_secret = "phase7-invalid-entry-must-stay-secret"
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Set-EnvOverrides')
Set-EnvOverrides -Pairs @(
    "DATABASE_URL=$env:PHASE7_SECRET_URL",
    'SIMULATOR_REAL_ENABLE_INJECT=0'
)
if ($env:DATABASE_URL -ne $env:PHASE7_SECRET_URL) {
    throw 'DATABASE_URL override was not applied'
}
try {
    Set-EnvOverrides -Pairs @($env:PHASE7_INVALID_SECRET)
    throw 'Invalid override unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Invalid override unexpectedly succeeded') { throw }
}
[Console]::Out.Write('override-redaction-ok')
"""
    )

    result = _run_powershell(
        powershell,
        command,
        extra_env={
            "PHASE7_SECRET_URL": secret_url,
            "PHASE7_INVALID_SECRET": invalid_secret,
        },
    )
    combined_output = result.stdout + result.stderr

    assert "override-redaction-ok" in result.stdout
    assert secret_url not in combined_output
    assert "phase7-private" not in combined_output
    assert "token=hidden" not in combined_output
    assert invalid_secret not in combined_output


def test_env_override_write_host_has_no_raw_value_expression() -> None:
    source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")
    start = source.index("function Set-EnvOverrides")
    end = source.index("# --- Configuration & Paths ---", start)
    function_source = source[start:end]

    write_host_lines = [
        line for line in function_source.splitlines() if "Write-Host" in line
    ]
    assert write_host_lines
    for line in write_host_lines:
        assert "$val" not in line
        assert "$p" not in line
        assert "$pair" not in line
        assert "$Pairs" not in line
        assert "DATABASE_URL" not in line


def test_full_stack_summary_uses_only_the_safe_database_display_value() -> None:
    source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")

    assert 'Write-Host "$SafeDatabaseDisplayUrl"' in source
    assert 'Write-Host "$EffectiveDatabaseUrl"' not in source


def test_database_and_ownership_preflight_precede_first_main_stop() -> None:
    source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")
    main = source[source.index("# --- Main Logic ---") :]

    stop_index = main.index("Stop-AllServices -Services $Services -FailOnConflict")
    assert main.index("Get-EffectiveDatabaseUrl -PythonExe $Python") < stop_index
    assert (
        main.index("Get-SafeDatabaseDisplayUrl -DatabaseUrl $EffectiveDatabaseUrl")
        < stop_index
    )
    assert (
        main.index("if ($ResetDb -and $EffectiveDatabaseUrl -ne $DefaultDatabaseUrl)")
        < stop_index
    )
    assert (
        main.index("Assert-ServiceOwnershipForReplacement -Services $Services")
        < stop_index
    )


_OWNERSHIP_FUNCTIONS = (
    "Get-ServiceStopState",
    "Get-ServiceStopPlan",
    "Stop-AllServices",
    "Assert-ServiceOwnershipForReplacement",
)


def _ownership_command(body: str) -> str:
    imports = "\n".join(
        f"Invoke-Expression (Get-LauncherFunctionText -Name '{name}')"
        for name in _OWNERSHIP_FUNCTIONS
    )
    return (
        _AST_SETUP
        + imports
        + r"""
$script:PidByFile = @{}
$script:ListenerByPort = @{}
$script:ExistingPids = @{}
$script:ListenerCalls = @{}
$script:StoppedPids = @()
$script:RemovedPidFiles = @()

function Get-PidFromFile {
    param([string]$Path)
    return $script:PidByFile[$Path]
}
function Get-ListeningPid {
    param([int]$Port)
    $value = $script:ListenerByPort[$Port]
    if ($value -is [System.Array]) {
        $call = [int]$script:ListenerCalls[$Port]
        $script:ListenerCalls[$Port] = $call + 1
        if ($call -ge $value.Count) { return $value[$value.Count - 1] }
        return $value[$call]
    }
    return $value
}
function Test-ProcessExists {
    param([int]$Id)
    return [bool]$script:ExistingPids[$Id]
}
function Stop-ProcessById {
    param([int]$Id)
    $script:StoppedPids += $Id
}
function Remove-ServicePidFile {
    param([string]$Path)
    $script:RemovedPidFiles += $Path
}
function New-TestService {
    param([string]$Name, [int]$Port, [string]$PidFile)
    return [pscustomobject]@{ Name = $Name; Port = $Port; PidFile = $PidFile }
}
"""
        + body
    )


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    ("saved_pid", "listener_pid", "saved_exists"),
    (
        (None, 202, False),
        (101, 202, True),
        (101, None, True),
        (101, 101, False),
    ),
    ids=("foreign", "mismatched", "missing-listener", "stale-owner"),
)
def test_unproven_service_ownership_never_stops_a_process(
    powershell: Path,
    saved_pid: int | None,
    listener_pid: int | None,
    saved_exists: bool,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
if ($env:PHASE7_SAVED_PID) {
    $saved = [int]$env:PHASE7_SAVED_PID
    $script:PidByFile['backend.pid'] = $saved
    $script:ExistingPids[$saved] = $env:PHASE7_SAVED_EXISTS -eq '1'
}
if ($env:PHASE7_LISTENER_PID) {
    $script:ListenerByPort[18000] = [int]$env:PHASE7_LISTENER_PID
}
$result = Stop-AllServices -Services @($service)
if ($result) { throw 'Unproven ownership was reported as stopped' }
if ($script:StoppedPids.Count -ne 0) { throw 'An unowned process was stopped' }
[Console]::Out.Write('unowned-safe')
"""
    result = _run_powershell(
        powershell,
        _ownership_command(body),
        extra_env={
            "PHASE7_SAVED_PID": "" if saved_pid is None else str(saved_pid),
            "PHASE7_LISTENER_PID": "" if listener_pid is None else str(listener_pid),
            "PHASE7_SAVED_EXISTS": "1" if saved_exists else "0",
        },
    )

    assert "unowned-safe" in result.stdout
    assert "not stopped" in result.stdout or "not proven" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_matching_pid_file_and_listener_stops_only_owned_process(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$script:PidByFile['backend.pid'] = 101
$script:ListenerByPort[18000] = 101
$script:ExistingPids[101] = $true
$result = Stop-AllServices -Services @($service)
if (-not $result) { throw 'Owned process was not reported as stopped' }
if ($script:StoppedPids.Count -ne 1 -or $script:StoppedPids[0] -ne 101) {
    throw 'Unexpected stopped PID set'
}
if ($script:RemovedPidFiles.Count -ne 1 -or $script:RemovedPidFiles[0] -ne 'backend.pid') {
    throw 'Owned PID file was not removed'
}
[Console]::Out.Write('owned-stop-ok')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "owned-stop-ok" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_fail_on_conflict_checks_all_services_before_any_stop(
    powershell: Path,
) -> None:
    body = r"""
$owned = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$foreign = New-TestService -Name 'Admin UI' -Port 5173 -PidFile 'admin.pid'
$script:PidByFile['backend.pid'] = 101
$script:ExistingPids[101] = $true
$script:ListenerByPort[18000] = 101
$script:ListenerByPort[5173] = 202
try {
    Stop-AllServices -Services @($owned, $foreign) -FailOnConflict
    throw 'Ownership conflict unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Ownership conflict unexpectedly succeeded') { throw }
}
if ($script:StoppedPids.Count -ne 0) { throw 'Owned process stopped before conflict rejection' }
[Console]::Out.Write('preflight-before-stop-ok')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "preflight-before-stop-ok" in result.stdout
    assert "not stopped" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_listener_change_between_plan_and_stop_is_never_killed(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$script:PidByFile['backend.pid'] = 101
$script:ExistingPids[101] = $true
$script:ListenerByPort[18000] = @(101, 202)
try {
    Stop-AllServices -Services @($service) -FailOnConflict
    throw 'Listener race unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Listener race unexpectedly succeeded') { throw }
}
if ($script:StoppedPids.Count -ne 0) { throw 'Listener race stopped a process' }
[Console]::Out.Write('listener-race-safe')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "listener-race-safe" in result.stdout
    assert "ownership changed" in result.stdout
