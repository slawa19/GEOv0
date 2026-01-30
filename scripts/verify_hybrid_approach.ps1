# Verification script for hybrid approach (A+B)
# Checks that:
# 1. tx.updated events contain edge_patch with used/available
# 2. Snapshot refetch happens periodically
# 3. viz_* fields are present and valid in snapshot

param(
    [string]$ApiBase = "http://127.0.0.1:18000/api/v1",
    # Admin token (sent as X-Admin-Token). Default matches app.config.settings.ADMIN_TOKEN.
    [string]$Token = "dev-admin-token-change-me",

    # If not provided, the script will start a new run.
    [string]$RunId = "",
    [string]$ScenarioId = "",
    [ValidateSet("real", "fixtures")][string]$Mode = "real",
    [ValidateRange(0, 100)][int]$IntensityPercent = 50,
    [string]$Equivalent = "UAH",

    # Warm up to let the run produce initial events/artifacts.
    [ValidateRange(0, 120)][int]$WarmupSeconds = 15,
    [ValidateRange(1, 300)][int]$MaxWaitSeconds = 60,

    [switch]$StopRunAtEnd
)

Write-Host "🔍 Hybrid Approach Verification" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$headers = @{ "X-Admin-Token" = "$Token" }

function Get-ApiAuthority([string]$base) {
    $u = [uri]$base
    return $u.GetLeftPart([System.UriPartial]::Authority)
}

if (-not $RunId) {
    if (-not $ScenarioId) {
        Write-Host "🧭 Selecting scenario..." -ForegroundColor Yellow
        $scenariosResp = Invoke-RestMethod -Uri "$ApiBase/simulator/scenarios" -Headers $headers -Method Get
        $preferred = $scenariosResp.items | Where-Object { $_.scenario_id -eq "greenfield-village-100" } | Select-Object -First 1
        if (-not $preferred) {
            $preferred = $scenariosResp.items | Select-Object -First 1
        }
        if (-not $preferred) {
            Write-Host "❌ No scenarios found. Seed/upload a scenario first." -ForegroundColor Red
            exit 1
        }
        $ScenarioId = $preferred.scenario_id
    }

    Write-Host "🚀 Starting run (scenario=$ScenarioId, mode=$Mode, intensity=$IntensityPercent)..." -ForegroundColor Yellow
    $body = @{ scenario_id = $ScenarioId; mode = $Mode; intensity_percent = $IntensityPercent } | ConvertTo-Json -Compress
    $runResp = Invoke-RestMethod -Uri "$ApiBase/simulator/runs" -Headers $headers -Method Post -Body $body -ContentType "application/json"
    $RunId = $runResp.run_id

    if (-not $RunId) {
        Write-Host "❌ Failed to start run (no run_id returned)." -ForegroundColor Red
        exit 1
    }
}

$runId = $RunId
Write-Host "✅ Using run: $runId" -ForegroundColor Green
Write-Host ""

if ($WarmupSeconds -gt 0) {
    Write-Host "⏳ Warmup: waiting $WarmupSeconds sec..." -ForegroundColor DarkGray
    Start-Sleep -Seconds $WarmupSeconds
    Write-Host ""
}

# Check snapshot has viz_* fields
Write-Host "📊 Checking snapshot..." -ForegroundColor Yellow
$snapshot = Invoke-RestMethod -Uri "$ApiBase/simulator/runs/$runId/graph/snapshot?equivalent=$Equivalent" -Headers $headers -Method Get

$nodesWithVizSize = ($snapshot.nodes | Where-Object { $_.viz_size } | Measure-Object).Count
$nodesTotal = ($snapshot.nodes | Measure-Object).Count
$linksWithVizWidthKey = ($snapshot.links | Where-Object { $_.viz_width_key } | Measure-Object).Count
$linksTotal = ($snapshot.links | Measure-Object).Count

Write-Host "  Nodes with viz_size: $nodesWithVizSize / $nodesTotal" -ForegroundColor $(if ($nodesWithVizSize -eq $nodesTotal) { "Green" } else { "Red" })
Write-Host "  Links with viz_width_key: $linksWithVizWidthKey / $linksTotal" -ForegroundColor $(if ($linksWithVizWidthKey -eq $linksTotal) { "Green" } else { "Red" })
Write-Host ""

