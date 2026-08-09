from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_RUN_FULL_STACK = _ROOT / "scripts" / "run_full_stack.ps1"
_RUN_LOCAL = _ROOT / "scripts" / "run_local.ps1"
_RUN_REAL_SIMULATOR = _ROOT / "scripts" / "run_real_simulator.ps1"


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
@pytest.mark.parametrize(
    "reserved", ("/", "?", "#"), ids=("slash", "query", "fragment")
)
def test_database_display_rejects_ambiguous_raw_reserved_credentials_without_leak(
    powershell: Path,
    reserved: str,
) -> None:
    secret = f"phase7-before{reserved}phase7-after"
    raw_url = f"postgresql+asyncpg://operator:{secret}@db.test/geov0"
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-SafeDatabaseDisplayUrl')
try {
    Get-SafeDatabaseDisplayUrl -DatabaseUrl $env:PHASE7_DATABASE_URL
    throw 'Ambiguous URL unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Ambiguous URL unexpectedly succeeded') { throw }
    [Console]::Out.Write($_.Exception.Message)
}
"""
    )

    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_DATABASE_URL": raw_url},
    )
    combined_output = result.stdout + result.stderr

    assert result.stdout == "Unable to render a safe DATABASE_URL summary."
    assert raw_url not in combined_output
    assert "phase7-before" not in combined_output
    assert "phase7-after" not in combined_output


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    "raw_url",
    (
        "not-a-url-phase7-secret",
        "postgresql+asyncpg://",
        "1bad://phase7-secret",
        "postgresql+asyncpg://operator:phase7-secret/path",
    ),
)
def test_database_display_rejects_malformed_urls_with_generic_error(
    powershell: Path,
    raw_url: str,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-SafeDatabaseDisplayUrl')
try {
    Get-SafeDatabaseDisplayUrl -DatabaseUrl $env:PHASE7_DATABASE_URL
    throw 'Malformed URL unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Malformed URL unexpectedly succeeded') { throw }
    [Console]::Out.Write($_.Exception.Message)
}
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_DATABASE_URL": raw_url},
    )

    assert result.stdout == "Unable to render a safe DATABASE_URL summary."
    assert raw_url not in result.stderr


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
    main = source[source.index("# --- START action ---") :]

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
    "Test-ServiceStopPlansEqual",
    "Remove-StaleServiceOwnershipMetadata",
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
$script:MetadataByFile = @{}
$script:ListenerByPort = @{}
$script:FingerprintByPid = @{}
$script:IdentityStatusByPid = @{}
$script:ListenerCalls = @{}
$script:StoppedPids = @()
$script:RemovedPidFiles = @()
$script:FailStopPid = 0

function Get-ServiceOwnershipMetadata {
    param([string]$Path)
    return $script:MetadataByFile[$Path]
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
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $fingerprint = $script:FingerprintByPid[$Id]
    $status = $script:IdentityStatusByPid[$Id]
    if (-not $status) {
        if (-not $fingerprint) { $status = 'Missing' }
        elseif ($fingerprint -eq $ExpectedStartFingerprint) { $status = 'Exact' }
        else { $status = 'Mismatch' }
    }
    return [pscustomobject]@{ Status = $status; Fingerprint = $fingerprint }
}
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    if ($Id -eq $script:FailStopPid) { throw 'simulated stop failure' }
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
function New-TestMetadata {
    param(
        [int]$Id,
        [string]$StartTime,
        [string]$ServiceName = 'Backend',
        [int]$Port = 18000
    )
    return [pscustomobject]@{
        Valid = $true
        Pid = $Id
        ProcessStartFingerprint = $StartTime
        ServiceName = $ServiceName
        Port = $Port
    }
}
"""
        + body
    )


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    ("saved_pid", "listener_pid", "saved_fingerprint", "current_fingerprint"),
    (
        (None, 202, None, None),
        (101, 202, "start-a", "start-a"),
        (101, None, "start-a", "start-a"),
        (101, 101, "start-a", "start-b"),
    ),
    ids=(
        "foreign",
        "mismatched-listener",
        "owned-not-listening",
        "pid-reused-listener",
    ),
)
def test_unproven_service_ownership_never_stops_a_process(
    powershell: Path,
    saved_pid: int | None,
    listener_pid: int | None,
    saved_fingerprint: str | None,
    current_fingerprint: str | None,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
if ($env:PHASE7_SAVED_PID) {
    $saved = [int]$env:PHASE7_SAVED_PID
    $script:MetadataByFile['backend.pid'] = New-TestMetadata `
        -Id $saved `
        -StartTime $env:PHASE7_SAVED_FINGERPRINT
    $script:FingerprintByPid[$saved] = $env:PHASE7_CURRENT_FINGERPRINT
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
            "PHASE7_SAVED_FINGERPRINT": saved_fingerprint or "",
            "PHASE7_CURRENT_FINGERPRINT": current_fingerprint or "",
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
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a'
$script:ListenerByPort[18000] = 101
$script:FingerprintByPid[101] = 'start-a'
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
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a'
$script:FingerprintByPid[101] = 'start-a'
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
def test_pid_reuse_without_listener_cleans_only_stale_metadata(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'old-start'
$script:FingerprintByPid[101] = 'reused-start'
$result = Stop-AllServices -Services @($service) -FailOnConflict
if (-not $result) { throw 'Safe stale metadata cleanup failed' }
if ($script:StoppedPids.Count -ne 0) { throw 'Reused PID was stopped' }
if ($script:RemovedPidFiles.Count -ne 1 -or $script:RemovedPidFiles[0] -ne 'backend.pid') {
    throw 'Stale metadata was not removed'
}
[Console]::Out.Write('pid-reuse-cleanup-safe')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "pid-reuse-cleanup-safe" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_unreadable_process_identity_is_a_conflict_and_retains_metadata(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a'
$script:IdentityStatusByPid[101] = 'Unreadable'
try {
    Stop-AllServices -Services @($service) -FailOnConflict
    throw 'Unreadable identity unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Unreadable identity unexpectedly succeeded') { throw }
}
if ($script:StoppedPids.Count -ne 0) { throw 'Unreadable process identity was stopped' }
if ($script:RemovedPidFiles.Count -ne 0) { throw 'Unreadable ownership metadata was removed' }
[Console]::Out.Write('unreadable-identity-fail-closed')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "unreadable-identity-fail-closed" in result.stdout
    assert "identity is unreadable" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_exact_live_non_listener_has_an_actionable_conflict_reason(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a'
$script:FingerprintByPid[101] = 'start-a'
$state = Get-ServiceStopState -Service $service
if (-not $state.Conflict) { throw 'Exact live non-listener was not a conflict' }
if ($state.Reason -notlike '*alive but is not listening on port 18000*') {
    throw "Unexpected conflict reason: $($state.Reason)"
}
[Console]::Out.Write($state.Reason)
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "owned PID 101 is alive but is not listening on port 18000" in result.stdout


def test_status_displays_the_exact_conflict_reason() -> None:
    source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")

    assert '$status = "Conflict: $($state.Reason)"' in source


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_stop_failure_propagates_and_retains_all_ownership_files(
    powershell: Path,
) -> None:
    body = r"""
$backend = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$admin = New-TestService -Name 'Admin UI' -Port 5173 -PidFile 'admin.pid'
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a'
$script:MetadataByFile['admin.pid'] = New-TestMetadata -Id 202 -StartTime 'start-b' -ServiceName 'Admin UI' -Port 5173
$script:FingerprintByPid[101] = 'start-a'
$script:FingerprintByPid[202] = 'start-b'
$script:ListenerByPort[18000] = 101
$script:ListenerByPort[5173] = 202
$script:FailStopPid = 202
try {
    Stop-AllServices -Services @($backend, $admin) -FailOnConflict
    throw 'Stop failure unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Stop failure unexpectedly succeeded') { throw }
    if ($_.Exception.Message -ne 'simulated stop failure') { throw }
}
if ($script:StoppedPids.Count -ne 1 -or $script:StoppedPids[0] -ne 101) {
    throw 'Unexpected stop sequence before failure'
}
if ($script:RemovedPidFiles.Count -ne 0) { throw 'Ownership metadata removed after stop failure' }
[Console]::Out.Write('stop-failure-retained')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "stop-failure-retained" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_multi_service_late_conflict_is_detected_before_any_stop(
    powershell: Path,
) -> None:
    body = r"""
$backend = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$admin = New-TestService -Name 'Admin UI' -Port 5173 -PidFile 'admin.pid'
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a'
$script:MetadataByFile['admin.pid'] = New-TestMetadata -Id 202 -StartTime 'start-b' -ServiceName 'Admin UI' -Port 5173
$script:FingerprintByPid[101] = 'start-a'
$script:FingerprintByPid[202] = 'start-b'
$script:ListenerByPort[18000] = @(101, 101)
$script:ListenerByPort[5173] = @(202, 303)
try {
    Stop-AllServices -Services @($backend, $admin) -FailOnConflict
    throw 'Late multi-service conflict unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Late multi-service conflict unexpectedly succeeded') { throw }
}
if ($script:StoppedPids.Count -ne 0) { throw 'A service stopped before collective final preflight completed' }
[Console]::Out.Write('late-conflict-preflight-safe')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "late-conflict-preflight-safe" in result.stdout
    assert "final preflight" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_startup_never_adopts_a_listener_other_than_launched_process(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForLaunchedServiceOwnership')
$script:OwnershipWrites = @()
$script:StoppedPids = @()
function Get-ProcessStartTimeFingerprint { param([int]$Id) return 'start-a' }
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'start-a' }
}
function Get-ListeningPid { param([int]$Port) return 202 }
function Write-ServiceOwnershipMetadata {
    param([object]$Service, [int]$Id, [string]$ProcessStartFingerprint)
    $script:OwnershipWrites += $Id
}
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    if ($ExpectedStartFingerprint -ne 'start-a') { throw 'Wrong cleanup fingerprint' }
    $script:StoppedPids += $Id
}
function Start-Sleep { param([int]$Milliseconds) }
$service = [pscustomobject]@{ Name = 'Backend'; Port = 18000; PidFile = 'backend.pid' }
$process = [pscustomobject]@{ Id = 101 }
try {
    Wait-ForLaunchedServiceOwnership -Service $service -Process $process -TimeoutSec 1
    throw 'Foreign listener was adopted'
} catch {
    if ($_.Exception.Message -eq 'Foreign listener was adopted') { throw }
}
if ($script:OwnershipWrites.Count -ne 0) { throw 'Foreign listener ownership was persisted' }
if ($script:StoppedPids.Count -ne 1 -or $script:StoppedPids[0] -ne 101) {
    throw 'Exact launched process was not cleaned up'
}
[Console]::Out.Write('startup-non-adoption-safe')
"""
    )
    result = _run_powershell(powershell, command)

    assert "startup-non-adoption-safe" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_startup_timeout_cleans_up_exact_launched_process(powershell: Path) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForLaunchedServiceOwnership')
$script:StoppedPids = @()
function Get-ProcessStartTimeFingerprint { param([int]$Id) return 'start-a' }
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'start-a' }
}
function Get-ListeningPid { param([int]$Port) return $null }
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    if ($ExpectedStartFingerprint -ne 'start-a') { throw 'Wrong cleanup fingerprint' }
    $script:StoppedPids += $Id
}
$service = [pscustomobject]@{ Name = 'Backend'; Port = 18000; PidFile = 'backend.pid' }
$process = [pscustomobject]@{ Id = 101 }
try {
    Wait-ForLaunchedServiceOwnership -Service $service -Process $process -TimeoutSec 0
    throw 'Startup timeout unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Startup timeout unexpectedly succeeded') { throw }
    if ($_.Exception.Message -notlike '*did not listen*') { throw }
}
if ($script:StoppedPids.Count -ne 1 -or $script:StoppedPids[0] -ne 101) {
    throw 'Timed-out launched process was not cleaned up'
}
[Console]::Out.Write('startup-timeout-cleanup-safe')
"""
    )
    result = _run_powershell(powershell, command)

    assert result.stdout == "startup-timeout-cleanup-safe"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_startup_child_that_already_exited_is_not_a_cleanup_failure(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForLaunchedServiceOwnership')
function Get-ProcessStartTimeFingerprint { param([int]$Id) return $null }
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Missing'; Fingerprint = $null }
}
function Get-ListeningPid { param([int]$Port) return $null }
function Stop-ProcessById { throw 'Already-exited child was stopped' }
$service = [pscustomobject]@{ Name = 'Backend'; Port = 18000; PidFile = 'backend.pid' }
$process = [pscustomobject]@{ Id = 101 }
try {
    Wait-ForLaunchedServiceOwnership -Service $service -Process $process -TimeoutSec 0
    throw 'Startup timeout unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Startup timeout unexpectedly succeeded') { throw }
    if ($_.Exception.Message -notlike '*identity is unavailable*') { throw }
    if ($_.Exception.Message -like '*Startup cleanup also failed*') {
        throw 'Already-exited child was reported as a cleanup failure'
    }
}
[Console]::Out.Write('already-exited-child-cleanup-ok')
"""
    )
    result = _run_powershell(powershell, command)

    assert result.stdout == "already-exited-child-cleanup-ok"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_startup_cleanup_failure_preserves_primary_and_cleanup_evidence(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForLaunchedServiceOwnership')
function Get-ProcessStartTimeFingerprint { param([int]$Id) return 'start-a' }
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'start-a' }
}
function Get-ListeningPid { param([int]$Port) return 202 }
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    throw 'cleanup-stop-failed'
}
$service = [pscustomobject]@{ Name = 'Backend'; Port = 18000; PidFile = 'backend.pid' }
$process = [pscustomobject]@{ Id = 101 }
try {
    Wait-ForLaunchedServiceOwnership -Service $service -Process $process -TimeoutSec 1
    throw 'Cleanup failure unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Cleanup failure unexpectedly succeeded') { throw }
    if ($_.Exception.Message -notlike 'Unable to start Backend: port 18000 is owned by a different process.*') {
        throw 'Primary startup failure was masked'
    }
    if ($_.Exception.Message -notlike '*Startup cleanup also failed: cleanup-stop-failed*') {
        throw 'Cleanup failure evidence was lost'
    }
    if ($null -eq $_.Exception.InnerException -or
        $_.Exception.InnerException.Message -notlike '*owned by a different process*') {
        throw 'Primary failure was not preserved as the inner exception'
    }
    if ([string]$_.Exception.Data['CleanupFailure'] -notlike '*cleanup-stop-failed*') {
        throw 'Cleanup failure detail was not preserved'
    }
}
[Console]::Out.Write('startup-cleanup-failure-evidence-ok')
"""
    )
    result = _run_powershell(powershell, command)

    assert result.stdout == "startup-cleanup-failure-evidence-ok"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_successful_startup_persists_ownership_without_cleanup(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForLaunchedServiceOwnership')
$script:OwnershipWrites = @()
function Get-ProcessStartTimeFingerprint { param([int]$Id) return 'start-a' }
function Get-ListeningPid { param([int]$Port) return 101 }
function Write-ServiceOwnershipMetadata {
    param([object]$Service, [int]$Id, [string]$ProcessStartFingerprint)
    $script:OwnershipWrites += "$Id|$ProcessStartFingerprint"
}
function Stop-ProcessById { throw 'Successful startup attempted cleanup' }
$service = [pscustomobject]@{ Name = 'Backend'; Port = 18000; PidFile = 'backend.pid' }
$process = [pscustomobject]@{ Id = 101 }
$ownership = Wait-ForLaunchedServiceOwnership -Service $service -Process $process -TimeoutSec 1
if ($ownership.Pid -ne 101 -or $ownership.ProcessStartFingerprint -ne 'start-a') {
    throw 'Successful ownership result changed'
}
if ($script:OwnershipWrites.Count -ne 1 -or $script:OwnershipWrites[0] -ne '101|start-a') {
    throw 'Successful ownership was not persisted exactly once'
}
[Console]::Out.Write('startup-success-unchanged')
"""
    )
    result = _run_powershell(powershell, command)

    assert result.stdout == "startup-success-unchanged"


def test_launcher_uses_direct_pass_thru_processes_for_all_listeners() -> None:
    source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")

    assert source.count("-PassThru") == 3
    assert source.count("-FilePath $Tools.Node") == 2
    assert "Start-Process -FilePath 'cmd.exe'" not in source
    assert "Write-ServiceOwnershipMetadata -Service $Service" in source


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_versioned_ownership_metadata_round_trip(
    powershell: Path,
    tmp_path: Path,
) -> None:
    ownership_path = tmp_path / "backend.pid"
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-ServiceOwnershipMetadata')
Invoke-Expression (Get-LauncherFunctionText -Name 'Write-ServiceOwnershipMetadata')
$service = [pscustomobject]@{
    Name = 'Backend'
    Port = 18000
    PidFile = $env:PHASE7_OWNERSHIP_PATH
}
Write-ServiceOwnershipMetadata -Service $service -Id 101 -ProcessStartFingerprint 'utc-ticks:638903664000000000'
$metadata = Get-ServiceOwnershipMetadata -Path $service.PidFile
if (-not $metadata.Valid -or $metadata.Pid -ne 101) { throw 'Ownership metadata did not round-trip' }
if ($metadata.ServiceName -ne 'Backend' -or $metadata.Port -ne 18000) { throw 'Service binding was lost' }
if ($metadata.ProcessStartFingerprint -ne 'utc-ticks:638903664000000000') { throw 'Fingerprint was lost' }
[Console]::Out.Write('ownership-round-trip-ok')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_OWNERSHIP_PATH": str(ownership_path)},
    )

    assert result.stdout == "ownership-round-trip-ok"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_native_stop_failure_is_not_swallowed(powershell: Path) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Stop-ProcessById')
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'start-a' }
}
function Stop-Process { param([int]$Id, [switch]$Force, [object]$ErrorAction); throw 'native-stop-failed' }
try {
    Stop-ProcessById -Id 101 -ExpectedStartFingerprint 'start-a'
    throw 'Native stop failure unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Native stop failure unexpectedly succeeded') { throw }
    if ($_.Exception.Message -ne 'native-stop-failed') { throw }
}
[Console]::Out.Write('native-stop-failure-propagated')
"""
    )
    result = _run_powershell(powershell, command)

    assert result.stdout == "native-stop-failure-propagated"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_unreadable_identity_after_native_stop_is_not_reported_as_stopped(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Stop-ProcessById')
$script:IdentityCalls = 0
$script:DateCalls = 0
$script:NativeStopCalls = 0
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:IdentityCalls += 1
    if ($script:IdentityCalls -eq 1) {
        return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'start-a' }
    }
    return [pscustomobject]@{ Status = 'Unreadable'; Fingerprint = $null }
}
function Stop-Process {
    param([int]$Id, [switch]$Force, [object]$ErrorAction)
    $script:NativeStopCalls += 1
}
function Get-Date {
    $script:DateCalls += 1
    $origin = [datetime]'2026-08-09T00:00:00Z'
    if ($script:DateCalls -le 2) { return $origin }
    return $origin.AddSeconds(6)
}
function Start-Sleep { param([int]$Milliseconds) }
try {
    Stop-ProcessById -Id 101 -ExpectedStartFingerprint 'start-a'
    throw 'Unreadable post-stop identity was reported as stopped'
} catch {
    if ($_.Exception.Message -eq 'Unreadable post-stop identity was reported as stopped') { throw }
    if ($_.Exception.Message -notlike '*did not stop*') { throw }
}
if ($script:NativeStopCalls -ne 1) { throw 'Native stop was not invoked exactly once' }
if ($script:IdentityCalls -lt 2) { throw 'Post-stop identity was not observed' }
[Console]::Out.Write('unreadable-post-stop-fail-closed')
"""
    )
    result = _run_powershell(powershell, command)

    assert result.stdout == "unreadable-post-stop-fail-closed"


_RUN_LOCAL_GUARD_FUNCTIONS = (
    "Get-FullStackOwnershipState",
    "Assert-NoActiveFullStackOwnership",
)


def _run_local_guard_command(body: str) -> str:
    imports = "\n".join(
        f"Invoke-Expression (Get-LauncherFunctionText -Name '{name}')"
        for name in _RUN_LOCAL_GUARD_FUNCTIONS
    )
    return (
        _AST_SETUP
        + imports
        + r"""
