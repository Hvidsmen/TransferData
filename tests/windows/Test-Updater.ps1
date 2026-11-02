# No real scheduled tasks or updates are executed. Tests use a mock scheduler.
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
foreach ($name in @('Update-Portal.ps1','Install-PortalUpdater.ps1','Invoke-PortalUpdate.ps1','Start-Panel.ps1','Install-PythonWorker.ps1','Portal-Profile.ps1','Install-YDS.ps1')) {
 [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo ('windows/' + $name)),[ref]$tokens,[ref]$errors)|Out-Null
 if($errors.Count){throw ($errors|Out-String)}
}
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo 'windows/Update-Portal.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Parse failed'}
$definitions=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)
foreach($name in @('Write-UpdateStatus','Restore-TaskStates','Test-TaskRunning','Set-ERPWorkerSettings')) {
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
$TaskPath='\'
$scheduler=New-Object PSObject
$scheduler|Add-Member -MemberType ScriptMethod -Name GetRunningTasks -Value {param($unused) return @([pscustomobject]@{Path='\ERP'})}
if(-not (Test-TaskRunning 'ERP')){throw 'Root task path did not match the COM scheduler path'}
$TaskPath='\ETL\'
$scheduler=New-Object PSObject
$scheduler|Add-Member -MemberType ScriptMethod -Name GetRunningTasks -Value {param($unused) return @([pscustomobject]@{Path='\ETL\ERP'})}
if(-not (Test-TaskRunning 'ERP')){throw 'Nested task path did not match the COM scheduler path'}
$script:calls=@();$TaskPath='\'
$changed=@(@{Name='Web';Enabled=$true;Running=$true},@{Name='SQL';Enabled=$true;Running=$false},@{Name='ERP';Enabled=$false;Running=$false})
$scheduler=New-Object PSObject
$scheduler|Add-Member -MemberType ScriptMethod -Name GetRunningTasks -Value {param($unused) return @()}
function Enable-ScheduledTask {param($TaskName,$TaskPath) $script:calls+='enable:'+ $TaskName}
function Start-ScheduledTask {param($TaskName,$TaskPath) $script:calls+='start:'+ $TaskName}
function Disable-ScheduledTask {param($TaskName,$TaskPath) $script:calls+='disable:'+ $TaskName}
Restore-TaskStates
if(($script:calls -join ',') -ne 'enable:Web,start:Web,enable:SQL,disable:ERP'){throw 'Original service states were not preserved'}
$script:calls=@()
Restore-TaskStates -StartEnabled
if(($script:calls -join ',') -ne 'enable:Web,start:Web,enable:SQL,start:SQL,disable:ERP'){throw 'Enabled idle services were not started after success'}
$script:mockERP=[pscustomobject]@{Settings=[pscustomobject]@{Enabled=$false;ExecutionTimeLimit='PT72H';RestartCount=0;RestartInterval='PT0M';MultipleInstances='Parallel'};Principal='original-account';Actions='original-action'}
function Get-ScheduledTask {param($TaskName,$TaskPath) return $script:mockERP}
function Set-ScheduledTask {param($InputObject) $script:configuredERP=$InputObject}
Set-ERPWorkerSettings -Name 'ERP'
if($script:configuredERP.Settings.ExecutionTimeLimit -ne 'PT0S' -or $script:configuredERP.Settings.RestartCount -ne 999 -or $script:configuredERP.Settings.RestartInterval -ne 'PT1M'){throw 'ERP persistent-worker recovery settings missing'}
if($script:configuredERP.Settings.Enabled -or $script:configuredERP.Principal -ne 'original-account' -or $script:configuredERP.Actions -ne 'original-action'){throw 'ERP identity/action/enabled state changed'}
$script:mockERP=[pscustomobject]@{Settings=[pscustomobject]@{Enabled=$true;ExecutionTimeLimit='PT72H';RestartCount=0;RestartInterval='PT0M';MultipleInstances='IgnoreNew'};Principal=[pscustomobject]@{LogonType='Password'}}
$script:configuredERP=$null
Set-ERPWorkerSettings -Name 'ERP' -WarningAction SilentlyContinue
if($null -ne $script:configuredERP -or $script:mockERP.Settings.ExecutionTimeLimit -ne 'PT72H'){throw 'Password task was rewritten without credentials'}
$script:mockERP.Principal.LogonType='S4U'
function Set-ScheduledTask {param($InputObject,$ErrorAction) throw 'credential failure'}
Set-ERPWorkerSettings -Name 'ERP' -WarningAction SilentlyContinue
$savedProjectRoot=$projectRoot
$projectRoot=Join-Path ([IO.Path]::GetTempPath()) ('etl-yds-profile-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force (Join-Path $projectRoot '.state')|Out-Null
. (Join-Path $repo 'windows/Portal-Profile.ps1')
if($portalTaskPrefix -ne 'ETL Dev' -or $portalIsYds -or $portalRequiredModes -notcontains 'worker'){throw 'Default profile changed'}
@{version=1;profile='yds'}|ConvertTo-Json|Set-Content (Join-Path $projectRoot '.state/deployment.json') -Encoding UTF8
. (Join-Path $repo 'windows/Portal-Profile.ps1')
if($portalTaskPrefix -ne 'etl-dev-yds' -or -not $portalIsYds -or $portalRequiredModes -contains 'worker' -or $portalRequiredModes -notcontains 'python-worker'){throw 'YDS task requirements are wrong'}
if(($portalPrimaryTasks -join ',') -ne 'Web,ERP,Python' -or $portalAllowedHosts -match 'daichi|vm-dwh'){throw 'YDS inherited another company configuration'}
Remove-Item -LiteralPath $projectRoot -Recurse -Force
$projectRoot=$savedProjectRoot
Remove-Item -LiteralPath $StatusFile -ErrorAction SilentlyContinue
Write-Output 'Progress persistence, failed recovery marker and task-state restoration passed (mock scheduler).'
