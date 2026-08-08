param(
    [string]$Tag = "demo"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DemoRoot = Join-Path $Root "demo"
$Out = Join-Path $DemoRoot "build_${Stamp}_$Tag"

New-Item -ItemType Directory -Force -Path $Out | Out-Null

$Items = @(
    "bridge_qq.py",
    "qqbot_core",
    "tests",
    "CHANGELOG.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "pytest.ini",
    ".gitattributes",
    "requirements.txt",
    "requirements-dev.txt",
    "README.md",
    "AGENTS.md"
)

foreach ($Item in $Items) {
    $Src = Join-Path $Root $Item
    if (Test-Path -LiteralPath $Src) {
        Copy-Item -LiteralPath $Src -Destination $Out -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $Out "scripts") | Out-Null
foreach ($ScriptName in @("backup.ps1", "restore.ps1", "status.ps1")) {
    Copy-Item -LiteralPath (Join-Path $Root "scripts\$ScriptName") -Destination (Join-Path $Out "scripts") -Force
}

New-Item -ItemType Directory -Force -Path (Join-Path $Out "config") | Out-Null
Copy-Item -LiteralPath (Join-Path $Root "config\.env.example") -Destination (Join-Path $Out "config") -Force

New-Item -ItemType Directory -Force -Path (Join-Path $Out "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Out "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Out "docs\public") | Out-Null
Copy-Item -Path (Join-Path $Root "docs\public\*.md") -Destination (Join-Path $Out "docs\public") -Force
Set-Content -Path (Join-Path $Out "data\.gitkeep") -Value "" -Encoding UTF8
Set-Content -Path (Join-Path $Out "logs\.gitkeep") -Value "" -Encoding UTF8

Write-Output "Sanitized demo created: $Out"
Write-Output "Excluded: .env, data, logs, docs, backups, shell."