$script:FullStackMetadata = $null
$script:IdentityStatus = 'Missing'
$script:ListenerPid = $null
$script:RemovedOwnershipFiles = @()
function Get-FullStackOwnershipServices {
    return @([pscustomobject]@{ Name = 'Backend'; OwnershipFile = 'full-stack/backend.owner.json' })
}
function Get-FullStackOwnershipMetadata {
    param([string]$Path)
    return $script:FullStackMetadata
}
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = $script:IdentityStatus; Fingerprint = $ExpectedStartFingerprint }
}
function Get-ListeningPid {
    param([int]$Port)
    return $script:ListenerPid
}
function Remove-Item {
    param([string]$LiteralPath, [switch]$Force, [object]$ErrorAction)
    $script:RemovedOwnershipFiles += $LiteralPath
    $script:FullStackMetadata = $null
}
function New-FullStackMetadata {
    return [pscustomobject]@{
        Valid = $true
        Pid = 101
        ProcessStartFingerprint = 'utc-ticks:638903664000000000'
        ServiceName = 'Backend'
        Port = 18000
    }
}
"""
        + body
    )


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    ("identity_status", "listener_pid", "expected_reason"),
    (
        ("Exact", "101", "full-stack owns exact PID 101"),
        ("Exact", "", "is alive but not its expected listener"),
        ("Unreadable", "", "process identity is unreadable"),
    ),
    ids=("active-listener", "exact-non-listener", "unreadable"),
)
def test_run_local_refuses_active_or_unreadable_full_stack_ownership(
    powershell: Path,
    identity_status: str,
    listener_pid: str,
    expected_reason: str,
) -> None:
    body = r"""
