from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess

import pytest


# PowerShell's HOST formatter word-wraps everything it renders - warnings, error
# records, verbose and debug - at the console width, inserting a line break INSIDE
# the message. A substring assertion on such output therefore measures the console,
# not the launcher. Output written with `[Console]::Out.Write` bypasses the
# formatter and is never wrapped; exposure is decided by the EMISSION CHANNEL, not
# by the length of the phrase being asserted.
#
# Two axes move the break, and neither is under the test's control:
#   - WIDTH, and NOT MONOTONICALLY. The live failure of `test_fail_on_conflict_...`
#     was at width 120; at 100 and at 80 the same message passes, because the break
#     lands elsewhere. A pin "an artificially narrow console stays green" would have
#     passed on the defect it was meant to catch.
#   - LOCALE. The Windows PowerShell warning prefix is `WARNING: ` (9) in en-US and
#     `ПРЕДУПРЕЖДЕНИЕ: ` (16) in ru-RU. Those seven columns are what pushed the
#     122-character line past 120: the same commit was red on a Russian host and
#     green on an English one at identical width. GitHub Actions runs en-US without
#     a console (width fixed at the 120 fallback), so CI did not observe this
#     instance - which is not the same as CI being unable to observe the class.
#
# `_console_text` removes the layout before comparison. It is for DIAGNOSTIC
# CONTENT only: exact-serialization, redaction and structural assertions keep the
# raw stream, because for those the whitespace is part of the contract.
def _console_text(raw: str) -> str:
    """Collapse host line-wrapping so a diagnostic can be matched by its words."""
    return " ".join(raw.split())


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
        # PowerShell writes UTF-8; `text=True` alone would decode it with the host's
        # locale codepage. On a host whose PowerShell UI language is not English the
        # localized `WARNING:` prefix is then undecodable (cp1252 has no 0x9D), the
        # reader thread dies, and `stdout` arrives as None - every assertion below
        # fails with TypeError instead of a diagnostic. Measured 2026-09-20 on a
        # ru-RU pwsh 7.6.6: 13 failures that CI's en-US runner cannot reproduce.
        # `errors="replace"` cannot manufacture a false green: every diagnostic these
        # tests assert is ASCII, so replacement can only corrupt the localized noise
        # around it, never the text being matched.
        encoding="utf-8",
        errors="replace",
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
def test_database_display_accepts_at_sign_inside_discarded_query(
    powershell: Path,
) -> None:
    raw_url = (
        "postgresql+asyncpg://phase7-user:phase7-password@db.example.test:5432/geov0"
        "?application_name=operator@workstation&sslmode=require"
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
    assert "operator@workstation" not in result.stdout


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    "raw_url",
    (
        "postgresql+asyncpg://operator:5432?secret@db.test/geov0",
        "postgresql+asyncpg://operator:2024#winter@db.test/geov0",
    ),
    ids=("numeric-query-password-prefix", "numeric-fragment-password-prefix"),
)
def test_database_display_rejects_ambiguous_numeric_credential_prefixes(
    powershell: Path,
    raw_url: str,
) -> None:
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
    for secret_part in ("operator", "5432", "2024", "secret", "winter"):
        assert secret_part not in combined_output


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_database_display_value_handles_a_url_without_authority_without_native_arguments(
    powershell: Path,
) -> None:
    """The no-authority branch of the display (`scheme:///path`), with a secret in the query.

    Until 017 T1704 this used the SQLite default URL. The application now accepts only
    `postgresql+asyncpg`, and the same shape is still reachable there: a Unix-socket URL names its
    host and password in the query, not in an authority.
    """
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Get-SafeDatabaseDisplayUrl')
$display = Get-SafeDatabaseDisplayUrl `
    -DatabaseUrl 'postgresql+asyncpg:///geov0_dev_local?host=/var/run/postgresql&password=must-not-print'
[Console]::Out.Write($display)
"""
    )

    result = _run_powershell(powershell, command)

    assert result.stdout == "postgresql+asyncpg:///geov0_dev_local"
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
    # Programme 017 `T1710` replaced the SQLite-only reset restriction with two preflights: the
    # destructive-boundary check that owns the `geov0_dev_<slug>` name contract, and the proof that
    # the application resolves the same database this launcher manages. Both are fallible, so both
    # belong before the first stop for the same reason the line they replace did.
    assert (
        main.index("Invoke-DevDatabaseCommand -PythonExe $Python -Command 'validate'")
        < stop_index
    )
    assert main.index('if ($EffectiveDatabaseUrl -ne $DevDatabaseUrl)') < stop_index
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
function Get-FullStackRepositoryIdentity { return 'repo-a' }
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
        [int]$Port = 18000,
        [string]$RepositoryIdentity = 'repo-a'
    )
    return [pscustomobject]@{
        Valid = $true
        RepositoryIdentity = $RepositoryIdentity
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
    (
        "saved_pid",
        "listener_pid",
        "saved_fingerprint",
        "current_fingerprint",
        "expected_warning",
    ),
    (
        (
            None,
            202,
            None,
            None,
            "Backend: port 18000 is listening on PID 202 but no ownership metadata"
            " exists. The listener was not stopped.",
        ),
        (
            101,
            202,
            "start-a",
            "start-a",
            "Backend: port 18000 is listening on PID 202 but owned PID 101 was"
            " expected. The listener was not stopped.",
        ),
        (
            101,
            None,
            "start-a",
            "start-a",
            "Backend: owned PID 101 is alive but is not listening on port 18000."
            " The listener was not stopped.",
        ),
        (
            101,
            101,
            "start-a",
            "start-b",
            "Backend: port 18000 is listening on PID 101 but the saved process"
            " fingerprint does not match. The listener was not stopped.",
        ),
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
    expected_warning: str,
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
    # Was `"not stopped" in ... or "not proven" in ...`, which is satisfied by the
    # SUMMARY warning alone and therefore never asserted the per-service reason at
    # all: all four cases would have passed with the reason text deleted. Each case
    # now names its own full diagnostic, and the summary is asserted separately.
    assert expected_warning in _console_text(result.stdout)
    assert _STOP_SUMMARY_WARNING in _console_text(result.stdout)


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
def test_wrong_repository_identity_is_never_accepted_as_owned(
    powershell: Path,
) -> None:
    body = r"""
$service = New-TestService -Name 'Backend' -Port 18000 -PidFile 'backend.pid'
$script:MetadataByFile['backend.pid'] = New-TestMetadata -Id 101 -StartTime 'start-a' -RepositoryIdentity 'repo-b'
$script:ListenerByPort[18000] = 101
$script:FingerprintByPid[101] = 'start-a'
$state = Get-ServiceStopState -Service $service
if (-not $state.Conflict) { throw 'Foreign repository metadata was accepted' }
if ($script:StoppedPids.Count -ne 0) { throw 'Foreign repository process was stopped' }
[Console]::Out.Write('foreign-repository-refused')
"""
    result = _run_powershell(powershell, _ownership_command(body))
    assert result.stdout == "foreign-repository-refused"


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
    assert (
        "Admin UI: port 5173 is listening on PID 202 but no ownership metadata"
        " exists. The listener was not stopped."
    ) in _console_text(result.stdout)


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
    assert (
        "Backend: owned PID 101 identity is unreadable; metadata was retained."
        " The listener was not stopped."
    ) in _console_text(result.stdout)


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
    assert (
        "Service ownership changed during final preflight. No process was stopped."
    ) in _console_text(result.stdout)


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
function Get-FullStackRepositoryIdentity { return 'repo-a' }
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
if ($metadata.RepositoryIdentity -ne 'repo-a') { throw 'Repository identity was lost' }
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
function Get-RunLocalRepositoryIdentity { return 'repo-a' }
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
        RepositoryIdentity = 'repo-a'
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
        (
            "Exact",
            "101",
            "Backend: full-stack owns exact PID 101 on port 18000."
            " run_local did not stop or adopt it.",
        ),
        (
            "Exact",
            "",
            "Backend: full-stack exact PID 101 is alive but not its expected"
            " listener. run_local did not stop or adopt it.",
        ),
        (
            "Unreadable",
            "",
            "Backend: full-stack process identity is unreadable; metadata was"
            " retained. run_local did not stop or adopt it.",
        ),
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
    assert expected_reason in _console_text(result.stdout)


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
    assert (
        "Backend: removed full-stack metadata only after its process identity was"
        " proven stale."
    ) in _console_text(result.stdout)


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
    assert (
        "Backend: port 18000 is listening on PID 303 but no ownership metadata"
        " exists. The listener was not stopped."
    ) in _console_text(result.stdout)


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
        # Same host-locale decoding trap as `_run_powershell` above: a localized
        # PowerShell line on either pipe would kill the reader instead of failing
        # the assertion that names it.
        encoding="utf-8",
        errors="replace",
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        contender = _run_powershell(
            powershell,
            imports
            + r"""
$env:GEO_LAUNCHER_LIFECYCLE_LOCK_NAME = Get-LauncherLifecycleLockName -RepositoryRoot $env:PHASE7_LOCK_ROOT
$env:GEO_LAUNCHER_LIFECYCLE_LOCK_OWNER_PID = '1'
$env:GEO_LAUNCHER_LIFECYCLE_LOCK_OWNER_FINGERPRINT = 'utc-ticks:1'
$env:GEO_LAUNCHER_LIFECYCLE_LOCK_TOKEN = 'forged'
try {
    Enter-LauncherLifecycleLock -RepositoryRoot $env:PHASE7_LOCK_ROOT -LauncherName 'contender'
    throw 'Concurrent lifecycle mutation unexpectedly acquired the lock'
} catch {
    if ($_.Exception.Message -eq 'Concurrent lifecycle mutation unexpectedly acquired the lock') { throw }
    [Console]::Out.Write($_.Exception.Message)
}
""",
            extra_env={
                "PHASE7_SCRIPT_PATH": str(_RUN_LOCAL),
                "PHASE7_LOCK_ROOT": str(tmp_path),
            },
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
    assert "Start-Process -FilePath $UiTools.Node" in real_simulator_source
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
    restart_stop = source.index("Invoke-RunLocalOwnershipStopPlan -Services", lock)
    action_switch = source.index("switch ($Action)", restart_stop)

    assert rejection < lock < restart_stop < action_switch
    assert "restart-backend -ReloadBackend" not in source[:rejection]
    assert "$PSCommandPath" not in source
    assert "AllowInherited" not in source
    assert "GEO_LAUNCHER_LIFECYCLE_LOCK_TOKEN" not in source


def test_replacement_paths_wait_for_owned_ports_to_be_released() -> None:
    local_source = _RUN_LOCAL.read_text(encoding="utf-8-sig")
    restart_backend = local_source.split("    'restart-backend' {", 1)[1].split(
        "    'start' {", 1
    )[0]
    assert (
        restart_backend.index(
            "Invoke-RunLocalOwnershipStopPlan -Services $backendStopServices"
        )
        < restart_backend.index("Wait-ForPortToBeFree -Port $backendPortUsed")
        < restart_backend.index("Start-Process -FilePath $Python")
    )

    real_source = _RUN_REAL_SIMULATOR.read_text(encoding="utf-8-sig")
    start_block = real_source.split("# Action=start.", 1)[1]
    assert (
        start_block.index("Invoke-RunRealOwnershipStopPlan -Service $simulatorService")
        < start_block.index("Wait-ForPortToBeFree -Port $SimulatorUiPort")
        < start_block.index("Start-SimulatorUiReal -UiTools $uiTools")
    )


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    "script_path",
    (_RUN_LOCAL, _RUN_REAL_SIMULATOR),
    ids=("run-local", "run-real"),
)
def test_launcher_port_release_wait_observes_until_listener_disappears(
    powershell: Path,
    script_path: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Wait-ForPortToBeFree')
$script:Observations = 0
function Get-ListeningPid {
    param([int]$Port)
    $script:Observations += 1
    if ($script:Observations -lt 3) { return 909 }
    return $null
}
function Start-Sleep { param([int]$Milliseconds) }
$released = Wait-ForPortToBeFree -Port 5176 -TimeoutSec 1
if (-not $released) { throw 'Port release was not observed' }
if ($script:Observations -lt 3) { throw 'Port wait did not retry' }
[Console]::Out.Write('port-release-observed')
"""
    )

    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_SCRIPT_PATH": str(script_path)},
    )
    assert result.stdout == "port-release-observed"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    (
        "script_path",
        "function_name",
        "service_name",
        "launched_pid",
        "primary_fragment",
    ),
    (
        (
            _RUN_LOCAL,
            "Wait-ForRunLocalServiceOwnership",
            "Backend",
            101,
            "listener PID 999 is not launched PID 101",
        ),
        (
            _RUN_REAL_SIMULATOR,
            "Wait-ForRunRealSimulatorOwnership",
            "Simulator UI",
            303,
            "listener PID 999 is not launched PID 303",
        ),
    ),
    ids=("run-local", "run-real"),
)
def test_launcher_startup_cleanup_preserves_primary_failure(
    powershell: Path,
    script_path: Path,
    function_name: str,
    service_name: str,
    launched_pid: int,
    primary_fragment: str,
) -> None:
    command = (
        _AST_SETUP
        + r"""
$functionName = $env:PHASE7_FUNCTION_NAME
Invoke-Expression (Get-LauncherFunctionText -Name $functionName)
function Get-ProcessStartTimeFingerprint { param([int]$Id) return 'start-a' }
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = 'start-a' }
}
function Get-ListeningPid { param([int]$Port) return 999 }
# run_local walks the listener's parents (2026-09-24); 999 is not a descendant of the launched PID.
function Get-RunLocalDescendantListener { return $null }
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    throw 'cleanup-stop-failed'
}
function Write-RunLocalOwnershipMetadata { throw 'Ownership was unexpectedly persisted' }
function Write-RunRealOwnershipMetadata { throw 'Ownership was unexpectedly persisted' }
$service = [pscustomobject]@{
    Name = $env:PHASE7_SERVICE_NAME
    Port = 5176
    OwnershipFile = 'unused.owner.json'
}
$process = [pscustomobject]@{ Id = [int]$env:PHASE7_LAUNCHED_PID }
try {
    & $functionName -Service $service -Process $process -TimeoutSec 1
    throw 'Combined failure unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Combined failure unexpectedly succeeded') { throw }
    if ($_.Exception.Message -notlike "*$($env:PHASE7_PRIMARY_FRAGMENT)*") {
        throw 'Primary startup failure was masked'
    }
    if ($_.Exception.Message -notlike '*Startup cleanup also failed: cleanup-stop-failed*') {
        throw 'Cleanup failure evidence was lost'
    }
    if ($null -eq $_.Exception.InnerException -or
        $_.Exception.InnerException.Message -notlike "*$($env:PHASE7_PRIMARY_FRAGMENT)*") {
        throw 'Primary failure was not preserved as the inner exception'
    }
    if ([string]$_.Exception.Data['CleanupFailure'] -notlike '*cleanup-stop-failed*') {
        throw 'Cleanup failure detail was not preserved'
    }
}
[Console]::Out.Write('startup-cleanup-evidence-ok')
"""
    )

    result = _run_powershell(
        powershell,
        command,
        extra_env={
            "PHASE7_SCRIPT_PATH": str(script_path),
            "PHASE7_FUNCTION_NAME": function_name,
            "PHASE7_SERVICE_NAME": service_name,
            "PHASE7_LAUNCHED_PID": str(launched_pid),
            "PHASE7_PRIMARY_FRAGMENT": primary_fragment,
        },
    )
    assert result.stdout == "startup-cleanup-evidence-ok"


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


# 2026-09-24: on Windows `.venv\Scripts\python.exe` is a redirector that starts the base interpreter
# as a CHILD (measured: launched PID 16076, listening interpreter PID 11892), so the backend never
# listens on the launched PID. run_local accepts a listener that DESCENDS from the launched PID
# (parent chain, bounded depth, parents never younger than children), records both, and stops both.
# The process table below is a fake: {pid: start ticks} and {pid: parent pid}.
_RUN_LOCAL_DESCENDANT_SETUP = r"""
foreach ($name in @(
    'Get-LauncherLifecycleLockName', 'Get-RunLocalRepositoryIdentity',
    'Get-RunLocalOwnershipMetadata', 'Write-RunLocalOwnershipMetadata',
    'Get-RunLocalOwnershipState', 'Get-RunLocalOwnershipPlan',
    'Test-RunLocalOwnershipPlansEqual', 'Invoke-RunLocalOwnershipStopPlan',
    'Get-ProcessStartTimeFingerprint', 'Get-StartFingerprintTicks',
    'Get-RunLocalDescendantListener', 'Wait-ForRunLocalServiceOwnership'
)) {
    Invoke-Expression (Get-LauncherFunctionText -Name $name)
}
$RepoRoot = 'C:\repo-a'
$script:StartTicks = @{}
$script:Parents = @{}
$script:Listener = $null
$script:Stopped = @()
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    if (-not $script:StartTicks.ContainsKey($Id)) {
        return [pscustomobject]@{ Status = 'Missing'; Fingerprint = $null }
    }
    $fingerprint = "utc-ticks:$($script:StartTicks[$Id])"
    $status = if (-not $ExpectedStartFingerprint) { 'Observed' }
        elseif ($fingerprint -eq $ExpectedStartFingerprint) { 'Exact' } else { 'Mismatch' }
    return [pscustomobject]@{ Status = $status; Fingerprint = $fingerprint }
}
function Get-ProcessParentId {
    param([int]$Id)
    if ($script:Parents.ContainsKey($Id)) { return $script:Parents[$Id] }
    return $null
}
function Get-ListeningPid { param([int]$Port) return $script:Listener }
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:Stopped += "$Id|$ExpectedStartFingerprint"
    # Killing the redirector does not kill its child; killing the listener frees the port.
    $script:StartTicks.Remove($Id)
    if ($script:Listener -eq $Id) { $script:Listener = $null }
}
function Start-Sleep { param([int]$Milliseconds) }
$service = [pscustomobject]@{
    Name = 'Backend'
    OwnershipFile = $env:PHASE7_OWNERSHIP_PATH
    LegacyPidFile = "$($env:PHASE7_OWNERSHIP_PATH).legacy"
    Port = 18000
}
"""


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_local_adopts_a_descendant_listener_and_stop_kills_both(
    powershell: Path,
    tmp_path: Path,
) -> None:
    command = (
        _AST_SETUP
        + _RUN_LOCAL_DESCENDANT_SETUP
        + r"""
$script:StartTicks[101] = 1000
$script:StartTicks[202] = 1001
$script:Parents[202] = 101
$script:Listener = 202
$started = Wait-ForRunLocalServiceOwnership -Service $service -Process ([pscustomobject]@{ Id = 101 }) -TimeoutSec 1
if ($started.Pid -ne 101 -or $started.ListenerPid -ne 202 -or
    $started.ListenerProcessStartFingerprint -ne 'utc-ticks:1001') {
    throw "Descendant listener was not returned: $($started | Out-String)"
}
$raw = Get-Content -LiteralPath $service.OwnershipFile -Raw | ConvertFrom-Json
if ($raw.version -ne 2 -or $raw.pid -ne 101 -or $raw.listener_pid -ne 202 -or
    $raw.process_start_fingerprint -ne 'utc-ticks:1000' -or $raw.listener_start_fingerprint -ne 'utc-ticks:1001') {
    throw 'Both the launched PID and the listener were not recorded'
}
$state = Get-RunLocalOwnershipState -Service $service
if (-not $state.Owned -or $state.Conflict) { throw "Descendant listener is not owned: $($state.Reason)" }
Invoke-RunLocalOwnershipStopPlan -Services @($service)
if (($script:Stopped -join ',') -ne '101|utc-ticks:1000,202|utc-ticks:1001') {
    throw "Stop did not kill the redirector and then its listening child: $($script:Stopped -join ',')"
}
if ($script:Listener) { throw 'The port is still held' }
if (Test-Path -LiteralPath $service.OwnershipFile) { throw 'Ownership metadata survived the stop' }
[Console]::Out.Write('run-local-descendant-adopted-and-stopped')
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
    assert result.stdout == "run-local-descendant-adopted-and-stopped"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
@pytest.mark.parametrize(
    "table",
    (
        # A foreign listener: its parents never reach the launched PID.
        "$script:StartTicks[202] = 1001; $script:StartTicks[303] = 900; $script:Parents[202] = 303",
        # A recycled parent PID: 202 records 101 as its parent, but the 101 alive now started
        # AFTER 202 - it is the launched process that reused a dead parent's PID, not the parent.
        "$script:StartTicks[202] = 999; $script:Parents[202] = 101",
        # Deeper than the bounded walk (four generations): 202 -> 5 -> 4 -> 3 -> 2 -> 101.
        "$script:StartTicks[202] = 1005; foreach ($p in 2..5) { $script:StartTicks[$p] = 1000 + $p }; "
        "$script:Parents[202] = 5; $script:Parents[5] = 4; $script:Parents[4] = 3; "
        "$script:Parents[3] = 2; $script:Parents[2] = 101",
    ),
    ids=("foreign", "recycled-parent-pid", "too-deep"),
)
def test_run_local_refuses_a_listener_that_is_not_the_launched_descendant(
    powershell: Path,
    tmp_path: Path,
    table: str,
) -> None:
    command = (
        _AST_SETUP
        + _RUN_LOCAL_DESCENDANT_SETUP
        + r"""
$script:StartTicks[101] = 1000
$script:Listener = 202
Invoke-Expression $env:PHASE7_TABLE
try {
    Wait-ForRunLocalServiceOwnership -Service $service -Process ([pscustomobject]@{ Id = 101 }) -TimeoutSec 1
    throw 'Foreign listener was adopted'
} catch {
    if ($_.Exception.Message -eq 'Foreign listener was adopted') { throw }
    if ($_.Exception.Message -notlike '*listener PID 202 is not launched PID 101*') { throw }
}
if (Test-Path -LiteralPath $service.OwnershipFile) { throw 'Foreign listener ownership was persisted' }
if (($script:Stopped -join ',') -ne '101|utc-ticks:1000') {
    throw "Only the launched process may be cleaned up: $($script:Stopped -join ',')"
}
[Console]::Out.Write('run-local-foreign-listener-refused')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={
            "PHASE7_SCRIPT_PATH": str(_RUN_LOCAL),
            "PHASE7_OWNERSHIP_PATH": str(tmp_path / "backend.owner.json"),
            "PHASE7_TABLE": table,
        },
    )
    assert result.stdout == "run-local-foreign-listener-refused"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_local_reads_version_1_ownership_and_refuses_a_malformed_version_2(
    powershell: Path,
    tmp_path: Path,
) -> None:
    command = (
        _AST_SETUP
        + _RUN_LOCAL_DESCENDANT_SETUP
        + r"""
function New-Record {
    param([int]$Version)
    return [ordered]@{
        version = $Version
        repository_identity = (Get-RunLocalRepositoryIdentity)
        service_name = 'Backend'
        port = 18000
        pid = 101
        process_start_fingerprint = 'utc-ticks:1000'
    } | ConvertTo-Json -Compress
}
Set-Content -LiteralPath $service.OwnershipFile -Value (New-Record -Version 1)
$read = Get-RunLocalOwnershipMetadata -Path $service.OwnershipFile
if (-not $read.Valid -or $read.ListenerPid -ne 101 -or $read.ListenerProcessStartFingerprint -ne 'utc-ticks:1000') {
    throw 'A version-1 record did not read as launched PID = listener'
}
$script:StartTicks[101] = 1000
$script:Listener = 101
if (-not (Get-RunLocalOwnershipState -Service $service).Owned) { throw 'A live version-1 owner was not owned' }
Set-Content -LiteralPath $service.OwnershipFile -Value (New-Record -Version 2)
if ((Get-RunLocalOwnershipMetadata -Path $service.OwnershipFile).Valid) {
    throw 'A version-2 record without a listener was accepted'
}
$state = Get-RunLocalOwnershipState -Service $service
if ($state.Owned -or -not $state.Conflict) { throw 'A malformed record was not a conflict' }
[Console]::Out.Write('run-local-metadata-versions-ok')
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
    assert result.stdout == "run-local-metadata-versions-ok"


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


def test_runtime_metadata_and_transactional_start_wiring() -> None:
    full_stack = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")
    run_local = _RUN_LOCAL.read_text(encoding="utf-8-sig")
    run_real = _RUN_REAL_SIMULATOR.read_text(encoding="utf-8-sig")

    assert "repository_identity = Get-FullStackRepositoryIdentity" in full_stack
    assert "RepositoryIdentity = [string]$metadata.repository_identity" in full_stack
    assert "Get-FullStackRepositoryIdentity" in full_stack.split(
        "function Get-ServiceStopState", 1
    )[1]
    assert "RepositoryIdentity = [string]$metadata.repository_identity" in run_local
    assert "RepositoryIdentity = [string]$metadata.repository_identity" in run_real

    assert "AllowInherited" not in run_local
    assert "GEO_LAUNCHER_LIFECYCLE_LOCK_TOKEN" not in run_local
    assert "$PSCommandPath" not in run_local
    assert run_local.index("Enter-LauncherLifecycleLock", run_local.index("$LifecycleLock")) < run_local.index(
        "if ($Action -eq 'restart')"
    )
    assert run_local.count("$StartedThisAttempt += [pscustomobject]@{") == 3
    assert "Invoke-RunLocalStartupRollback" in run_local

    main = run_real.split("# Action=start.", 1)[1]
    assert main.index("Assert-RunRealOwnershipForReplacement") < main.index(
        "Get-SimulatorUiRealTools"
    )
    assert main.index("Get-SimulatorUiRealTools") < main.index(
        "Get-RunningComposeServices"
    )
    assert main.index("Ensure-ComposeCore") < main.index("Seed-DbIfRequested")
    assert main.index("Seed-DbIfRequested") < main.index(
        "Invoke-RunRealOwnershipStopPlan"
    )
    assert main.index("Invoke-RunRealOwnershipStopPlan") < main.index(
        "Start-SimulatorUiReal"
    )
    assert "Invoke-RunRealStartupRollback" in main


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_local_startup_rollback_is_reverse_exact_and_keeps_both_failures(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Undo-RunLocalStartedServices')
$script:Stopped = @()
$script:Removed = @()
function Get-RunLocalRepositoryIdentity { return 'repo-a' }
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status = 'Exact'; Fingerprint = $ExpectedStartFingerprint }
}
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:Stopped += "$Id|$ExpectedStartFingerprint"
}
function Test-Path { param([string]$LiteralPath) return $true }
function Get-RunLocalOwnershipMetadata {
    param([string]$Path)
    $id = if ($Path -eq 'ui.owner.json') { 202 } else { 101 }
    $name = if ($Path -eq 'ui.owner.json') { 'Admin UI' } else { 'Backend' }
    $port = if ($Path -eq 'ui.owner.json') { 5173 } else { 18000 }
    $fingerprint = if ($id -eq 202) { 'start-b' } else { 'start-a' }
    return [pscustomobject]@{ Valid=$true; RepositoryIdentity='repo-a'; ServiceName=$name; Port=$port; Pid=$id; ProcessStartFingerprint=$fingerprint }
}
function Remove-Item { param([string]$LiteralPath, [switch]$Force, [object]$ErrorAction); $script:Removed += $LiteralPath }
$started = @(
    [pscustomobject]@{ Service=[pscustomobject]@{Name='Backend';Port=18000;OwnershipFile='backend.owner.json'};Pid=101;ProcessStartFingerprint='start-a' },
    [pscustomobject]@{ Service=[pscustomobject]@{Name='Admin UI';Port=5173;OwnershipFile='ui.owner.json'};Pid=202;ProcessStartFingerprint='start-b' }
)
Undo-RunLocalStartedServices -StartedServices $started
if (($script:Stopped -join ',') -ne '202|start-b,101|start-a') { throw 'Rollback was not reverse/exact' }
if (($script:Removed -join ',') -ne 'ui.owner.json,backend.owner.json') { throw 'Metadata cleanup order changed' }

Invoke-Expression (Get-LauncherFunctionText -Name 'Invoke-RunLocalStartupRollback')
function Undo-RunLocalStartedServices { throw 'cleanup-failed' }
try {
    Invoke-RunLocalStartupRollback -PrimaryFailure ([System.Exception]::new('primary-failed')) -StartedServices @($started[0])
    throw 'Combined failure unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Combined failure unexpectedly succeeded') { throw }
    if ($_.Exception.Message -notlike '*primary-failed*cleanup-failed*') { throw }
    if ($_.Exception.Data['RollbackFailure'] -notlike '*cleanup-failed*') { throw 'Rollback evidence missing' }
}
[Console]::Out.Write('run-local-rollback-ok')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_SCRIPT_PATH": str(_RUN_LOCAL)},
    )
    assert result.stdout == "run-local-rollback-ok"


@pytest.mark.skipif(not _POWERSHELLS, reason="PowerShell is required")
@pytest.mark.parametrize("powershell", _POWERSHELLS, ids=_POWERSHELL_IDS)
def test_run_real_rollback_limits_compose_delta_and_keeps_primary_evidence(
    powershell: Path,
) -> None:
    command = (
        _AST_SETUP
        + r"""
Invoke-Expression (Get-LauncherFunctionText -Name 'Stop-ComposeServicesStartedThisAttempt')
$script:ComposeArgs = @()
function Get-RunningComposeServices { return @('db','redis','app','foreign') }
function Invoke-DockerCompose { param([string[]]$composeArgs); $script:ComposeArgs = @($composeArgs) }
Stop-ComposeServicesStartedThisAttempt -BaselineServices @('db')
if (($script:ComposeArgs -join ',') -ne 'stop,redis,app') { throw "Wrong Compose rollback delta: $($script:ComposeArgs -join ',')" }

Invoke-Expression (Get-LauncherFunctionText -Name 'Undo-RunRealStartedUi')
$script:UiStopped = @()
$script:UiRemoved = @()
function Get-RunRealRepositoryIdentity { return 'repo-a' }
function Get-ProcessIdentityObservation {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    return [pscustomobject]@{ Status='Exact'; Fingerprint=$ExpectedStartFingerprint }
}
function Stop-ProcessById {
    param([int]$Id, [string]$ExpectedStartFingerprint)
    $script:UiStopped += "$Id|$ExpectedStartFingerprint"
}
function Test-Path { param([string]$LiteralPath) return $true }
function Get-RunRealOwnershipMetadata {
    param([string]$Path)
    return [pscustomobject]@{ Valid=$true; RepositoryIdentity='repo-a'; ServiceName='Simulator UI'; Port=5176; Pid=303; ProcessStartFingerprint='start-c' }
}
function Remove-Item { param([string]$LiteralPath, [switch]$Force, [object]$ErrorAction); $script:UiRemoved += $LiteralPath }
$lateUi = [pscustomobject]@{ Service=[pscustomobject]@{Name='Simulator UI';Port=5176;OwnershipFile='ui.owner.json'};Pid=303;ProcessStartFingerprint='start-c' }
Undo-RunRealStartedUi -StartedUi @($lateUi)
if (($script:UiStopped -join ',') -ne '303|start-c') { throw 'Late UI was not rolled back exactly' }
if (($script:UiRemoved -join ',') -ne 'ui.owner.json') { throw 'Late UI metadata was not removed' }

Invoke-Expression (Get-LauncherFunctionText -Name 'Invoke-RunRealStartupRollback')
$script:RollbackCalls = @()
function Undo-RunRealStartedUi { param([object[]]$StartedUi); $script:RollbackCalls += 'ui'; throw 'ui-cleanup-failed' }
function Stop-ComposeServicesStartedThisAttempt { param([string[]]$BaselineServices); $script:RollbackCalls += 'compose'; throw 'compose-cleanup-failed' }
try {
    Invoke-RunRealStartupRollback -PrimaryFailure ([System.Exception]::new('primary-failed')) -StartedUi @() -BaselineComposeServices @('db')
    throw 'Combined failure unexpectedly succeeded'
} catch {
    if ($_.Exception.Message -eq 'Combined failure unexpectedly succeeded') { throw }
    if ($_.Exception.Message -notlike '*primary-failed*') { throw 'Primary evidence missing' }
    if ($_.Exception.Data['RollbackFailure'] -notlike '*ui-cleanup-failed*compose-cleanup-failed*') { throw 'Rollback evidence missing' }
}
if (($script:RollbackCalls -join ',') -ne 'ui,compose') { throw 'Rollback order changed' }
[Console]::Out.Write('run-real-rollback-ok')
"""
    )
    result = _run_powershell(
        powershell,
        command,
        extra_env={"PHASE7_SCRIPT_PATH": str(_RUN_REAL_SIMULATOR)},
    )
    assert result.stdout == "run-real-rollback-ok"


# ---------------------------------------------------------------------------
# F-014-11 / T1412: the pin.
#
# The finding was recorded as "the verdict of eight assertions is a function of
# the console width of whoever runs them", with a prescribed pin - "a run with an
# artificially narrow console must stay green". Measurement before the fix
# corrected three parts of that:
#
#   - the inventory is 12 exposed assertions over 9 distinct diagnostics, not 8,
#     and it is decided by EMISSION CHANNEL. The finding's headline example,
#     `owned PID 101 is alive but is not listening on port 18000` at the test
#     below, is written with `[Console]::Out.Write` and cannot be wrapped at any
#     width. Length was the wrong measure;
#   - the failure is NOT monotonic in width. The live red was at 120; the same
#     message passes at 100 and at 80. The prescribed narrow-console pin would
#     have been green on the defect it was written to catch;
#   - width was not even the axis that fired. The run had no console at all
#     (fixed 120 fallback); the Russian warning prefix is seven columns longer
#     than the English one, and those seven columns are the whole difference.
#
# So the pin is not a width. It is an invariance: for every diagnostic this file
# asserts, and for every place the host could break the line, the assertion must
# still hold. That is deterministic, needs no console, no locale and no
# PowerShell, and therefore runs everywhere - including in CI, which is en-US and
# consoleless and never saw this instance.
# ---------------------------------------------------------------------------

# Both Windows PowerShell warning prefixes. The width at which each line breaks
# is a function of the prefix, so the sweep carries both.
_WARNING_PREFIXES: tuple[str, ...] = (
    "WARNING: ",
    "\u041f\u0420\u0415\u0414\u0423\u041f\u0420\u0415\u0416\u0414\u0415\u041d\u0418\u0415: ",
)

# Every diagnostic this file asserts against host-formatted output. Each is the
# full sentence the launcher emits, not a fragment of it: a fragment can be
# reconstructed across two unrelated records once whitespace is collapsed, and
# `test_collapsing_whitespace_joins_across_line_boundaries` below demonstrates
# exactly that, on the fragment this file used to assert.
_WRAP_EXPOSED_DIAGNOSTICS: tuple[str, ...] = (
    "Backend: port 18000 is listening on PID 202 but no ownership metadata"
    " exists. The listener was not stopped.",
    "Backend: port 18000 is listening on PID 202 but owned PID 101 was"
    " expected. The listener was not stopped.",
    "Backend: owned PID 101 is alive but is not listening on port 18000."
    " The listener was not stopped.",
    "Backend: port 18000 is listening on PID 101 but the saved process"
    " fingerprint does not match. The listener was not stopped.",
    "Admin UI: port 5173 is listening on PID 202 but no ownership metadata"
    " exists. The listener was not stopped.",
    "Backend: port 18000 is listening on PID 303 but no ownership metadata"
    " exists. The listener was not stopped.",
    "Backend: owned PID 101 identity is unreadable; metadata was retained."
    " The listener was not stopped.",
    "Service ownership changed during final preflight. No process was stopped.",
    "One or more services were not stopped because ownership was not proven.",
    "Backend: full-stack owns exact PID 101 on port 18000."
    " run_local did not stop or adopt it.",
    "Backend: full-stack exact PID 101 is alive but not its expected"
    " listener. run_local did not stop or adopt it.",
    "Backend: full-stack process identity is unreadable; metadata was"
    " retained. run_local did not stop or adopt it.",
    "Backend: removed full-stack metadata only after its process identity was"
    " proven stale.",
)

_STOP_SUMMARY_WARNING = (
    "One or more services were not stopped because ownership was not proven."
)

# Asserted with `in` against raw process output on purpose. Each of these is
# emitted by `[Console]::Out.Write`, which bypasses the host formatter, so it is
# never wrapped and must not be normalized away. Adding a line here is a claim
# about the EMISSION CHANNEL and has to be checked against the launcher.
_RAW_OUTPUT_ASSERTIONS_BY_DESIGN: frozenset[str] = frozenset(
    {
        # test_repository_lifecycle_lock_rejects_interleaving_and_is_crash_released
        # emits it with `[Console]::Out.Write($_.Exception.Message)`.
        "Launcher lifecycle is busy",
        # test_exact_live_non_listener_has_an_actionable_conflict_reason emits it
        # with `[Console]::Out.Write($state.Reason)`. This is the phrase F-014-11
        # named as "the most exposed of the eight, held green only by the current
        # width". It is the longest of them, and it is immune: the reason never
        # reaches the host formatter. Length was the wrong measure.
        "owned PID 101 is alive but is not listening on port 18000",
        # test_status_reason_distinguishes_unreadable_identity_with_listener emits
        # it with `[Console]::Out.Write($state.Reason)` too. The same words DO reach
        # the formatter in test_unreadable_process_identity_is_a_conflict_..., whose
        # assertion is normalized - one phrase, two channels, two dispositions.
        "identity is unreadable",
    }
)


def _host_wrapped_variants(line: str) -> tuple[str, ...]:
    """Every way the PowerShell host could break `line`, plus the pathological ones.

    The host breaks at the last space that fits, keeps that space at the end of
    the line and writes CRLF. `subprocess` with ``text=True`` turns that into LF,
    so both forms are generated; so are a line broken at every space at once, and
    a form with continuation indentation, which some hosts add.
    """
    positions = [index for index, char in enumerate(line) if char == " "]
    variants: list[str] = []
    for index in positions:
        head, tail = line[: index + 1], line[index + 1 :]
        variants.append(head + "\r\n" + tail)
        variants.append(head + "\n" + tail)
        variants.append(head + "\r\n    " + tail)
    # every break at once, and the same with CRLF
    variants.append("\n".join(part + " " for part in line.split(" "))[:-1])
    variants.append("\r\n".join(part + " " for part in line.split(" "))[:-1])
    return tuple(variants)


@pytest.mark.parametrize("prefix", _WARNING_PREFIXES, ids=("en", "ru"))
@pytest.mark.parametrize(
    "diagnostic", _WRAP_EXPOSED_DIAGNOSTICS, ids=range(len(_WRAP_EXPOSED_DIAGNOSTICS))
)
def test_console_text_survives_every_host_wrap_position(
    prefix: str, diagnostic: str
) -> None:
    """No break position, in either locale, may hide a diagnostic from its assertion."""
    line = prefix + diagnostic
    variants = _host_wrapped_variants(line)
    assert variants
    for variant in variants:
        assert diagnostic in _console_text(variant), variant


@pytest.mark.parametrize(
    "diagnostic", _WRAP_EXPOSED_DIAGNOSTICS, ids=range(len(_WRAP_EXPOSED_DIAGNOSTICS))
)
def test_the_wrap_sweep_is_not_vacuous(diagnostic: str) -> None:
    """The counter-proof: the sweep must break the line at EVERY position it claims.

    Without it the test above would pass on a `_console_text` that does nothing -
    the defect this task exists to remove, not to reproduce. The first version of
    this counter-proof only asked that SOME variant break the raw match, and a
    mutation that deleted the whole per-position sweep survived it: the two
    break-everywhere variants alone kept it green. So the assertion is positional.
    """
    prefix = _WARNING_PREFIXES[0]
    line = prefix + diagnostic
    variants = _host_wrapped_variants(line)

    for index, char in enumerate(diagnostic):
        if char != " ":
            continue
        cut = len(prefix) + index
        assert any(
            variant.startswith(line[: cut + 1])
            and variant[cut + 1 : cut + 2] in {"\r", "\n"}
            for variant in variants
        ), f"no variant breaks at offset {index}"

    assert any(diagnostic not in variant for variant in variants), diagnostic


@pytest.mark.parametrize(
    "diagnostic", _WRAP_EXPOSED_DIAGNOSTICS, ids=range(len(_WRAP_EXPOSED_DIAGNOSTICS))
)
def test_normalization_does_not_rescue_a_changed_diagnostic(diagnostic: str) -> None:
    """Negative control: dropping ANY single word must still fail the assertion.

    Whitespace normalization must not become a second false green by matching
    output the launcher never produced.
    """
    words = diagnostic.split(" ")
    for index in range(len(words)):
        mutated = " ".join(words[:index] + words[index + 1 :])
        assert diagnostic not in _console_text(
            _WARNING_PREFIXES[0] + mutated
        ), words[index]


def test_collapsing_whitespace_joins_across_line_boundaries() -> None:
    """The named cost of normalization, and why fragments became whole sentences.

    Collapsing whitespace cannot tell a wrapped record from two unrelated lines:
    either way it joins the last word of one to the first word of the next. A
    two-word fragment can therefore be reassembled out of output in which nothing
    said it - a false green introduced by the fix itself. The boundary is real
    here and not hypothetical: these launchers print unprefixed lines beside
    warnings (the `Write-Host` banner, and the `[Console]::Out.Write` sentinels
    these tests rely on).

    A whole diagnostic sentence is not reconstructible that way. That is the
    reason every assertion above was widened from a fragment to the full sentence,
    and this test holds that reasoning to the code instead of to a comment.
    """
    two_lines = (
        "Ownership could not be proven and nothing was not\n"
        "stopped, which begins an unrelated line.\n"
    )
    assert "not stopped" in _console_text(two_lines)
    assert (
        "Backend: port 18000 is listening on PID 202 but no ownership metadata"
        " exists. The listener was not stopped."
    ) not in _console_text(two_lines)


def test_pinned_diagnostics_are_still_the_text_the_launchers_emit() -> None:
    """A reword in a launcher must redden the pin rather than silently orphan it."""
    full_stack = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")
    run_local = _RUN_LOCAL.read_text(encoding="utf-8-sig")

    assert (
        'Write-Warning "$($state.Service.Name): $($state.Reason). '
        'The listener was not stopped."' in full_stack
    )
    assert (
        "Write-Warning 'Service ownership changed during final preflight."
        " No process was stopped.'" in full_stack
    )
    assert (
        "Write-Warning 'One or more services were not stopped because ownership"
        " was not proven.'" in full_stack
    )
    assert (
        'Write-Warning "$($state.Service.Name): $($state.Reason). '
        'run_local did not stop or adopt it."' in run_local
    )
    assert (
        'Write-Warning "$($state.Service.Name): removed full-stack metadata only'
        ' after its process identity was proven stale."' in run_local
    )


def _unnormalized_output_assertions(source: str) -> tuple[str, ...]:
    """Report `in`/`not in` assertions on process output that skip `_console_text`.

    The guard exists because fixing the twelve known sites closes the instances
    and not the class: the next assertion written against launcher output would
    reintroduce it. `test_the_guard_detects_an_unnormalized_assertion` proves this
    function can actually see one.
    """
    findings: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assert):
            continue
        for compare in ast.walk(node.test):
            if not isinstance(compare, ast.Compare) or len(compare.ops) != 1:
                continue
            operator = compare.ops[0]
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            haystack = compare.comparators[0]
            reads_output = any(
                (isinstance(inner, ast.Attribute) and inner.attr in {"stdout", "stderr"})
                or (isinstance(inner, ast.Name) and inner.id == "combined_output")
                for inner in ast.walk(haystack)
            )
            if not reads_output:
                continue
            normalized = any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_console_text"
                for inner in ast.walk(haystack)
            )
            if normalized:
                continue
            needle = compare.left
            if isinstance(needle, ast.Constant) and isinstance(needle.value, str):
                if " " not in needle.value:
                    continue
                if needle.value in _RAW_OUTPUT_ASSERTIONS_BY_DESIGN:
                    continue
                findings.append(f"line {compare.lineno}: {needle.value!r}")
            elif isinstance(operator, ast.In):
                # A non-literal needle cannot be inspected for spaces, so it is
                # treated as exposed. `not in` redaction checks are exempt: their
                # needles are URLs and tokens, and hiding one behind a wrap would
                # need a space the secrets do not contain.
                findings.append(f"line {compare.lineno}: non-literal needle")
    return tuple(findings)


def test_no_launcher_output_assertion_skips_console_normalization() -> None:
    findings = _unnormalized_output_assertions(
        Path(__file__).read_text(encoding="utf-8")
    )
    assert findings == (), findings


def test_the_guard_detects_an_unnormalized_assertion() -> None:
    """Counter-test: a guard that cannot fail is the subject of programme 014."""
    offending = (
        "def t(result):\n"
        "    assert 'not stopped' in result.stdout\n"
    )
    assert _unnormalized_output_assertions(offending)

    accepted = (
        "def t(result):\n"
        "    assert 'not stopped' in _console_text(result.stdout)\n"
    )
    assert _unnormalized_output_assertions(accepted) == ()

    no_space = (
        "def t(result):\n"
        "    assert 'unowned-safe' in result.stdout\n"
    )
    assert _unnormalized_output_assertions(no_space) == ()
