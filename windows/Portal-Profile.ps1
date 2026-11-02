# Non-secret local installation profile. This file is dot-sourced by launch/update scripts.
$deploymentPath = Join-Path $projectRoot '.state\deployment.json'
$portalTaskPrefix = 'ETL Dev'
$portalIsYds = $false
$portalRequiredModes = @('web','worker','erp-worker')
$portalPrimaryTasks = @('Web','TransferData','ERP')
$portalAllowedHosts = 'vm-dwh,localhost,127.0.0.1,192.168.221.178,etl-dev-portal.daichi.ru'
if (Test-Path -LiteralPath $deploymentPath) {
    $deployment = Get-Content -LiteralPath $deploymentPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($deployment.version -ne 1 -or $deployment.profile -ne 'yds' -or @($deployment.PSObject.Properties).Count -ne 2) {
        throw 'Invalid deployment.json. Expected version 1, profile yds.'
    }
    $portalTaskPrefix = 'etl-dev-yds'
    $portalIsYds = $true
    $portalRequiredModes = @('web','erp-worker','python-worker')
    $portalPrimaryTasks = @('Web','ERP','Python')
    $portalAllowedHosts = 'localhost,127.0.0.1,192.168.70.9'
}
