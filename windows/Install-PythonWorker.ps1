# Run once as the Windows account used by the other ETL Dev tasks, elevated.
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$dash = [char]0x2014
$name = "ETL Dev $dash Python"
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    throw "Python task already exists. No task was replaced."
}
$credential = Get-Credential -UserName ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -Message "Windows account for ETL Dev Python worker"
if ($null -eq $credential) { throw "Installation cancelled." }
$account = New-Object System.Security.Principal.NTAccount($credential.UserName)
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($account.Translate([Security.Principal.SecurityIdentifier]).Value -ne $currentSid) {
    throw "Use the current ETL Dev Windows account."
}
$arguments = '-NoProfile -NonInteractive -File "' + (Join-Path $projectRoot 'windows\Start-Panel.ps1') + '" -Mode python-worker'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
try {
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -User $credential.UserName -Password $credential.GetNetworkCredential().Password -RunLevel Highest | Out-Null
    Start-ScheduledTask -TaskName $name
} finally {
    $credential = $null
}
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State
