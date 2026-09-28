param([string]$Name, [string[]]$Selector = @(), [switch]$NoMarker)
$ErrorActionPreference = 'Stop'
$R = '<repo>
$WT = '<repo>
$py = '<repo>
$out = Join-Path $R $Name
New-Item -ItemType Directory -Force -Path $out | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $out 'artifacts') | Out-Null
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_p025_cov'
$env:GEO_TEST_ALLOW_DB_RESET = '1'
$env:GEO_TEST_USE_MIGRATED_SCHEMA = '1'
$env:GEO_TEST_ARTIFACT_ROOT = (Join-Path $out 'artifacts')
$env:COVERAGE_FILE = (Join-Path $out '.coverage')
$env:COVERAGE_RCFILE = (Join-Path $R 'coveragerc')
Set-Location $WT
$args2 = @('-m','pytest','--basetemp',(Join-Path $out 'pytest'),'-o',"cache_dir=$(Join-Path $out 'cache')",'-q',
  "--cov=app","--cov-branch","--cov-context=test","--cov-config=$(Join-Path $R 'coveragerc')","--cov-report=",
  '--durations=0','--durations-min=0.05',"--junitxml=$(Join-Path $out 'junit.xml')")
if (-not $NoMarker) { $args2 += @('-m','not slow') }
if ($Selector.Count -gt 0) { $args2 += '--'; $args2 += $Selector }
$start = Get-Date
& $py @args2 *> (Join-Path $out 'pytest.log')
$code = $LASTEXITCODE
"exit=$code elapsed_s=$([int]((Get-Date)-$start).TotalSeconds)" | Tee-Object -FilePath (Join-Path $out 'exit.txt')