$script:FullStackMetadata = New-FullStackMetadata
$script:IdentityStatus = $env:PHASE7_IDENTITY_STATUS
if ($env:PHASE7_LISTENER_PID) { $script:ListenerPid = [int]$env:PHASE7_LISTENER_PID }
try {
    Assert-NoActiveFullStackOwnership
    throw 'run_local unexpectedly accepted full-stack ownership'
} catch {
    if ($_.Exception.Message -eq 'run_local unexpectedly accepted full-stack ownership') { throw }
    if ($_.Exception.Message -notlike '*run_local refused*') { throw }
}
[Console]::Out.Write('run-local-full-stack-conflict-ok')
"""
    result = _run_powershell(
        powershell,
        _run_local_guard_command(body),
        extra_env={
            "PHASE7_SCRIPT_PATH": str(_RUN_LOCAL),
            "PHASE7_IDENTITY_STATUS": identity_status,
            "PHASE7_LISTENER_PID": listener_pid,
        },
    )

    assert "run-local-full-stack-conflict-ok" in result.stdout
    assert expected_reason in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize("identity_status", ("Missing", "Mismatch"))
def test_run_local_only_cleans_disproved_stale_full_stack_ownership(
    powershell: Path,
    identity_status: str,
) -> None:
    body = r"""
