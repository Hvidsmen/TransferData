param(
    [ValidateSet("web", "worker", "erp-worker", "python-worker")][string]$Mode = "web",
    [string]$CredentialFile = ".state\postgres.credential.xml",
    [string]$AllowedHosts = "vm-dwh,localhost,127.0.0.1,192.168.221.178,etl-dev-portal.daichi.ru"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:TRANSFERDATA_STATE_DIR = Join-Path $projectRoot ".state"
$env:TRANSFERDATA_ALLOWED_HOSTS = $AllowedHosts
$pythonArgs = @("-m", "transferdata.web")
if (Test-Path "$projectRoot\.venv\Scripts\transferdata.exe") {
    $python = "$projectRoot\.venv\Scripts\python.exe"
} else {
    $python = "py"
}
if ($Mode -eq "web") {
    & $python @pythonArgs runpanel --host 0.0.0.0 --port 8003
    exit $LASTEXITCODE
}
if ($Mode -eq "python-worker") {
    & $python @pythonArgs pythonworker
    exit $LASTEXITCODE
}
if ($Mode -eq "erp-worker") {
    & $python @pythonArgs etl_worker
    exit $LASTEXITCODE
}
if (-not [IO.Path]::IsPathRooted($CredentialFile)) {
    $CredentialFile = Join-Path $projectRoot $CredentialFile
}
if (-not (Test-Path -LiteralPath $CredentialFile)) {
    Write-Host "No PostgreSQL credential file. SQL Server procedure jobs can run without it."
    & $python @pythonArgs etlworker
    exit $LASTEXITCODE
}
$credential = Import-Clixml -LiteralPath $CredentialFile
if ($credential -isnot [System.Management.Automation.PSCredential]) {
    throw "Credential file must contain a PSCredential for this Windows account."
}
$previousUser = $env:TARGET_USER
$previousPassword = $env:TARGET_PASSWORD
$exitCode = 1
try {
    $env:TARGET_USER = $credential.UserName
    $env:TARGET_PASSWORD = $credential.GetNetworkCredential().Password
    & $python @pythonArgs etlworker
    $exitCode = $LASTEXITCODE
} finally {
    $env:TARGET_USER = $previousUser
    $env:TARGET_PASSWORD = $previousPassword
    $credential = $null
}
exit $exitCode
