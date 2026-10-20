# QQBot 项目备份：所有备份都放在 项目根目录 backups/，不写 C 盘。
param(
    [string]$Tag = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
# 安全护栏：确认目标确实是本项目（用文件标记，不用盘符——盘符判断不可移植）
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "bridge_qq.py"))) {
    throw "不是 QQBot 项目根目录（缺少 bridge_qq.py）: $ProjectRoot"
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
    # qqbot_core 是实际在跑的业务模块，之前完全没被备份覆盖；
    # 其中 routing/observer/topics/knowledge/memory/group_policy/evaluation 是 git 未跟踪文件，备份就是它们的唯一副本。
    "qqbot_core/__init__.py",
    "qqbot_core/agent_memory.py",
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
    "data/recent_group_context.json",
    "data/last_answer.txt",
    "logs/bridge.log",
    "logs/restart_hidden.log",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-memory.txt",
    "config/.env.example",
    "scripts/backup.ps1",
    "scripts/restore.ps1",
    "scripts/status.ps1",
    "scripts/backup_agent_memory.py",
    "scripts/run_memory_ab_eval.py",
    "scripts/check_memory_embedding.py",
    "docs/agent_memory_implementation.md",
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

$memorySnapshot = $null
$memoryDatabase = Join-Path $ProjectRoot "data\agent_memory\memory.sqlite3"
if (Test-Path -LiteralPath $memoryDatabase) {
    $pythonCommand = (Get-Command python -ErrorAction Stop).Source
    $snapshotScript = Join-Path $ProjectRoot "scripts\backup_agent_memory.py"
    $snapshotJson = & $pythonCommand -B $snapshotScript
    if ($LASTEXITCODE -ne 0) {
        throw "记忆快照失败；已有源码备份保留，未删除任何文件"
    }
    $memorySnapshot = ($snapshotJson | ConvertFrom-Json).snapshot
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    tag = $Tag
    project_root = $ProjectRoot
    backup_dir = $backupDir
    files = $copied
    agent_memory_snapshot = $memorySnapshot
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupDir "manifest.json") -Encoding UTF8

Write-Output "备份完成: $backupDir"
foreach ($rel in $copied) {
    Write-Output "  - $rel"
}
