# 用法: .\stop-services.ps1 -Service DHCP
# 说明: 在 VM4 上停止关键服务模拟故障

param(
    [ValidateSet("DHCP", "DNS", "AD")]
    [string]$Service = "DHCP"
)

$service_map = @{
    "DHCP" = "DHCPServer"
    "DNS"  = "DNS"
    "AD"   = "NTDS"
}

$svc_name = $service_map[$Service]

Write-Host "[*] 当前 $Service 状态:" -ForegroundColor Cyan
Get-Service $svc_name | Format-Table Name, Status -AutoSize

Write-Host "[*] 正在停止 $Service..." -ForegroundColor Yellow
Stop-Service $svc_name -Force

Write-Host "[!] $Service 已停止，等待 Zabbix 告警触发" -ForegroundColor Red
Write-Host "[!] 恢复命令: Start-Service $svc_name" -ForegroundColor Green
