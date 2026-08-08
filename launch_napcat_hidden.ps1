# 隐藏启动 NapCat，避免开机/重启时弹 cmd 窗口
Start-Process -FilePath (Join-Path $PSScriptRoot 'shell\launch_napcat.bat') -WindowStyle Hidden
