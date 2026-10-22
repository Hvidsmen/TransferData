# Windows PowerShell 5.1. Downloads the official SDK only when needed.
[CmdletBinding()]
param([switch]$UseExisting)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Join-Path $projectRoot ".tools\onec"
$state = Join-Path $projectRoot ".state"
$project = Join-Path $projectRoot "tools\OneCMetadata\OneCMetadata.csproj"
$sdkVersion = "10.0.402"
$savedCliHome = $env:DOTNET_CLI_HOME
$savedPackages = $env:NUGET_PACKAGES
$savedTelemetry = $env:DOTNET_CLI_TELEMETRY_OPTOUT
$savedCertificate = $env:DOTNET_GENERATE_ASPNET_CERTIFICATE
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw "A 64-bit Windows server is required." }
    New-Item -ItemType Directory -Force $installRoot, $state | Out-Null
    # Library diagnostics and build artefacts stay private to the service account.
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($sid in @($identity, "S-1-5-18", "S-1-5-32-544")) {
        $principal = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($principal,"FullControl","ContainerInherit,ObjectInherit","None","Allow")
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $installRoot -AclObject $acl
    $env:DOTNET_CLI_HOME = Join-Path $installRoot "cli"
    $env:NUGET_PACKAGES = Join-Path $installRoot "packages"
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
    $env:DOTNET_GENERATE_ASPNET_CERTIFICATE = "false"
    $dotnet = Join-Path $installRoot "dotnet\dotnet.exe"
    if (-not (Test-Path -LiteralPath $dotnet)) {
        $globalDotnet = Get-Command dotnet -ErrorAction SilentlyContinue
        if ($globalDotnet) {
            $sdkList = @(& $globalDotnet.Source --list-sdks)
            if ($LASTEXITCODE -eq 0 -and @($sdkList | Where-Object { $_ -match '^10\.' }).Count) { $dotnet = $globalDotnet.Source }
        }
    }
    if (-not (Test-Path -LiteralPath $dotnet)) {
        if ($UseExisting) { throw "Install the 1C metadata helper once with windows\Install-1CMetadata.ps1." }
        Write-Host "Downloading official .NET SDK $sdkVersion (one-time setup)."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $metadata = Invoke-RestMethod "https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json"
        $sdk = @($metadata.releases | ForEach-Object { $_.sdks } | Where-Object { $_.version -eq $sdkVersion })[0]
        $file = @($sdk.files | Where-Object { $_.rid -eq "win-x64" -and $_.url.EndsWith(".zip") })[0]
        if (-not $file -or $file.url -notlike "https://builds.dotnet.microsoft.com/dotnet/*") { throw "Official SDK metadata is unavailable." }
        $archive = Join-Path $installRoot "sdk.zip"
        Invoke-WebRequest -UseBasicParsing -Uri $file.url -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA512).Hash -ne $file.hash) { throw "SDK SHA512 verification failed." }
        Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $installRoot "dotnet") -Force
        Remove-Item -LiteralPath $archive
    }
    & $dotnet restore $project --locked-mode
    if ($LASTEXITCODE -ne 0) { throw "NuGet restore failed. Check corporate proxy access to api.nuget.org." }
    $output = Join-Path $installRoot ("bin-" + [guid]::NewGuid().ToString("N"))
    & $dotnet publish $project -c Release --no-restore --output $output
    if ($LASTEXITCODE -ne 0) { throw "Metadata helper build failed." }
    $assembly = Join-Path $output "OneCMetadata.dll"
    $version = (& $dotnet $assembly --version) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $version.protocol -ne 1) { throw "Metadata helper self-check failed." }
    $manifest = @{protocol=1; dotnet=$dotnet; assembly=$assembly} | ConvertTo-Json
    $temporary = Join-Path $state ("onec-helper-" + [guid]::NewGuid().ToString("N") + ".json")
    [IO.File]::WriteAllText($temporary, $manifest, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination (Join-Path $state "onec-helper.json") -Force
    Write-Host "1C metadata reader is ready. Use ERP_to_DWH -> 1C metadata in the portal."
} finally {
    $env:DOTNET_CLI_HOME = $savedCliHome
    $env:NUGET_PACKAGES = $savedPackages
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = $savedTelemetry
    $env:DOTNET_GENERATE_ASPNET_CERTIFICATE = $savedCertificate
}
