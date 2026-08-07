# QQBot 项目备份：所有备份都放在 <项目根>\backups，不写 C 盘。
param(
    [string]$Tag = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProjectRoot.StartsWith("E:\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "备份脚本只允许在 E 盘项目中使用: $ProjectRoot"
}

$BackupRoot = Join-Path $ProjectRoot "backups"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$safeTag = ""
if ($Tag) {
    $safeTag = "_" + ($Tag -replace '[^A-Za-z0-9_-]', '_')
}
$backupDir = Join-Path $BackupRoot ($stamp + $safeTag)

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$relativeFiles = @(
    "bridge_qq.py",
    ".env",
    "data/bridge_sessions.json",
    "data/bridge_sessions_archive.json",
    "data/bot_memory.md",
    "data/group_profiles.json",
    "data/stats.json",
    "data/health.json",
    "data/last_answer.txt",
    "logs/bridge.log",
    "logs/restart_hidden.log",
    "requirements.txt",
    "requirements-dev.txt",
    "config/.env.example",
    "scripts/backup.ps1",
    "scripts/restore.ps1",
    "scripts/status.ps1",
    "restart_bridge_hidden.py",
    "AGENTS.md",
    "README.md",
    "shell/config/onebot11_*.json",
    "shell/config/webui.json",
    "shell/config/napcat_protocol_*.json"
)

$copied = @()
foreach ($rel in $relativeFiles) {
    $src = Join-Path $ProjectRoot $rel
    $sources = @()
    if ($rel -match '[\*\?]') {
        $sources = @(Get-ChildItem -Path $src -File -Force -ErrorAction SilentlyContinue)
    } elseif (Test-Path -LiteralPath $src) {
        $sources = @(Get-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue)
    }
    foreach ($item in $sources) {
        $destRel = $item.FullName.Substring($ProjectRoot.Length).TrimStart('\', '/')
        $dest = Join-Path $backupDir $destRel
        $destDir = Split-Path -Parent $dest
        if ($destDir -and -not (Test-Path -LiteralPath $destDir)) {
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
        }
        Copy-Item -LiteralPath $item.FullName -Destination $dest -Force
        $copied += $destRel
    }
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    tag = $Tag
    project_root = $ProjectRoot
    backup_dir = $backupDir
    files = $copied
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupDir "manifest.json") -Encoding UTF8

Write-Output "备份完成: $backupDir"
foreach ($rel in $copied) {
    Write-Output "  - $rel"
}
