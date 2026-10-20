param(
    [string]$Config = "config.json",
    [string]$CredentialFile = ".state\postgres.credential.xml",
    [ValidateSet("check", "prepare", "run", "serve")][string]$Command = "run"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not [IO.Path]::IsPathRooted($CredentialFile)) {
    $CredentialFile = Join-Path $projectRoot $CredentialFile
}
$credential = Import-Clixml -LiteralPath $CredentialFile
if ($credential -isnot [System.Management.Automation.PSCredential]) {
    throw "Credential file must contain a PSCredential exported by this Windows user."
}
$previousUser = $env:TARGET_USER
$previousPassword = $env:TARGET_PASSWORD
$exitCode = 1
try {
    $env:TARGET_USER = $credential.UserName
    $env:TARGET_PASSWORD = $credential.GetNetworkCredential().Password
    & "$projectRoot\.venv\Scripts\transferdata.exe" $Command --config $Config
    $exitCode = $LASTEXITCODE
} finally {
    $env:TARGET_USER = $previousUser
    $env:TARGET_PASSWORD = $previousPassword
    $credential = $null
}
exit $exitCode
