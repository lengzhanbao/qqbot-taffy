# NapCat 重启脚本（<项目根>）
$log = '<项目根>\restart_out.log'
Start-Transcript -Path $log -Force | Out-Null
Write-Output "=== restart_napcat.ps1 开始 $(Get-Date -Format 'HH:mm:ss') ==="
Write-Output "[1/2] 停止所有 QQ / NapCat 进程..."
Stop-Process -Name QQ -Force -ErrorAction SilentlyContinue
Stop-Process -Name NapCatWinBootMain -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5
$left = Get-Process -Name QQ,NapCatWinBootMain -ErrorAction SilentlyContinue
Write-Output "  剩余进程: $($left.Count)"
Write-Output "[2/2] 启动 NapCat..."
Start-Process -FilePath '<项目根>\shell\launch_napcat.bat' -WindowStyle Hidden
Write-Output "  已启动"
Stop-Transcript | Out-Null
