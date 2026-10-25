# No real scheduled tasks or updates are executed. Tests use a mock scheduler.
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
foreach ($name in @('Update-Portal.ps1','Install-PortalUpdater.ps1','Invoke-PortalUpdate.ps1')) {
 [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo ('windows/' + $name)),[ref]$tokens,[ref]$errors)|Out-Null
 if($errors.Count){throw ($errors|Out-String)}
}
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo 'windows/Update-Portal.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failed'}
$definitions=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)
foreach($name in @('Write-UpdateStatus','Restore-TaskStates','Test-TaskRunning')) {
 $definition=$definitions|Where-Object{$_.Name -eq $name}
 Invoke-Expression ($definition.Extent.Text.Replace('catch { Write-Warning "Could not publish update progress. The update itself continues." }', 'catch { throw }'))
}
$RequestId='0123456789abcdef0123456789abcdef'
$StatusFile=Join-Path ([IO.Path]::GetTempPath()) ('etl-updater-test-'+[guid]::NewGuid().ToString('N')+'.json')
$script:updateJournal=@();$script:updateRequester='test-admin';$mutationStarted=$false
Write-UpdateStatus 'wait' 3
$state=Get-Content $StatusFile -Raw|ConvertFrom-Json
if($state.step -ne 3 -or $state.state -ne 'running'){throw 'Progress not persisted'}
$mutationStarted=$true
Write-UpdateStatus 'install' 5 'failed'
$state=Get-Content $StatusFile -Raw|ConvertFrom-Json
if(-not $state.recovery_required -or $state.events.Count -ne 2){throw 'Failure/recovery state missing'}
$script:calls=@();$TaskPath='\'
$changed=@(@{Name='Web';Enabled=$true;Running=$true},@{Name='SQL';Enabled=$true;Running=$false},@{Name='ERP';Enabled=$false;Running=$false})
$scheduler=New-Object PSObject
$scheduler|Add-Member -MemberType ScriptMethod -Name GetRunningTasks -Value {param($unused) return @()}
function Enable-ScheduledTask {param($TaskName,$TaskPath) $script:calls+='enable:'+ $TaskName}
function Start-ScheduledTask {param($TaskName,$TaskPath) $script:calls+='start:'+ $TaskName}
function Disable-ScheduledTask {param($TaskName,$TaskPath) $script:calls+='disable:'+ $TaskName}
Restore-TaskStates
if(($script:calls -join ',') -ne 'enable:Web,start:Web,enable:SQL,disable:ERP'){throw 'Original service states were not preserved'}
Remove-Item -LiteralPath $StatusFile -ErrorAction SilentlyContinue
Write-Output 'Progress persistence, failed recovery marker and task-state restoration passed (mock scheduler).'
