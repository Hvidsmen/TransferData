# Fixed on-demand scheduled-task entry point. No arguments are accepted from the browser.
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$directory = Join-Path $projectRoot ".state\portal-update"
$requestPath = Join-Path $directory "request.json"
$lockPath = Join-Path $directory "request.lock"
$runner = $null
$request = $null
$transcript = $false
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'Never'
$env:PIP_NO_INPUT = '1' 
try {
    $runner = [IO.File]::Open((Join-Path $directory "runner.lock"), 'OpenOrCreate', 'ReadWrite', 'None')
    $request = Get-Content -LiteralPath $requestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($request.version -ne 1 -or $request.id -notmatch '^[a-f0-9]{32}$') { throw "Invalid update request." }
    if ((Get-Content -LiteralPath $lockPath -Raw).Trim() -ne $request.id) { throw "Update request lock mismatch." }
    $statusFile = Join-Path $directory ("runs\" + $request.id + ".json")
    Start-Transcript -Path (Join-Path $directory ("runs\" + $request.id + ".log")) -Append | Out-Null
    $transcript = $true
    & (Join-Path $projectRoot "windows\Update-Portal.ps1") -StatusFile $statusFile -RequestId $request.id
    if (-not $?) { exit 1 }
} catch {
    if ($request -and $request.id -match '^[a-f0-9]{32}$') {
        $path = Join-Path $directory ("runs\" + $request.id + ".json")
        try {
            $state = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($state.state -ne 'failed' -and $state.state -ne 'success') {
                $state.state = 'failed'
                $state.updated_at = [DateTime]::UtcNow.ToString('o')
                $state | Add-Member -NotePropertyName recovery_required -NotePropertyValue ([bool](Test-Path (Join-Path $projectRoot '.state\maintenance.flag'))) -Force
                $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8
            }
        } catch { Write-Warning "Could not record updater failure." }
    }
    throw
} finally {
    if ($transcript) { Stop-Transcript | Out-Null }
    if ($runner) {
        if ($request -and (Test-Path -LiteralPath $lockPath)) {
            if ((Get-Content -LiteralPath $lockPath -Raw).Trim() -eq $request.id) { Remove-Item -LiteralPath $lockPath -Force }
        }
        $runner.Dispose()
    }
}
