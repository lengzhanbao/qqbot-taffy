# QQBot 项目恢复：只从 项目根目录 backups/ 读取，恢复前自动备份当前文件。
param(
    [string]$BackupPath = "",
    [switch]$CodeOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProjectRoot.StartsWith("E:\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "恢复脚本只允许在 E 盘项目中使用: $ProjectRoot"
}

$BackupRoot = Join-Path $ProjectRoot "backups"
if (-not (Test-Path -LiteralPath $BackupRoot)) {
    throw "备份目录不存在: $BackupRoot"
}

$bridgePids = Get-CimInstance Win32_Process |
    Where-Object {
        ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and
        $_.CommandLine -match "bridge_qq\.py"
    }
if ($bridgePids) {
    throw "检测到桥接进程正在运行，请先停止桥接再恢复。"
}

if ($BackupPath) {
    $resolvedBackup = (Resolve-Path -LiteralPath $BackupPath -ErrorAction Stop).Path
}
else {
    $backups = Get-ChildItem -LiteralPath $BackupRoot -Directory |
        Sort-Object Name -Descending
    if (-not $backups) {
        throw "没有可恢复的备份"
    }
    Write-Output "可用备份："
    for ($i = 0; $i -lt $backups.Count; $i++) {
        Write-Output ("{0}. {1}" -f ($i + 1), $backups[$i].Name)
    }
    $choice = Read-Host "输入序号"
    $idx = [int]$choice - 1
    if ($idx -lt 0 -or $idx -ge $backups.Count) {
        throw "无效序号"
    }
    $resolvedBackup = $backups[$idx].FullName
}

$resolvedBackupFull = (Resolve-Path -LiteralPath $resolvedBackup -ErrorAction Stop).Path
$resolvedRootFull = (Resolve-Path -LiteralPath $BackupRoot -ErrorAction Stop).Path
if (-not $resolvedBackupFull.StartsWith(
        $resolvedRootFull + "\",
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "备份路径必须在 $BackupRoot 内"
}
if ($resolvedBackupFull -eq $resolvedRootFull) {
    throw "不能选择备份根目录"
}

$answer = Read-Host "确认恢复 $resolvedBackup 到 $ProjectRoot？输入 YES 继续"
if ($answer -ne "YES") {
    Write-Output "已取消"
    exit 0
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$beforeDir = Join-Path $BackupRoot ("before_restore_" + $stamp)
New-Item -ItemType Directory -Force -Path $beforeDir | Out-Null

$relativeFiles = @(
    "bridge_qq.py",
    ".env",
    "qqbot_core/__init__.py",
    "qqbot_core/config.py",
    "qqbot_core/evaluation.py",
    "qqbot_core/group_policy.py",
    "qqbot_core/knowledge.py",
    "qqbot_core/learning.py",
    "qqbot_core/memory.py",
    "qqbot_core/observer.py",
    "qqbot_core/prompts.py",
    "qqbot_core/routing.py",
    "qqbot_core/safety.py",
    "qqbot_core/stats.py",
    "qqbot_core/style.py",
    "qqbot_core/text.py",
    "qqbot_core/topics.py",
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
    "restart_napcat.ps1",
    "launch_napcat_hidden.ps1",
    "start_bridge_on_boot.ps1",
    "register_startup_task.ps1",
    "AGENTS.md",
    "README.md",
    "shell/config/onebot11_*.json",
    "shell/config/webui.json",
    "shell/config/napcat_protocol_*.json"
)

$manifestPath = Join-Path $resolvedBackupFull "manifest.json"
if (Test-Path -LiteralPath $manifestPath) {
    try {
        $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
        if ($manifest.files) {
            $relativeFiles = @($manifest.files)
        }
    }
    catch {
        Write-Output "manifest.json 读取失败，使用默认文件清单。"
    }
}

if ($CodeOnly) {
    $relativeFiles = @($relativeFiles | Where-Object {
        $_ -match '\.py$|^requirements[^/\\]*\.txt$|^scripts[/\\][^/\\]+\.ps1$|^config[/\\]\.env\.example$'
    })
    Write-Output "仅恢复源码；跳过配置密钥、会话、授权和记忆数据。"
}

$restored = @()
foreach ($rel in $relativeFiles) {
    $targetPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $rel))
    $sourcePath = [System.IO.Path]::GetFullPath((Join-Path $resolvedBackupFull $rel))
    if (-not $targetPath.StartsWith($ProjectRoot + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $sourcePath.StartsWith($resolvedBackupFull + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "备份清单包含越界路径，恢复已中止"
    }
    $src = Join-Path $resolvedBackupFull $rel
    $sources = @()
    if ($rel -match '[\*\?]') {
        $sources = @(Get-ChildItem -Path $src -File -Force -ErrorAction SilentlyContinue)
    } else {
        if (Test-Path -LiteralPath $src) {
            $sources = @(Get-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue)
        } else {
            $fallbackSrc = Join-Path $resolvedBackupFull (Split-Path $rel -Leaf)
            if (Test-Path -LiteralPath $fallbackSrc) {
                $sources = @(Get-Item -LiteralPath $fallbackSrc -Force -ErrorAction SilentlyContinue)
            }
        }
    }
    foreach ($item in $sources) {
        $restoreRel = $rel
        if ($rel -match '[\*\?]') {
            $restoreRel = $item.FullName.Substring($resolvedBackupFull.Length).TrimStart('\', '/')
        }
        $cur = Join-Path $ProjectRoot $restoreRel
        if (Test-Path -LiteralPath $cur) {
            $beforeRel = $restoreRel -replace '[\\/]', '__'
            $beforeFile = Join-Path $beforeDir $beforeRel
            Copy-Item -LiteralPath $cur -Destination $beforeFile -Force
        }
        $targetDir = Split-Path -Parent $cur
        if ($targetDir -and -not (Test-Path -LiteralPath $targetDir)) {
            New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
        }
        Copy-Item -LiteralPath $item.FullName -Destination $cur -Force
        $restored += $restoreRel
    }
}

Write-Output "恢复前已自动备份到: $beforeDir"
foreach ($rel in $restored) {
    Write-Output "  - $rel"
}
