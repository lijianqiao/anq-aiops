# 文件: inject-faults.ps1
# 说明: 循环停止/恢复服务，模拟间歇性故障
# 用法: .\inject-faults.ps1 -Service DHCP -Interval 300
# 需要: 以管理员身份运行 PowerShell

param(
    [ValidateSet("DHCP", "DNS", "AD")]
    [string]$Service = "DHCP",
    [int]$Interval = 300  # 秒
)

$service_map = @{
    "DHCP" = "DHCPServer"
    "DNS"  = "DNS"
    "AD"   = "NTDS"
}

$svc_name = $service_map[$Service]

Write-Host "[*] 故障注入模式: 每 ${Interval} 秒停止/恢复 $Service ($svc_name)" -ForegroundColor Cyan
Write-Host "[*] 按 Ctrl+C 停止" -ForegroundColor Gray

while ($true) {
    Write-Host "[$(Get-Date)] 停止 $Service..." -ForegroundColor Yellow
    try {
        Stop-Service $svc_name -Force -ErrorAction Stop
        Write-Host "[$(Get-Date)] $Service 已停止" -ForegroundColor Red
    } catch {
        Write-Host "[$(Get-Date)] 停止失败: $_" -ForegroundColor Red
    }

    Start-Sleep -Seconds 60

    Write-Host "[$(Get-Date)] 恢复 $Service..." -ForegroundColor Green
    try {
        Start-Service $svc_name -ErrorAction Stop
        Write-Host "[$(Get-Date)] $Service 已恢复" -ForegroundColor Green
    } catch {
        Write-Host "[$(Get-Date)] 恢复失败: $_" -ForegroundColor Red
    }

    Write-Host "[$(Get-Date)] 等待 ${Interval} 秒后再次触发..." -ForegroundColor Cyan
    Start-Sleep -Seconds $Interval
}
