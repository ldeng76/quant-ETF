# 微信小程序开发环境启动脚本
# 用法:
#   启动: .\scripts\start-mini-dev.ps1
#   停止: .\scripts\start-mini-dev.ps1 --stop

### 日志配置 ###
$LOG_DIR = Join-Path $PSScriptRoot "..\logs"
$BACKEND_LOG = Join-Path $LOG_DIR "backend-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
$SCRIPT_LOG = Join-Path $LOG_DIR "start-mini-dev-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"

# 确保日志目录存在
if (-not (Test-Path $LOG_DIR)) {
    New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
}

# 启动脚本输出记录
try {
    Start-Transcript -Path $SCRIPT_LOG -Append -ErrorAction SilentlyContinue
} catch {
    Write-Host "[警告] 无法记录脚本日志: $_" -ForegroundColor Yellow
}

Write-Host "日志文件: $SCRIPT_LOG" -ForegroundColor Cyan

# ==================== 命令行参数解析 ====================
$STOP_MODE = $false
$KILL_PORT = 8522

if ($args -contains "--stop" -or $args -contains "-s") {
    $STOP_MODE = $true
}

# ==================== 停止服务 ====================
if ($STOP_MODE) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  停止 quant-ETF 开发环境" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan

    # 1. 清理日志事件订阅
    Get-EventSubscriber | Where-Object { $_.SourceIdentifier -like "Backend*" } | Unregister-Event -Force -ErrorAction SilentlyContinue

    # 2. 通过端口杀掉进程
    $conn = Get-NetTCPConnection -LocalPort $KILL_PORT -ErrorAction SilentlyContinue
    if ($conn) {
        $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $pids) {
            try {
                $proc = Get-Process -Id $p -ErrorAction Stop
                Write-Host "[停止] PID $p - $($proc.ProcessName)" -ForegroundColor Yellow
                Stop-Process -Id $p -Force -ErrorAction Stop
            } catch {
                Write-Host "     无法停止 PID $p，尝试管理员权限..." -ForegroundColor Red
            }
        }
    } else {
        Write-Host "端口 $KILL_PORT 未被占用" -ForegroundColor Gray
    }

    # 3. 检查是否还有进程在运行
    Start-Sleep -Seconds 1
    $remaining = Get-NetTCPConnection -LocalPort $KILL_PORT -ErrorAction SilentlyContinue
    if ($remaining) {
        Write-Host "`n仍有进程占用端口 $KILL_PORT，请手动停止:" -ForegroundColor Red
        $remaining | ForEach-Object {
            $p = $_.OwningProcess
            Write-Host "  PID: $p" -ForegroundColor Yellow
        }
        Write-Host "`n或者用管理员权限运行: Stop-Process -Id (Get-NetTCPConnection -LocalPort $KILL_PORT).OwningProcess -Force" -ForegroundColor Gray
    } else {
        Write-Host "`n[√] 端口 $KILL_PORT 已释放" -ForegroundColor Green
    }

    Write-Host "`n[√] 停止操作完成" -ForegroundColor Green

    # 停止脚本日志
    try { Stop-Transcript -ErrorAction SilentlyContinue } catch { }
    exit 0
}

$ErrorActionPreference = "Stop"
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  quant-ETF 小程序开发环境" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 检查环境变量
$env:MINI_DEV = "1"
Write-Host "[√] 开发模式已启用 (MINI_DEV=1)" -ForegroundColor Green
Write-Host "    无需微信 AppID，使用 Mock 登录" -ForegroundColor Gray

# 本地管理员账号配置（用于登录看板）
$env:INIT_ADMIN_USER = "admin"
$env:INIT_ADMIN_PASS = "admin123"
Write-Host "[√] 本地账号已配置: admin / admin123`n" -ForegroundColor Green

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
    # 启动后端（重定向输出到日志文件）
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    @"
========================================
后端启动时间: $timestamp
日志文件: $BACKEND_LOG
========================================
"@ | Out-File -FilePath $BACKEND_LOG -Encoding UTF8

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "uv"
    $psi.Arguments = "run quant-etf dashboard"
    $psi.WorkingDirectory = (Split-Path $PSScriptRoot -Parent)
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $process = [System.Diagnostics.Process]::Start($psi)
    
    # 异步读取输出并写入日志
    Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -Action {
        if ($EventArgs.Data) {
            "$(Get-Date -Format 'HH:mm:ss') $($EventArgs.Data)" | Out-File -FilePath $BACKEND_LOG -Append -Encoding UTF8
        }
    } -SourceIdentifier "BackendOutput" | Out-Null
    Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -Action {
        if ($EventArgs.Data) {
            "$(Get-Date -Format 'HH:mm:ss') [ERROR] $($EventArgs.Data)" | Out-File -FilePath $BACKEND_LOG -Append -Encoding UTF8
        }
    } -SourceIdentifier "BackendError" | Out-Null
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()

    Write-Host "     后端启动中 (PID: $($process.Id))..." -ForegroundColor Yellow
    Write-Host "     后端日志: $BACKEND_LOG" -ForegroundColor Cyan
    Start-Sleep -Seconds 3

    # 验证
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8522/health" -TimeoutSec 10
        Write-Host "     后端已启动: $($health.status)`n" -ForegroundColor Green
    } catch {
        Write-Host "     警告: 后端可能启动失败，请检查日志" -ForegroundColor Red
        Write-Host "     日志位置: $BACKEND_LOG" -ForegroundColor Gray
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
Write-Host "  - 后端日志: $BACKEND_LOG" -ForegroundColor Gray
Write-Host ""

# 停止脚本日志记录
try {
    Stop-Transcript -ErrorAction SilentlyContinue
} catch { }