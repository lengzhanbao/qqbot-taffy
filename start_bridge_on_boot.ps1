# 开机启动 QQ 桥接 (<项目根>\start_bridge_on_boot.ps1)
# - 等 NapCat OneBot WebSocket 端口 3001 就绪（QQ 登录后才起）
# - 调用 restart_bridge.py 启动桥接
# - 若 NapCat 10 分钟内未起来，停止重试（避免无限日志）

$root = $PSScriptRoot
$log = Join-Path $root 'boot_start_bridge.log'
$stop = $false
try {
    $script:logStream = [System.IO.StreamWriter]::new($log, $true, [System.Text.Encoding]::UTF8)
    $script:logStream.AutoFlush = $true
} catch { $stop = $true }

function Out([string]$msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Output $line
    if ($script:logStream) { $script:logStream.WriteLine($line) }
}

if ($stop) { exit 1 }

Out "=== 开机启动 QQ 桥接 ==="

# 1) 启动 NapCat (如果还没在)
$napcatProc = Get-Process -Name NapCatWinBootMain -ErrorAction SilentlyContinue
if (-not $napcatProc) {
    Out "[1/3] 启动 NapCat..."
    $bat = Join-Path $root 'shell\launch_napcat.bat'
    if (Test-Path $bat) {
        Start-Process -FilePath $bat -WindowStyle Hidden
    } else {
        Out "  找不到 $bat，跳过"
    }
} else {
    Out "[1/3] NapCat 已在运行 (PID $($napcatProc.Id))"
}

# 2) 等 OneBot WebSocket 端口 3001 就绪（最多 10 分钟）
Out "[2/3] 等待 OneBot WebSocket 端口 3001..."
$ready = $false
for ($i = 0; $i -lt 120; $i++) {
    $c = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
    if ($c) {
        Out "  端口 3001 已就绪（$($i * 5) 秒）"
        $ready = $true
        break
    }
    Start-Sleep -Seconds 5
}
if (-not $ready) {
    Out "  10 分钟内未检测到 3001 端口，跳过桥接启动（NapCat 可能登录失败）"
    $script:logStream.Close()
    exit 1
}

# 3) 启动桥接
Out "[3/3] 启动桥接 (restart_bridge.py)..."
$pyw = $env:PYTHONW
if (-not $pyw) {
    $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
}
if (-not $pyw) { $pyw = 'pythonw' }
$script = Join-Path $root 'restart_bridge_hidden.py'
if (-not (Test-Path $pyw)) { $pyw = 'pythonw' }
try {
    $p = Start-Process -FilePath $pyw -ArgumentList @($script) -WorkingDirectory $root -WindowStyle Hidden -PassThru
    Out "  已触发 (PID $($p.Id))"
} catch {
    Out "  启动失败: $_"
}

Out "=== 完成 ==="
$script:logStream.Close()
