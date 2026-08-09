from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_RUN_FULL_STACK = _ROOT / "scripts" / "run_full_stack.ps1"
_POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def test_full_stack_summary_uses_only_the_safe_database_display_value() -> None:
    source = _RUN_FULL_STACK.read_text(encoding="utf-8-sig")

    assert 'Write-Host "$SafeDatabaseDisplayUrl"' in source
    assert 'Write-Host "$EffectiveDatabaseUrl"' not in source


@pytest.mark.skipif(_POWERSHELL is None, reason="PowerShell is required")
def test_database_display_value_excludes_userinfo_and_query_secrets() -> None:
    sentinel_password = "phase7-sentinel-password"
    sentinel_query_secret = "phase7-sentinel-query-secret"
    raw_url = (
        "postgresql+asyncpg://phase7-user:"
        f"{sentinel_password}@db.example.test:5432/geov0"
        f"?sslmode=require&access_token={sentinel_query_secret}"
    )
    command = r"""
$source = Get-Content -LiteralPath $env:PHASE7_SCRIPT_PATH -Raw
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw ($parseErrors | ForEach-Object { $_.Message } | Out-String)
}
$functionAst = $ast.Find(
    {
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Get-SafeDatabaseDisplayUrl'
    },
    $true
)
if ($null -eq $functionAst) {
    throw 'Get-SafeDatabaseDisplayUrl was not found'
}
Invoke-Expression $functionAst.Extent.Text
$display = Get-SafeDatabaseDisplayUrl `
    -PythonExe $env:PHASE7_PYTHON_EXE `
    -DatabaseUrl $env:PHASE7_DATABASE_URL
[Console]::Out.Write($display)
"""
    environment = {
        **os.environ,
        "PHASE7_SCRIPT_PATH": str(_RUN_FULL_STACK),
        "PHASE7_PYTHON_EXE": sys.executable,
        "PHASE7_DATABASE_URL": raw_url,
    }

    result = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "postgresql+asyncpg://db.example.test:5432/geov0"
    assert "phase7-user" not in result.stdout
    assert sentinel_password not in result.stdout
    assert sentinel_query_secret not in result.stdout
    assert "access_token" not in result.stdout
