# Fresh, independent Windows installation. Run elevated from the YDS checkout.
[CmdletBinding()]
param([switch]$OpenFirewall)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run PowerShell as administrator, under the intended portal service account.' }
function Invoke-YdsChecked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Installation stopped: $Program exited with $LASTEXITCODE." }
}
if (Get-NetTCPConnection -State Listen -LocalPort 8003 -ErrorAction SilentlyContinue) { throw 'Port 8003 is already in use. Stop the conflicting service or use another server.' }
$state = Join-Path $projectRoot '.state'
$profile = Join-Path $state 'deployment.json'
if (-not (Test-Path $profile) -and (Test-Path $state) -and @(Get-ChildItem $state -Force).Count) {
    throw 'Existing state found without a YDS profile. Use a fresh checkout; do not copy another company state.'
}
if (-not (Test-Path $profile)) {
    New-Item -ItemType Directory -Force $state | Out-Null
    @{version=1;profile='yds'} | ConvertTo-Json | Set-Content -LiteralPath $profile -Encoding UTF8
}
. (Join-Path $PSScriptRoot 'Portal-Profile.ps1')
if (-not $portalIsYds) { throw 'This installer is only for the YDS profile.' }
$dash = [char]0x2014
foreach ($suffix in @('Web','ERP','Python','Update')) {
    if (Get-ScheduledTask -TaskName "$portalTaskPrefix $dash $suffix" -ErrorAction SilentlyContinue) {
        throw 'A YDS task already exists. Nothing was overwritten; use Update-Portal.ps1 for updates.'
    }
}
# Restrict new secrets and the local service database to the installation account.
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @($identity.User.Value, 'S-1-5-18', 'S-1-5-32-544')) {
    $who = New-Object System.Security.Principal.SecurityIdentifier($sid)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($who,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $state -AclObject $acl
$env:TRANSFERDATA_STATE_DIR = $state
$env:TRANSFERDATA_ALLOWED_HOSTS = $portalAllowedHosts
$env:TRANSFERDATA_CSRF_ORIGINS = 'http://192.168.70.9:8003'
$bootstrapPython = Get-PortalPython
Invoke-YdsChecked $bootstrapPython @('-m','uv','sync','--locked')
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
Invoke-YdsChecked $python @('-m','transferdata.web','migrate','--noinput')
Invoke-YdsChecked $python @('-m','transferdata.web','collectstatic','--noinput')
Invoke-YdsChecked $python @('-m','transferdata.web','check')
Invoke-YdsChecked 'powershell.exe' @('-NoProfile','-File',(Join-Path $PSScriptRoot 'Install-1CMetadata.ps1'))
& $python -c "import os,sys; os.environ['DJANGO_SETTINGS_MODULE']='transferdata.web.settings'; import django; django.setup(); from django.contrib.auth import get_user_model; sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 10)"
if ($LASTEXITCODE -eq 10) {
    Write-Host 'Create a new portal administrator. Do not reuse another company credentials.'
    Invoke-YdsChecked $python @('-m','transferdata.web','createsuperuser')
} elseif ($LASTEXITCODE -ne 0) { throw 'Could not check the local portal administrator.' }
$credential = Get-Credential -UserName $identity.Name -Message 'Windows account for etl-dev-yds services'
if ($null -eq $credential) { throw 'Installation cancelled before task registration.' }
$created = @()
$firewallCreated = $false
try {
    $account = New-Object System.Security.Principal.NTAccount($credential.UserName)
    if ($account.Translate([Security.Principal.SecurityIdentifier]).Value -ne $identity.User.Value) { throw 'Use the current Windows account.' }
    foreach ($pair in @(@('Web','web'), @('ERP','erp-worker'), @('Python','python-worker'))) {
        $name = "$portalTaskPrefix $dash $($pair[0])"
        $arguments = '-NoProfile -NonInteractive -File "' + (Join-Path $PSScriptRoot 'Start-Panel.ps1') + '" -Mode ' + $pair[1]
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $projectRoot
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $taskSettings -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Highest | Out-Null
        $created += $name
    }
    & (Join-Path $PSScriptRoot 'Install-PortalUpdater.ps1') -Credential $credential
    $created += "$portalTaskPrefix $dash Update"
    if ($OpenFirewall) {
        if (-not (Get-NetFirewallRule -Name 'etl-dev-yds-web' -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -Name 'etl-dev-yds-web' -DisplayName 'etl-dev-yds port 8003' -Direction Inbound -Protocol TCP -LocalPort 8003 -Action Allow -Profile Domain,Private -RemoteAddress LocalSubnet | Out-Null
            $firewallCreated = $true
        }
    }
    foreach ($suffix in @('Web','ERP','Python')) { Start-ScheduledTask -TaskName "$portalTaskPrefix $dash $suffix" }
    $since = [DateTime]::UtcNow.AddSeconds(-5).ToString('o')
    Invoke-YdsChecked $python @('-m','transferdata.web','wait_worker_health','--since',$since,'--worker','onec','--worker','python')
    $response = Invoke-WebRequest 'http://127.0.0.1:8003/login/' -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -ne 200 -or $response.Content -notmatch 'etl-dev-yds') { throw 'YDS web login did not respond as expected.' }
} catch {
    # Only tasks created in this attempt are removed; state is retained for diagnosis.
    foreach ($name in $created) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
    if ($created -contains "$portalTaskPrefix $dash Update") {
        Remove-Item -LiteralPath (Join-Path $state 'portal-updater.json') -ErrorAction SilentlyContinue
    }
    if ($firewallCreated) { Remove-NetFirewallRule -Name 'etl-dev-yds-web' -ErrorAction SilentlyContinue }
    throw
} finally { $credential = $null }
Write-Host 'etl-dev-yds installed: http://192.168.70.9:8003/ . Configure new SQL Server connections in the portal.'
