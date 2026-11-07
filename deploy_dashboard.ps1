# Deploy the rebuilt Q-Tracker (dashboard actions + OpenAI Platform billing) to
# the per-user install location. Keeps a full dated backup of the previous build.
$ErrorActionPreference = 'Stop'

$repo = 'C:\Users\QUEEN\Projects\Q-Tracker-OpenAI'
$source = Join-Path $repo 'dist\Q-Tracker'
$install = Join-Path $env:LOCALAPPDATA 'Programs\Q-Tracker'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:LOCALAPPDATA ('Programs\Q-Tracker-before-dashboard-' + $stamp)

if (-not (Test-Path -LiteralPath (Join-Path $source 'Q-Tracker.exe'))) {
    throw "Build output missing at $source"
}

$running = @(Get-Process -Name 'Q-Tracker' -ErrorAction SilentlyContinue)
foreach ($process in $running) {
    Write-Output ("Stopping PID " + $process.Id)
    Stop-Process -Id $process.Id -Force
}
if ($running.Count -gt 0) {
    Start-Sleep -Seconds 3
}

if (Test-Path -LiteralPath $install) {
    Move-Item -LiteralPath $install -Destination $backup
    Write-Output ("Previous install moved to " + $backup)
}

Copy-Item -Path (Join-Path $source '*') -Destination (New-Item -ItemType Directory -Path $install -Force).FullName -Recurse -Force

$builtHash = (Get-FileHash -LiteralPath (Join-Path $source 'Q-Tracker.exe')).Hash
$installedHash = (Get-FileHash -LiteralPath (Join-Path $install 'Q-Tracker.exe')).Hash
if ($builtHash -ne $installedHash) {
    throw ("Installed exe hash mismatch: " + $installedHash + " != " + $builtHash)
}

Set-Content -LiteralPath (Join-Path $install 'SOURCE.txt') -Encoding ascii -Value @(
    'Source: https://github.com/queenleonidasth/Q-Tracker (commit 46bf8ae2, 2026-10-09)',
    'Variant: OpenAI Platform billing reconstructed + dashboard actions',
    ('Built: ' + $stamp),
    ('Build source: ' + $repo),
    'Added: openai_platform provider (official Costs/Usage APIs, Windows Credential Manager key)',
    'Added: hover dashboard now carries the former right-click commands as clickable rows',
    'Changed: Antigravity is hidden from the taskbar readout and the hover dashboard',
    'Removed: Playwright billing-page scraping (it was failing and needs a browser download)',
    ('Previous install backup: ' + $backup),
    ('Exe SHA-256: ' + $builtHash)
)

Start-Process -FilePath (Join-Path $install 'Q-Tracker.exe')
Start-Sleep -Seconds 12

$started = @(Get-Process -Name 'Q-Tracker' -ErrorAction SilentlyContinue)
if ($started.Count -eq 0) {
    throw 'Q-Tracker did not start after deployment'
}
Write-Output ("Running PID " + ($started[0].Id))
Write-Output ("Exe SHA-256 " + $installedHash)
Write-Output '--- last log lines ---'
Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'Q-Tracker\logs\app.log') -Tail 4
