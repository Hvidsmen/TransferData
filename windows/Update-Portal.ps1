# Windows PowerShell 5.1 compatible. Keep this file ASCII (task names use Unicode code points).
[CmdletBinding()]
param(
    [string[]]$TaskName = @(),
    [string]$TaskPath = "\",
    [int]$WaitSeconds = 600,
    [string]$StatusFile = "",
    [string]$RequestId = ""
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$state = Join-Path $projectRoot ".state"
$flag = Join-Path $state "maintenance.flag"
$backupRoot = Join-Path $projectRoot ".backups"
$savedStateDir = $env:TRANSFERDATA_STATE_DIR
$env:TRANSFERDATA_STATE_DIR = $state
$changed = @()
$stopped = $false
$mutationStarted = $false
$flagCreated = $false
$backup = $null

$script:updatePhase = "check"
$script:updateStep = 0
$script:updateJournal = @()
$script:updateRequester = ""
if ($StatusFile) {
    $expected = Join-Path $state ("portal-update\runs\" + $RequestId + ".json")
    if ($RequestId -notmatch '^[a-f0-9]{32}$' -or [IO.Path]::GetFullPath($StatusFile) -ne [IO.Path]::GetFullPath($expected)) { throw "Invalid update status path." }
    try {
        $initial = Get-Content -LiteralPath $StatusFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($initial.id -eq $RequestId) { $script:updateJournal = @($initial.events); $script:updateRequester = $initial.requested_by }
    } catch { Write-Warning "Initial progress metadata was unavailable." }
}
function Write-UpdateStatus {
    param([string]$Phase, [int]$Step, [string]$State = "running")
    $script:updatePhase = $Phase
    $script:updateStep = $Step
    if (-not $StatusFile) { return }
    $now = [DateTime]::UtcNow.ToString('o')
    $script:updateJournal += @{phase=$Phase; at=$now}
    $payload = @{version=1; id=$RequestId; state=$State; phase=$Phase; step=$Step; total=11; updated_at=$now; requested_by=$script:updateRequester; recovery_required=([bool]$mutationStarted -and $State -eq 'failed'); events=@($script:updateJournal | Select-Object -Last 40)}
    $temp = $StatusFile + "." + [guid]::NewGuid().ToString('N') + ".tmp"
    $previous = $temp + ".old"
    try {
        $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temp -Encoding UTF8
        if (Test-Path -LiteralPath $StatusFile) { [IO.File]::Replace($temp, $StatusFile, $previous) }
        else { [IO.File]::Move($temp, $StatusFile) }
    } catch { Write-Warning "Could not publish update progress. The update itself continues." }
    finally { Remove-Item -LiteralPath $temp, $previous -ErrorAction SilentlyContinue }
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE). Update stopped." }
}

function Restore-TaskStates {
    param([switch]$StartEnabled)
    foreach ($item in $changed) {
        if ($item.Enabled -or $item.Running) { Enable-ScheduledTask -TaskName $item.Name -TaskPath $TaskPath | Out-Null }
        if (($item.Running -or ($StartEnabled -and $item.Enabled)) -and -not (Test-TaskRunning $item.Name)) { Start-ScheduledTask -TaskName $item.Name -TaskPath $TaskPath }
        if (-not $item.Enabled) { Disable-ScheduledTask -TaskName $item.Name -TaskPath $TaskPath | Out-Null }
    }
}

function Test-TaskRunning {
    param([string]$Name)
    $fullName = $TaskPath.TrimEnd('\') + '\' + $Name
    foreach ($instance in $scheduler.GetRunningTasks(0)) {
        if ($instance.Path -eq $fullName) { return $true }
    }
    return $false
}

try {
    Write-UpdateStatus "check" 0
    if ($WaitSeconds -lt 5) { throw "WaitSeconds must be at least 5." }
    if (Test-Path $flag) { throw "A maintenance flag already exists. Review the previous update before continuing." }
    if (-not $TaskName.Count) {
        $dash = [char]0x2014
        $TaskName = @("ETL Dev $dash Web", "ETL Dev $dash TransferData", "ETL Dev $dash ERP")
        $pythonTask = Get-ScheduledTask -TaskName "ETL Dev $dash Python" -TaskPath $TaskPath -ErrorAction SilentlyContinue
        if ($pythonTask) { $TaskName += $pythonTask.TaskName }
    }
    if ($TaskName.Count -lt 3 -or $TaskName.Count -gt 4) { throw "Specify web, TransferData, ERP and optionally Python task names." }
    $tasks = @($TaskName | ForEach-Object { Get-ScheduledTask -TaskName $_ -TaskPath $TaskPath -ErrorAction Stop })
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $modes = @{}
    foreach ($task in $tasks) {
        $account = New-Object System.Security.Principal.NTAccount($task.Principal.UserId)
        try { $sid = $account.Translate([Security.Principal.SecurityIdentifier]).Value }
        catch { $sid = $task.Principal.UserId }
        if ($sid -ne $identity) { throw "Run this script as the Windows account used by all ETL Dev tasks." }
        $arguments = ($task.Actions | ForEach-Object { $_.Arguments }) -join " "
        if ($arguments.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
            $arguments -notmatch 'Start-Panel\.ps1' -or
            $arguments -notmatch '-Mode\s+["'']?(python-worker|erp-worker|worker|web)\b') {
            throw "Task '$($task.TaskName)' does not launch Start-Panel.ps1 from this project."
        }
        if ($modes.ContainsKey($Matches[1])) { throw "Duplicate task mode: $($Matches[1])." }
        $modes[$Matches[1]] = $task.TaskName
    }
    foreach ($required in @("web", "worker", "erp-worker")) {
        if (-not $modes.ContainsKey($required)) { throw "Required task mode is missing: $required." }
    }
    $branch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") { throw "Update must run from the main branch." }
    Write-UpdateStatus "fetch" 1
    Invoke-Checked "git" @("fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
    & git merge-base --is-ancestor HEAD refs/remotes/origin/main
    if ($LASTEXITCODE -ne 0) { throw "Local main has diverged. Resolve it manually; no reset will be performed." }
    $incoming = @(& git diff --name-only HEAD refs/remotes/origin/main)
    $local = @(& git diff --name-only HEAD) + @(& git ls-files --others --exclude-standard)
    if (@($local | Where-Object { $incoming -contains $_ }).Count) {
        throw "Local files overlap the incoming update. Preserve/resolve them before updating."
    }
    $python = "py"
    $useVenv = Test-Path "$projectRoot\.venv\Scripts\transferdata.exe"
    if ($useVenv) { $python = "$projectRoot\.venv\Scripts\python.exe" }
    # These checks deliberately do not print process environment or credential files.
    Invoke-Checked $python @("-c", "import transferdata.web.maintenance")
    New-Item -ItemType Directory -Force $state | Out-Null
    Remove-Item (Join-Path $state "paused-transferdata"), (Join-Path $state "paused-erp"), (Join-Path $state "paused-python") -ErrorAction SilentlyContinue
    Write-UpdateStatus "pause" 2
    New-Item -ItemType File $flag -ErrorAction Stop | Out-Null
    $flagCreated = $true
    foreach ($task in $tasks) {
        $changed += [pscustomobject]@{Name=$task.TaskName; Enabled=[bool]$task.Settings.Enabled; Running=(Test-TaskRunning $task.TaskName)}
        Disable-ScheduledTask -TaskName $task.TaskName -TaskPath $TaskPath | Out-Null
    }
    # Keep the UI available while workers finish; all new writes are paused.
    Write-UpdateStatus "wait" 3
    Write-Host "Waiting for running transfers to finish. No data transaction will be interrupted."
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        $ready = $true
        foreach ($pair in @(@("worker", "transferdata"), @("erp-worker", "erp"), @("python-worker", "python"))) {
            if ($modes.ContainsKey($pair[0]) -and (Test-TaskRunning $modes[$pair[0]]) -and -not (Test-Path (Join-Path $state ("paused-" + $pair[1])))) { $ready = $false }
        }
        if ($ready) { break }
        if ((Get-Date) -gt $deadline) { throw "Workers did not acknowledge pause in time. Existing workers may need a one-time restart to load maintenance support." }
        Start-Sleep -Seconds 2
    } while ($true)
    Write-UpdateStatus "backup" 4
    foreach ($task in $tasks) {
        if (Test-TaskRunning $task.TaskName) { Stop-ScheduledTask -TaskName $task.TaskName -TaskPath $TaskPath }
    }
    $deadline = (Get-Date).AddSeconds(30)
    while (@($tasks | Where-Object { Test-TaskRunning $_.TaskName }).Count) {
        if ((Get-Date) -gt $deadline) { throw "A task did not stop. No code or database changes were made." }
        Start-Sleep -Seconds 1
    }
    $stopped = $true
    $backup = Join-Path $backupRoot ((Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Force $backup | Out-Null
    # Restrict the backup: it includes the Django key and encrypted DPAPI credential file.
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($identity, "S-1-5-18", "S-1-5-32-544")) {
        $principal = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($principal,"FullControl","ContainerInherit,ObjectInherit","None","Allow")
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $backup -AclObject $acl
    $backupState = Join-Path $backup "state"
    New-Item -ItemType Directory -Force $backupState | Out-Null
    # The updater owns exclusive control locks; runtime update files are not application state.
    Get-ChildItem -LiteralPath $state -Force | Where-Object { $_.Name -ne "portal-update" } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $backupState -Recurse
    }
    foreach ($transient in @("maintenance.flag", "paused-transferdata", "paused-erp", "paused-python")) {
        Remove-Item -LiteralPath (Join-Path (Join-Path $backup "state") $transient) -ErrorAction SilentlyContinue
    }
    if (Test-Path "config.json") { Copy-Item -LiteralPath "config.json" -Destination $backup }
    $changed | ConvertTo-Json | Set-Content (Join-Path $backup "task-states.json") -Encoding UTF8
    Invoke-Checked "git" @("archive", "HEAD", "--output", (Join-Path $backup "source.tar"))
    # Preserve local files that git will leave in place, without printing their contents.
    foreach ($file in $local) {
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            $destination = Join-Path (Join-Path $backup "local-files") $file
            New-Item -ItemType Directory -Force (Split-Path -Parent $destination) | Out-Null
            Copy-Item -LiteralPath $file -Destination $destination
        }
    }
    Write-Host "Backup created: $backup"
    Write-UpdateStatus "install" 5
    $mutationStarted = $true
    Invoke-Checked "git" @("merge", "--ff-only", "refs/remotes/origin/main")
    if ($useVenv) { Invoke-Checked "py" @("-m", "uv", "sync", "--locked") }
    else { Invoke-Checked "py" @("-m", "pip", "install", "--user", "--no-build-isolation", ".") }
    Write-UpdateStatus "migrate" 6
    Invoke-Checked $python @("-m", "transferdata.web", "migrate", "--noinput")
    Write-UpdateStatus "static" 7
    Invoke-Checked $python @("-m", "transferdata.web", "collectstatic", "--noinput")
    Write-UpdateStatus "validate" 8
    Invoke-Checked $python @("-m", "transferdata.web", "check")
    Write-UpdateStatus "helper" 9
    if (Test-Path (Join-Path $state "onec-helper.json")) {
        Invoke-Checked "powershell.exe" @("-NoProfile", "-NonInteractive", "-File", (Join-Path $projectRoot "windows\Install-1CMetadata.ps1"), "-UseExisting")
    }
    Write-UpdateStatus "restart" 10
    $serviceStart = [DateTime]::UtcNow.ToString('o')
    Restore-TaskStates -StartEnabled
    # Verify new worker heartbeats while maintenance still prevents data transfers.
    $healthArgs = @('-m', 'transferdata.web', 'wait_worker_health', '--since', $serviceStart)
    $checkWorkers = 0
    foreach ($pair in @(@('worker', 'sql'), @('erp-worker', 'onec'), @('python-worker', 'python'))) {
        if ($modes.ContainsKey($pair[0])) {
            $taskState = $changed | Where-Object { $_.Name -eq $modes[$pair[0]] }
            if ($taskState.Enabled -or $taskState.Running) {
                $healthArgs += @('--worker', $pair[1])
                $checkWorkers++
            }
        }
    }
    if ($checkWorkers) { Invoke-Checked $python $healthArgs }
    foreach ($item in $changed) {
        if (($item.Enabled -or $item.Running) -and -not (Test-TaskRunning $item.Name)) {
            throw "Service '$($item.Name)' exited after restart. Update is not healthy."
        }
    }
    Remove-Item -LiteralPath $flag
    $flagCreated = $false
    Write-UpdateStatus "complete" 11 "success"
    Write-Host "ETL Dev updated. All enabled services were started; disabled services remain disabled."
} catch {
    if (-not $mutationStarted) {
        if ($flagCreated) { Remove-Item -LiteralPath $flag -ErrorAction SilentlyContinue; $flagCreated = $false }
        Restore-TaskStates
    } else {
        foreach ($task in $tasks) {
            try {
                if (Test-TaskRunning $task.TaskName) { Stop-ScheduledTask -TaskName $task.TaskName -TaskPath $TaskPath }
                Disable-ScheduledTask -TaskName $task.TaskName -TaskPath $TaskPath | Out-Null
            } catch { Write-Warning "Could not stop/disable task '$($task.TaskName)'. Check it in Task Scheduler." }
        }
        Write-Warning "Update failed after code/dependency changes. Tasks remain stopped and disabled. Backup: $backup"
        Write-Warning "Repair the failure before removing .state\maintenance.flag and enabling/restarting the tasks."
    }
    Write-UpdateStatus $script:updatePhase $script:updateStep "failed"
    Write-Error $_
    exit 1
} finally {
    $env:TRANSFERDATA_STATE_DIR = $savedStateDir
}
