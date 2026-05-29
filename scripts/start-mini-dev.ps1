# 微信小程序开发环境启动脚本
# 用法: .\scripts\start-mini-dev.ps1

$ErrorActionPreference = "Stop"

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  quant-ETF 小程序开发环境" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 检查环境变量
$env:MINI_DEV = "1"
Write-Host "[√] 开发模式已启用 (MINI_DEV=1)" -ForegroundColor Green
Write-Host "    无需微信 AppID，使用 Mock 登录`n" -ForegroundColor Gray

# 1. 检查 PostgreSQL
Write-Host "[1/4] 检查 PostgreSQL 连接..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8522/health" -UseBasicParsing -TimeoutSec 3
    $health = $response.Content | ConvertFrom-Json
    if ($health.postgresql -eq "ok") {
        Write-Host "     PostgreSQL: 正常`n" -ForegroundColor Green
    } else {
        throw "PostgreSQL 连接异常"
    }
} catch {
    Write-Host "     后端未运行，正在启动..." -ForegroundColor Yellow

    # 启动后端
    $process = Start-Process -FilePath "uv" -ArgumentList "run", "quant-etf", "dashboard" -PassThru -WindowStyle Normal
    Write-Host "     后端启动中 (PID: $($process.Id))..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5

    # 验证
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8522/health"
        Write-Host "     后端已启动: $($health.status)`n" -ForegroundColor Green
    } catch {
        Write-Host "     警告: 后端可能启动失败，请检查日志" -ForegroundColor Red
    }
}

# 2. 运行测试
Write-Host "[2/4] 运行 API 测试..." -ForegroundColor Yellow
$scriptPath = Join-Path $PSScriptRoot "test-mini-api.ps1"
if (Test-Path $scriptPath) {
    & $scriptPath
} else {
    Write-Host "     测试脚本不存在: $scriptPath" -ForegroundColor Yellow
}

Write-Host "`n[3/4] 开发环境已就绪`n" -ForegroundColor Green

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  快速开始" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

Write-Host "后端地址: http://localhost:8522" -ForegroundColor White
Write-Host "API 文档: http://localhost:8522/docs`n" -ForegroundColor White

Write-Host "测试登录 (Mock):" -ForegroundColor Yellow
Write-Host '  curl -X POST http://localhost:8522/api/wechat/login \' -ForegroundColor Gray
Write-Host '    -H ''Content-Type: application/json'' \' -ForegroundColor Gray
Write-Host '    -d ''{"code": "test123"}''' -ForegroundColor Gray

Write-Host "获取 Token 后测试:" -ForegroundColor Yellow
Write-Host '  curl -H ''Authorization: Bearer <token>'' \' -ForegroundColor Gray
Write-Host '    http://localhost:8522/api/strategy/today/etf' -ForegroundColor Gray

Write-Host "提示:" -ForegroundColor Yellow
Write-Host "  - 使用 Postman/Apifox 导入 docs/wechat-mini-debug-guide.md 中的集合" -ForegroundColor Gray
Write-Host "  - 重新运行此脚本可刷新测试环境" -ForegroundColor Gray
Write-Host "  - 查看日志: 在后端终端中查看" -ForegroundColor Gray
Write-Host ""