$script:FullStackMetadata = New-FullStackMetadata
$script:IdentityStatus = $env:PHASE7_IDENTITY_STATUS
Assert-NoActiveFullStackOwnership
if ($script:RemovedOwnershipFiles.Count -ne 1 -or
    $script:RemovedOwnershipFiles[0] -ne 'full-stack/backend.owner.json') {
    throw 'Disproved stale full-stack metadata was not removed exactly once'
}
[Console]::Out.Write('run-local-stale-full-stack-disproved')
"""
    result = _run_powershell(
        powershell,
        _run_local_guard_command(body),
        extra_env={
            "PHASE7_SCRIPT_PATH": str(_RUN_LOCAL),
            "PHASE7_IDENTITY_STATUS": identity_status,
        },
    )

    assert "run-local-stale-full-stack-disproved" in result.stdout
    assert "proven stale" in result.stdout


def test_launcher_ownership_namespaces_are_disjoint_and_actions_are_guarded() -> None:
    full_stack_source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")
    run_local_source = _RUN_LOCAL.read_text(encoding="utf-8-sig")
    run_real_source = _RUN_REAL_SIMULATOR.read_text(encoding="utf-8-sig")

    assert "Join-Path $FullStackOwnershipDir 'backend.owner.json'" in full_stack_source
    assert "Join-Path $RunLocalOwnershipDir 'backend.owner.json'" in run_local_source
    assert "Join-Path $runRealOwnershipDir 'simulator-ui.owner.json'" in run_real_source
    guard_index = run_local_source.index("if ($RequiresLifecycleLock)")
    assert run_local_source.index("Assert-NoActiveFullStackOwnership", guard_index) < (
        run_local_source.index("switch ($Action)", guard_index)
    )


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_full_stack_refuses_a_run_local_listener_without_full_stack_metadata(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'full-stack/backend.owner.json'
$script:ListenerByPort[18000] = 303
try {
    Stop-AllServices -Services @($service) -FailOnConflict
    throw 'run_local listener was treated as full-stack owned'
} catch {
    if ($_.Exception.Message -eq 'run_local listener was treated as full-stack owned') { throw }
}
if ($script:StoppedPids.Count -ne 0) { throw 'run_local listener was stopped by full-stack' }
[Console]::Out.Write('full-stack-run-local-conflict-ok')
"""
    result = _run_powershell(powershell, _ownership_command(body))

    assert "full-stack-run-local-conflict-ok" in result.stdout
    assert "no ownership metadata exists" in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_repository_lifecycle_lock_rejects_interleaving_and_is_crash_released(
    powershell: Path,
    tmp_path: Path,
) -> None:
    imports = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-LauncherLifecycleLockName')
