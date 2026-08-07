# QQBot 健康状态查看脚本，只读取 E 盘 health.json。
param(
    [string]$HealthPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProjectRoot.StartsWith("E:\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "状态脚本只允许在 E 盘项目中使用: $ProjectRoot"
}

if (-not $HealthPath) {
    $HealthPath = Join-Path $ProjectRoot "data\health.json"
}

if (-not (Test-Path -LiteralPath $HealthPath)) {
    Write-Host "[CRITICAL] data\health.json 不存在，机器人可能从未启动" -ForegroundColor Red
    exit 2
}

try {
    $health = Get-Content -Raw -LiteralPath $HealthPath | ConvertFrom-Json
    $updated = [datetime]::Parse([string]$health.updated_at)
}
catch {
    Write-Host "[CRITICAL] data\health.json 解析失败: $_" -ForegroundColor Red
    exit 2
}

$now = Get-Date
$diff = ($now - $updated).TotalSeconds
$healthPid = $null
if ($health.pid) {
    $healthPid = [int]$health.pid
}

$pidAlive = $false
if ($healthPid) {
    $pidAlive = [bool](Get-Process -Id $healthPid -ErrorAction SilentlyContinue)
}

Write-Host "===== QQBot 健康状态 =====" -ForegroundColor Cyan
Write-Host "PID:                 $($health.pid)"
Write-Host "启动时间:            $($health.start_time)"
Write-Host "WebSocket 连接:      $(if ($health.ws_connected) { '已连接' } else { '未连接' })"
Write-Host "最后收到消息:        $($health.last_message_received_at)"
Write-Host "最后发送回复:        $($health.last_reply_sent_at)"
Write-Host "最后错误:            $($health.last_error)"
Write-Host "最后错误时间:        $($health.last_error_at)"
Write-Host "最后安全拦截:        $($health.last_safety_block)"
Write-Host "搜索降级:            $(if ($health.search_degraded) { "是 ($($health.search_degraded_engine))" } else { '否' })"
Write-Host "模型熔断:            $(if ($health.model_circuit_open) { '是' } else { '否' })"
Write-Host "状态更新时间:        $($health.updated_at)"
Write-Host ""

if ($diff -gt 300) {
    Write-Host "[CRITICAL] 状态超过 5 分钟未更新，机器人可能已挂" -ForegroundColor Red
    exit 2
}

if (-not $pidAlive) {
    Write-Host "[CRITICAL] data\health.json 中的 PID 不存在，机器人未运行" -ForegroundColor Red
    exit 2
}

if (-not $health.ws_connected) {
    Write-Host "[WARNING] WebSocket 未连接" -ForegroundColor Yellow
    exit 1
}

if ($health.last_error) {
    Write-Host "[WARNING] 最近有错误，但仍在运行" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] 机器人运行正常" -ForegroundColor Green
exit 0
