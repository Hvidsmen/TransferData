# Run once elevated as the same Windows account used by the ETL Dev tasks.
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run PowerShell as administrator." }
$name = "ETL Dev $([char]0x2014) Update"
if (Get-ScheduledTask -TaskName $name -TaskPath "\" -ErrorAction SilentlyContinue) {
    throw "Updater task already exists. No task was replaced."
}
$credential = Get-Credential -UserName $identity.Name -Message "Windows account for unattended ETL Dev updates"
if ($null -eq $credential) { throw "Installation cancelled." }
$created = $false
try {
    $account = New-Object System.Security.Principal.NTAccount($credential.UserName)
    if ($account.Translate([Security.Principal.SecurityIdentifier]).Value -ne $identity.User.Value) { throw "Use the current ETL Dev Windows account." }
    $args = '-NoProfile -NonInteractive -File "' + (Join-Path $projectRoot 'windows\Invoke-PortalUpdate.ps1') + '"'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $args -WorkingDirectory $projectRoot
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $name -TaskPath "\" -Action $action -Settings $settings -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Highest | Out-Null
    $created = $true
    # The normal web token of the same account may read and start, not edit, this elevated task.
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $task = $scheduler.GetFolder('\').GetTask($name)
    $task.SetSecurityDescriptor(('D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;' + $identity.User.Value + ')'), 0)
    $state = Join-Path $projectRoot ".state"
    $updates = Join-Path $state "portal-update"
    New-Item -ItemType Directory -Force $updates | Out-Null
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($identity.User.Value, "S-1-5-18", "S-1-5-32-544")) {
        $who = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($who,"FullControl","ContainerInherit,ObjectInherit","None","Allow")
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $updates -AclObject $acl
    New-Item -ItemType Directory -Force $state | Out-Null
    @{version=1; task_name=$name; project_root=$projectRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'portal-updater.json') -Encoding UTF8
} catch {
    if ($created) { Unregister-ScheduledTask -TaskName $name -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue }
    throw
} finally {
    $credential = $null
}
Write-Host "Browser updater installed. Open ETL Dev -> Portal update. No update was started."