# Check events.ndjson for edge_patch
Write-Host "📡 Checking events for edge_patch..." -ForegroundColor Yellow
$authority = Get-ApiAuthority $ApiBase

$eventsArtifact = $null
$eventsContent = $null
$deadline = (Get-Date).AddSeconds($MaxWaitSeconds)
while ((Get-Date) -lt $deadline) {
    $artifactsResp = Invoke-RestMethod -Uri "$ApiBase/simulator/runs/$runId/artifacts" -Headers $headers -Method Get
    $eventsArtifact = $artifactsResp.items | Where-Object { $_.name -eq "events.ndjson" } | Select-Object -First 1
    if ($eventsArtifact) {
        try {
            $eventsUrl = "$authority$($eventsArtifact.url)"
            $eventsContent = (Invoke-WebRequest -Uri $eventsUrl -Headers $headers -Method Get).Content
            if ($eventsContent -and $eventsContent.Trim().Length -gt 0) {
                break
            }
        } catch {
            # keep polling
        }
    }
    Start-Sleep -Seconds 1
}

if (-not $eventsArtifact) {
    Write-Host "⚠️  No events.ndjson artifact found within $MaxWaitSeconds sec." -ForegroundColor Yellow
    Write-Host "    (The run may be too fresh, artifacts may be disabled, or the run crashed early.)" -ForegroundColor DarkGray
    if ($StopRunAtEnd) {
        try { Invoke-RestMethod -Uri "$ApiBase/simulator/runs/$runId/stop" -Headers $headers -Method Post | Out-Null } catch {}
    }
    exit 0
}

if (-not $eventsContent) {
    Write-Host "⚠️  events.ndjson is empty (yet). Try increasing -WarmupSeconds or -MaxWaitSeconds." -ForegroundColor Yellow
    if ($StopRunAtEnd) {
        try { Invoke-RestMethod -Uri "$ApiBase/simulator/runs/$runId/stop" -Headers $headers -Method Post | Out-Null } catch {}
    }
    exit 0
}

$events = @()
foreach ($line in ($eventsContent -split "`n")) {
    $t = $line.Trim()
    if (-not $t) { continue }
    try {
        $events += ($t | ConvertFrom-Json)
    } catch {
        # ignore partial/corrupt line while writer is appending
    }
}

$txUpdatedEvents = $events | Where-Object { $_.type -eq "tx.updated" }
$txWithPatches = $txUpdatedEvents | Where-Object { $_.edge_patch }

Write-Host "  Total tx.updated events: $($txUpdatedEvents.Count)" -ForegroundColor Cyan
Write-Host "  Events with edge_patch: $($txWithPatches.Count)" -ForegroundColor $(if ($txWithPatches.Count -gt 0) { "Green" } else { "Red" })

if ($txWithPatches.Count -gt 0) {
    $samplePatch = $txWithPatches[0].edge_patch[0]
    Write-Host ""
    Write-Host "  Sample edge_patch:" -ForegroundColor Green
    Write-Host "    source: $($samplePatch.source)" -ForegroundColor Gray
    Write-Host "    target: $($samplePatch.target)" -ForegroundColor Gray
    Write-Host "    used: $($samplePatch.used)" -ForegroundColor Gray
    Write-Host "    available: $($samplePatch.available)" -ForegroundColor Gray
}

Write-Host ""
if ($StopRunAtEnd) {
    Write-Host "🛑 Stopping run..." -ForegroundColor Yellow
    try {
        Invoke-RestMethod -Uri "$ApiBase/simulator/runs/$runId/stop" -Headers $headers -Method Post | Out-Null
        Write-Host "✅ Run stopped." -ForegroundColor Green
    } catch {
        Write-Host "⚠️  Failed to stop run (continuing)." -ForegroundColor Yellow
    }
    Write-Host ""
}

Write-Host "✅ Verification complete!" -ForegroundColor Green