Invoke-Expression (Get-LauncherFunctionText -Name 'Enter-LauncherLifecycleLock')
Invoke-Expression (Get-LauncherFunctionText -Name 'Exit-LauncherLifecycleLock')
"""
    )
    holder_command = (
        imports
        + r"""
$lock = Enter-LauncherLifecycleLock -RepositoryRoot $env:PHASE7_LOCK_ROOT -LauncherName 'holder'
[Console]::Out.WriteLine('held')
[Console]::Out.Flush()
$null = [Console]::In.ReadLine()
"""
    )
    environment = {
        **os.environ,
        "PHASE7_SCRIPT_PATH": str(_RUN_FULL_STACK),
        "PHASE7_LOCK_ROOT": str(tmp_path),
    }
    holder = subprocess.Popen(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", holder_command],
        cwd=_ROOT,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        contender = _run_powershell(
            powershell,
            imports
            + r"""
try {
    Enter-LauncherLifecycleLock -RepositoryRoot $env:PHASE7_LOCK_ROOT -LauncherName 'contender'
    throw 'Concurrent lifecycle mutation unexpectedly acquired the lock'
} catch {
    if ($_.Exception.Message -eq 'Concurrent lifecycle mutation unexpectedly acquired the lock') { throw }
    [Console]::Out.Write($_.Exception.Message)
}
""",
            extra_env={"PHASE7_LOCK_ROOT": str(tmp_path)},
        )
        assert "Launcher lifecycle is busy" in contender.stdout
    finally:
        holder.kill()
        holder.wait(timeout=10)

    recovered = _run_powershell(
        powershell,
        imports
        + r"""
