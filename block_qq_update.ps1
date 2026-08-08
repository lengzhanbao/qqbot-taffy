# 屏蔽 QQ 自动更新下载域名（防止 QQ 更新破坏 NapCat 兼容性）
$log = Join-Path $PSScriptRoot 'hosts_out.log'
Start-Transcript -Path $log -Force | Out-Null
Write-Output "=== 添加 hosts 屏蔽 $(Get-Date -Format 'HH:mm:ss') ==="
$hosts = 'C:\Windows\System32\drivers\etc\hosts'

# 备份
Copy-Item $hosts ($hosts + '.bak') -Force
Write-Output "已备份 hosts -> hosts.bak"

# 屏蔽条目（只影响 QQ 更新下载，不影响聊天/登录/图片收发）
$lines = @(
    "# === QQ 自动更新屏蔽 (NapCat 兼容保护, 2026-08-04) ===",
    "127.0.0.1 dldir1.qq.com",
    "127.0.0.1 dldir1v6.qq.com",
    "127.0.0.1 dldir1v4.qq.com",
    "127.0.0.1 dldir2.qq.com",
    "127.0.0.1 qqdl.gtimg.cn",
    "# === 结束 ==="
)

$content = Get-Content $hosts -Raw -Encoding UTF8
$blocked = ($content -match 'QQ 自动更新屏蔽')
if ($blocked) {
    Write-Output "屏蔽已存在，跳过"
} else {
    Add-Content $hosts -Value $lines -Encoding UTF8
    Write-Output "已添加 $($lines.Count) 行"
}

# 验证
$verify = Get-Content $hosts
$ok = $verify | Select-String "dldir1.qq.com|qqdl.gtimg.cn"
Write-Output "验证: $($ok.Count) 条屏蔽生效"
Stop-Transcript | Out-Null
