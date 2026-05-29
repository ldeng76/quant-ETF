# 微信小程序 API 快速测试脚本
# 用法: .\scripts\test-mini-api.ps1

$BASE_URL = "http://localhost:8522"

Write-Host "`n=== quant-ETF 小程序 API 测试 ===" -ForegroundColor Cyan

# 1. 健康检查
Write-Host "`n[1] 健康检查..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "$BASE_URL/health" -Method Get
    Write-Host "  状态: $($health.status)" -ForegroundColor Green
    Write-Host "  PostgreSQL: $($health.postgresql)" -ForegroundColor Green
} catch {
    Write-Host "  错误: 后端未启动或无法连接" -ForegroundColor Red
    Write-Host "  请先运行: uv run quant-etf dashboard" -ForegroundColor Yellow
    exit 1
}

# 2. 小程序登录（Mock 模式）
Write-Host "`n[2] 小程序登录 (Mock)..." -ForegroundColor Yellow
$loginResponse = Invoke-RestMethod `
    -Uri "$BASE_URL/api/wechat/login" `
    -Method Post `
    -ContentType "application/json" `
    -Body '{"code": "dev_user_1"}'

$TOKEN = $loginResponse.token
if (-not $TOKEN) {
    Write-Host "  错误: 登录失败，未获取到 token" -ForegroundColor Red
    exit 1
}
Write-Host "  Token: $($TOKEN.Substring(0, 20))..." -ForegroundColor Green

# 3. 用户信息
Write-Host "`n[3] 用户信息..." -ForegroundColor Yellow
$headers = @{ "Authorization" = "Bearer $TOKEN" }
$profile = Invoke-RestMethod -Uri "$BASE_URL/api/user/profile" -Headers $headers
Write-Host "  用户ID: $($profile.id)" -ForegroundColor Green
Write-Host "  用户名: $($profile.username)" -ForegroundColor Green
if ($profile.expires_at) {
    Write-Host "  有效期: $($profile.expires_at) (剩余 $($profile.days_remaining) 天)" -ForegroundColor Green
} else {
    Write-Host "  有效期: 无限制" -ForegroundColor Green
}

# 4. 策略列表
Write-Host "`n[4] 可用策略..." -ForegroundColor Yellow
$strategies = Invoke-RestMethod -Uri "$BASE_URL/api/strategy/list" -Headers $headers
foreach ($s in $strategies.strategies) {
    Write-Host "  - $($s.name) ($($s.key))" -ForegroundColor Green
}

# 5. 今日 ETF 结果
Write-Host "`n[5] 今日 ETF 结果..." -ForegroundColor Yellow
try {
    $etf = Invoke-RestMethod -Uri "$BASE_URL/api/strategy/today/etf" -Headers $headers
    Write-Host "  日期: $($etf.date)" -ForegroundColor Green
    Write-Host "  数量: $($etf.count)" -ForegroundColor Green
    if ($etf.records -and $etf.records.Count -gt 0) {
        Write-Host "`n  前 5 只:" -ForegroundColor Cyan
        $etf.records[0..([Math]::Min(4, $etf.records.Count - 1))] | ForEach-Object {
            $r60 = if ($_.r60) { "$($_.r60.ToString('F2'))%" } else { "N/A" }
            $weight = if ($_.target_weight) { "$($_.target_weight.ToString('F2'))%" } else { "N/A" }
            Write-Host "    $($_.code) $($_.name) | 60日: $r60 | 权重: $weight" -ForegroundColor Green
        }
    }
} catch {
    Write-Host "  今日无数据或尚未运行策略" -ForegroundColor Yellow
}

# 6. 关注列表
Write-Host "`n[6] 关注列表..." -ForegroundColor Yellow
try {
    $watchlist = Invoke-RestMethod -Uri "$BASE_URL/api/watchlist/" -Headers $headers
    Write-Host "  数量: $($watchlist.count)" -ForegroundColor Green
    if ($watchlist.items.Count -eq 0) {
        Write-Host "  正在添加测试关注..." -ForegroundColor Cyan
        $addResponse = Invoke-RestMethod `
            -Uri "$BASE_URL/api/watchlist/" `
            -Method Post `
            -Headers $headers `
            -ContentType "application/json" `
            -Body '{"code": "510300", "name": "沪深300ETF"}'
        Write-Host "  添加结果: $($addResponse.message)" -ForegroundColor Green

        $watchlist = Invoke-RestMethod -Uri "$BASE_URL/api/watchlist/" -Headers $headers
        $watchlist.items | ForEach-Object {
            Write-Host "    - $($_.code) $($_.name) [状态: $($_.status)]" -ForegroundColor Green
        }
    } else {
        $watchlist.items | ForEach-Object {
            Write-Host "    - $($_.code) $($_.name) [状态: $($_.status)]" -ForegroundColor Green
        }
    }
} catch {
    Write-Host "  错误: $($_.Exception.Message)" -ForegroundColor Red
}

# 7. 管理员功能（如果当前用户是管理员）
Write-Host "`n[7] 管理员功能..." -ForegroundColor Yellow
if ($profile.role -eq "admin") {
    Write-Host "  当前角色: 管理员" -ForegroundColor Green
    $users = Invoke-RestMethod -Uri "$BASE_URL/api/admin/users" -Headers $headers
    Write-Host "  用户总数: $($users.count)" -ForegroundColor Green
    $users.users | ForEach-Object {
        $exp = if ($_.expires_at) { $_.expires_at.ToString("yyyy-MM-dd") } else { "永久" }
        Write-Host "    ID $($_.id) | $($_.username) | 过期: $exp" -ForegroundColor Green
    }
} else {
    Write-Host "  当前角色: 普通用户（跳过管理员功能）" -ForegroundColor Yellow
}

Write-Host "`n=== 测试完成 ===" -ForegroundColor Cyan
Write-Host "Token 已保存，可用于后续手动测试: $TOKEN" -ForegroundColor Gray
Write-Host "`n手动测试示例:" -ForegroundColor Gray
Write-Host "  curl -H 'Authorization: Bearer $TOKEN' $BASE_URL/api/strategy/today/short" -ForegroundColor Gray