$lock = Enter-LauncherLifecycleLock -RepositoryRoot $env:PHASE7_LOCK_ROOT -LauncherName 'after-crash'
Exit-LauncherLifecycleLock -LockHandle $lock
[Console]::Out.Write('crash-released')
""",
        extra_env={"PHASE7_LOCK_ROOT": str(tmp_path)},
    )
    assert recovered.stdout == "crash-released"


def test_all_mutating_launchers_share_lock_and_cover_destructive_actions() -> None:
    full_stack_source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")
    run_local_source = _RUN_LOCAL.read_text(encoding="utf-8-sig")
    real_simulator_source = _RUN_REAL_SIMULATOR.read_text(encoding="utf-8-sig")

    lock_marker = "Local\\GEOv0-LauncherLifecycle-"
    for source in (full_stack_source, run_local_source, real_simulator_source):
        assert lock_marker in source
        assert "Enter-LauncherLifecycleLock" in source
        assert source.rindex("Exit-LauncherLifecycleLock -LockHandle") > source.rindex(
            "$LifecycleLock = Enter-LauncherLifecycleLock"
        )

    requires_block = run_local_source.split("$RequiresLifecycleLock =", 1)[1].split(
        "try {", 1
    )[0]
    for action in ("start", "stop", "restart", "restart-backend", "reset-db"):
        assert f"'{action}'" in requires_block
    assert "$Action -eq 'cleanup-simulator' -and -not $DryRun" in requires_block
    run_local_acquire = run_local_source.index("Enter-LauncherLifecycleLock `")
    assert run_local_acquire < run_local_source.index(
        "Assert-NoActiveFullStackOwnership", run_local_acquire
    )
    real_simulator_acquire = real_simulator_source.index(
        "$LifecycleLock = Enter-LauncherLifecycleLock -RepositoryRoot"
    )
    assert real_simulator_acquire < real_simulator_source.index(
        "Assert-NoActiveFullStackOwnership", real_simulator_acquire
    )
    assert "Stop-ViteDevServers" not in real_simulator_source
    assert "Stop-LocalUvicornServers" not in real_simulator_source
    assert "Invoke-LegacyProcessStopPlan" not in run_local_source
    assert run_local_source.count("Invoke-RunLocalOwnershipStopPlan -Services") >= 3
    assert "Stop-IfListeningAndOurs -Port" not in run_local_source
    assert "Stop-RunLocalProcesses" not in real_simulator_source
    assert "Invoke-RunRealOwnershipStopPlan" in real_simulator_source
    assert "Start-Process -FilePath $node.Source" in real_simulator_source
    assert "-PassThru" in real_simulator_source
    assert "& $uiScript" not in real_simulator_source
    assert (
        "$allStopped = Stop-AllServices -Services $Services -FailOnConflict"
        in full_stack_source
    )


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_local_legacy_pid_evidence_causes_zero_stops(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunLocalOwnershipState')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunLocalOwnershipPlan')
Invoke-Expression (Get-LauncherFunctionText -Name 'Test-RunLocalOwnershipPlansEqual')
Invoke-Expression (Get-LauncherFunctionText -Name 'Invoke-RunLocalOwnershipStopPlan')
$script:Stopped = @()
$script:Removed = @()
function Test-Path {
    param([string]$LiteralPath)
    return ($LiteralPath -eq 'backend.pid')
}
function Get-RunLocalOwnershipMetadata {
    param([string]$Path)
    return $null
}
function Get-RunLocalRepositoryIdentity { return 'repo-a' }
function Get-ListeningPid { param([int]$Port) return 202 }
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:Stopped += $Id
}
function Remove-Item { param([string]$LiteralPath, [switch]$Force, [object]$ErrorAction) $script:Removed += $LiteralPath }
$services = @([pscustomobject]@{
    Name = 'Backend'
    OwnershipFile = 'run-local/backend.owner.json'
    LegacyPidFile = 'backend.pid'
    Port = 18000
})
try {
    Invoke-RunLocalOwnershipStopPlan -Services $services
    throw 'Legacy evidence unexpectedly permitted stop'
} catch {
    if ($_.Exception.Message -eq 'Legacy evidence unexpectedly permitted stop') { throw }
    if ($_.Exception.Message -notlike '*legacy PID evidence exists*') { throw }
}
if ($script:Stopped.Count -ne 0) { throw 'Legacy sibling process was stopped' }
if ($script:Removed.Count -ne 0) { throw 'Legacy evidence was mutated' }
[Console]::Out.Write('run-local-legacy-conflict-zero-stops')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_SCRIPT_PATH": str(_RUN_LOCAL)},
    )
    assert result.stdout == "run-local-legacy-conflict-zero-stops"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_real_listener_without_metadata_causes_zero_stops(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunRealOwnershipState')
Invoke-Expression (Get-LauncherFunctionText -Name 'Invoke-RunRealOwnershipStopPlan')
$script:Stopped = @()
$script:Removed = @()
function Test-Path { param([string]$LiteralPath) return $false }
function Get-RunRealOwnershipMetadata { param([string]$Path) return $null }
function Get-RunRealRepositoryIdentity { return 'repo-a' }
function Get-ListeningPid { param([int]$Port) return 303 }
function Stop-ProcessById { param([int]$Id, [string]$ExpectedStartFingerprint) $script:Stopped += $Id }
function Remove-Item { param([string]$LiteralPath, [switch]$Force, [object]$ErrorAction) $script:Removed += $LiteralPath }
$service = [pscustomobject]@{ Name = 'Simulator UI'; OwnershipFile = 'run-real/simulator.owner.json'; Port = 5176 }
try {
    Invoke-RunRealOwnershipStopPlan -Service $service
    throw 'Listener without metadata unexpectedly permitted stop'
} catch {
    if ($_.Exception.Message -eq 'Listener without metadata unexpectedly permitted stop') { throw }
    if ($_.Exception.Message -notlike '*without exact run_real ownership metadata*') { throw }
}
if ($script:Stopped.Count -ne 0 -or $script:Removed.Count -ne 0) {
    throw 'Foreign listener was stopped or metadata changed'
}
[Console]::Out.Write('run-real-listener-conflict-zero-stops')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_SCRIPT_PATH": str(_RUN_REAL_SIMULATOR)},
    )
    assert result.stdout == "run-real-listener-conflict-zero-stops"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_full_stack_startup_rollback_is_reverse_order_and_exact_only(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Undo-StartedServices')
$script:Stopped = @()
$script:Removed = @()
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $status = if ($Id -eq 202) { 'Mismatch' } else { 'Exact' }
    return [pscustomobject]@{ Status = $status; Fingerprint = $ExpectedStartFingerprint }
}
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:Stopped += $Id
}
function Test-Path { param([string]$LiteralPath) return $true }
function Remove-Item {
    param([string]$LiteralPath, [switch]$Force, [object]$ErrorAction)
    $script:Removed += $LiteralPath
}
$started = @(
    [pscustomobject]@{ Service = [pscustomobject]@{ Name = 'Backend'; PidFile = 'backend.owner.json' }; Pid = 101; ProcessStartFingerprint = 'start-a' },
    [pscustomobject]@{ Service = [pscustomobject]@{ Name = 'Admin UI'; PidFile = 'admin.owner.json' }; Pid = 202; ProcessStartFingerprint = 'start-b' },
    [pscustomobject]@{ Service = [pscustomobject]@{ Name = 'Simulator UI'; PidFile = 'sim.owner.json' }; Pid = 303; ProcessStartFingerprint = 'start-c' }
)
Undo-StartedServices -StartedServices $started
if (($script:Stopped -join ',') -ne '303,101') { throw "Unexpected rollback stop order: $($script:Stopped -join ',')" }
if (($script:Removed -join ',') -ne 'sim.owner.json,admin.owner.json,backend.owner.json') {
    throw "Unexpected rollback metadata order: $($script:Removed -join ',')"
}
[Console]::Out.Write('startup-rollback-reverse-exact')
"""
    )
    result = _run_powershell(powershell, command)
    assert result.stdout == "startup-rollback-reverse-exact"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_status_reason_distinguishes_unreadable_identity_with_listener(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a'
$script:IdentityStatusByPid[101] = 'Unreadable'
$script:ListenerByPort[18000] = 101
$state = Get-ServiceStopState -Service $service
if (-not $state.Conflict) { throw 'Unreadable listener identity was not a conflict' }
[Console]::Out.Write($state.Reason)
"""
    result = _run_powershell(powershell, _ownership_command(body))
    assert "identity is unreadable" in result.stdout


def test_full_stack_main_wires_late_failure_to_reverse_rollback() -> None:
    source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")
    assert source.count("$StartedThisAttempt += [pscustomobject]@{") == 3
    assert "Undo-StartedServices -StartedServices $StartedThisAttempt" in source
    assert "$combinedFailure.Data['RollbackFailure']" in source


def test_run_local_status_branch_is_filesystem_read_only() -> None:
    source = _RUN_LOCAL.read_text(encoding="utf-8-sig")
    status_branch = source.split("    'status' {", 1)[1].split("    'stop' {", 1)[0]

    assert "if ($Action -ne 'status')" in source
    for mutation in (
        "Remove-StalePidFile",
        "Remove-Item",
        "New-Item",
        "Move-Item",
        "Set-Content",
        "Out-File",
    ):
        assert mutation not in status_branch
    assert "Get-RunLocalOwnershipPlan" in status_branch


def test_run_local_rejects_reload_before_any_lifecycle_mutation() -> None:
    source = _RUN_LOCAL.read_text(encoding="utf-8-sig")

    rejection = source.index("if ($ReloadBackend) {")
    lock = source.index("Enter-LauncherLifecycleLock", rejection)
    action_switch = source.index("switch ($Action)", lock)
    restart_stop = source.index("& $PSCommandPath @restartParams", action_switch)

    assert rejection < lock < action_switch < restart_stop
    assert "restart-backend -ReloadBackend" not in source[:rejection]


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_local_start_metadata_supports_a_separate_exact_stop(
    powershell: Path,
    tmp_path: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-LauncherLifecycleLockName')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunLocalRepositoryIdentity')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunLocalOwnershipMetadata')
Invoke-Expression (Get-LauncherFunctionText -Name 'Write-RunLocalOwnershipMetadata')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunLocalOwnershipState')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunLocalOwnershipPlan')
Invoke-Expression (Get-LauncherFunctionText -Name 'Test-RunLocalOwnershipPlansEqual')
Invoke-Expression (Get-LauncherFunctionText -Name 'Invoke-RunLocalOwnershipStopPlan')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-ProcessStartTimeFingerprint')
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForRunLocalServiceOwnership')
$RepoRoot = 'C:\repo-a'
$script:Stopped = @()
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'utc-ticks:638903664000000101' }
}
function Get-ListeningPid { param([int]$Port) return 101 }
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:Stopped += "$Id|$ExpectedStartFingerprint"
}
$service = [pscustomobject]@{
    Name = 'Backend'
    OwnershipFile = $env:PHASE7_OWNERSHIP_PATH
    LegacyPidFile = "$($env:PHASE7_OWNERSHIP_PATH).legacy"
    Port = 18000
}
$started = Wait-ForRunLocalServiceOwnership -Service $service -Process ([pscustomobject]@{ Id = 101 }) -TimeoutSec 1
if ($started.Pid -ne 101) { throw 'Started PID was not returned' }
$saved = Get-RunLocalOwnershipMetadata -Path $service.OwnershipFile
if (-not $saved.Valid -or $saved.Pid -ne 101 -or $saved.Port -ne 18000 -or
    $saved.RepositoryIdentity -ne (Get-RunLocalRepositoryIdentity)) {
    throw 'Exact run_local ownership metadata was not persisted'
}
Invoke-RunLocalOwnershipStopPlan -Services @($service)
if (($script:Stopped -join ',') -ne '101|utc-ticks:638903664000000101') {
    throw 'Separate exact stop did not target the launched instance'
}
if (Test-Path -LiteralPath $service.OwnershipFile) { throw 'Ownership metadata survived exact stop' }
[Console]::Out.Write('run-local-start-separate-stop-ok')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={
            "PHASE7_SCRIPT_PATH": str(_RUN_LOCAL),
            "PHASE7_OWNERSHIP_PATH": str(tmp_path / "backend.owner.json"),
        },
    )
    assert result.stdout == "run-local-start-separate-stop-ok"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_real_start_metadata_supports_a_separate_exact_stop(
    powershell: Path,
    tmp_path: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-LauncherLifecycleLockName')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunRealRepositoryIdentity')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunRealOwnershipMetadata')
Invoke-Expression (Get-LauncherFunctionText -Name 'Write-RunRealOwnershipMetadata')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-RunRealOwnershipState')
Invoke-Expression (Get-LauncherFunctionText -Name 'Invoke-RunRealOwnershipStopPlan')
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-ProcessStartTimeFingerprint')
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForRunRealSimulatorOwnership')
$repoRoot = 'C:\repo-a'
$script:Stopped = @()
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'utc-ticks:638903664000000303' }
}
function Get-ListeningPid { param([int]$Port) return 303 }
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:Stopped += "$Id|$ExpectedStartFingerprint"
}
$service = [pscustomobject]@{
    Name = 'Simulator UI'
    OwnershipFile = $env:PHASE7_OWNERSHIP_PATH
    Port = 5176
}
$started = Wait-ForRunRealSimulatorOwnership -Service $service -Process ([pscustomobject]@{ Id = 303 }) -TimeoutSec 1
if ($started.Pid -ne 303) { throw 'Started PID was not returned' }
$saved = Get-RunRealOwnershipMetadata -Path $service.OwnershipFile
if (-not $saved.Valid -or $saved.Pid -ne 303 -or $saved.Port -ne 5176 -or
    $saved.RepositoryIdentity -ne (Get-RunRealRepositoryIdentity)) {
    throw 'Exact run_real ownership metadata was not persisted'
}
Invoke-RunRealOwnershipStopPlan -Service $service
if (($script:Stopped -join ',') -ne '303|utc-ticks:638903664000000303') {
    throw 'Separate exact stop did not target the launched Simulator UI instance'
}
if (Test-Path -LiteralPath $service.OwnershipFile) { throw 'Ownership metadata survived exact stop' }
[Console]::Out.Write('run-real-start-separate-stop-ok')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={
            "PHASE7_SCRIPT_PATH": str(_RUN_REAL_SIMULATOR),
            "PHASE7_OWNERSHIP_PATH": str(tmp_path / "simulator-ui.owner.json"),
        },
    )
    assert result.stdout == "run-real-start-separate-stop-ok"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_real_compose_stop_failure_propagates(powershell: Path) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Stop-RunRealServices')
$script:Calls = @()
function Get-RunRealSimulatorService { return [pscustomobject]@{ Name = 'Simulator UI' } }
function Invoke-RunRealOwnershipStopPlan { param([object]$Service) $script:Calls += 'ui-stop' }
function Invoke-DockerCompose {
    param([string[]]$composeArgs)
    $script:Calls += 'compose-stop'
    throw 'compose-stop-failed'
}
try {
    Stop-RunRealServices
    throw 'Compose failure was swallowed'
} catch {
    if ($_.Exception.Message -eq 'Compose failure was swallowed') { throw }
    if ($_.Exception.Message -notlike '*compose-stop-failed*') { throw }
}
if (($script:Calls -join ',') -ne 'ui-stop,compose-stop') { throw 'Unexpected stop call order' }
[Console]::Out.Write('run-real-compose-failure-propagated')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_SCRIPT_PATH": str(_RUN_REAL_SIMULATOR)},
    )
    assert result.stdout.rstrip().endswith("run-real-compose-failure-propagated")
