# 微信小程序本地调试指南

## 一、架构概览

```
微信小程序 (wx.login)
    ↓ code
后端 API (/api/wechat/login)
    ↓ code2session → openid
JWT Token (HS256, 7天有效期)
    ↓ Bearer Token
所有受保护的 API (/api/*)
```

### 核心端点

| 端点 | 方法 | 说明 | 认证 |
|------|------|------|------|
| `/api/wechat/login` | POST | 小程序登录，获取 JWT | code |
| `/api/me` | GET | 当前用户信息 | Bearer |
| `/api/strategy/list` | GET | 策略列表 | Bearer |
| `/api/strategy/today/{name}` | GET | 今日策略结果 | Bearer |
| `/api/strategy/history-summary-data` | GET | 历史摘要 | Bearer |
| `/api/strategy/sell-signals-data` | GET | 卖出信号 | Bearer |
| `/api/watchlist/` | GET | 关注列表 | Bearer |
| `/api/watchlist/` | POST | 添加关注 | Bearer |
| `/api/watchlist/{id}` | DELETE | 删除关注 | Bearer |
| `/api/user/profile` | GET | 用户资料+有效期 | Bearer |
| `/api/user/profile` | PUT | 更新资料 | Bearer |
| `/api/admin/users` | GET | 用户列表(管理员) | Bearer+Admin |
| `/api/admin/users/{id}/extend` | PUT | 延长有效期 | Bearer+Admin |

---

## 二、后端启动

### 2.1 确保数据库运行

```bash
# PostgreSQL 必须运行，检查连接
uv run quant-etf check --port 8522
```

### 2.2 启动 Dashboard（包含所有 API）

```bash
# 默认端口 8522
uv run quant-etf dashboard

# 或自定义端口
uv run quant-etf dashboard --port 8522 --host 0.0.0.0
```

启动成功后，API 基础地址：`http://localhost:8522`

### 2.3 验证健康检查

```bash
curl http://localhost:8522/health
```

预期返回：
```json
{
  "status": "ok",
  "node_role": "primary",
  "auth_enabled": true,
  "postgresql": "ok"
}
```

---

## 三、模拟小程序登录（无需真实微信）

由于小程序登录需要微信开放平台审核通过的 AppID，本地开发有两种方案：

### 方案 A：Mock code2session（推荐）

修改 `wechat_mini.py` 中的 `code_to_session` 函数，使其在开发环境直接返回模拟数据：

```python
# src/quant_etf/dashboard/wechat_mini.py
def code_to_session(code: str) -> dict:
    """微信 code 换取 openid"""
    # 开发环境：直接返回模拟 openid
    if os.environ.get("ENV") == "development":
        return {"openid": f"dev_openid_{code}", "session_key": "dev_session_key"}

    # 生产环境：真实调用
    # ... 原有 code2session 逻辑 ...
```

然后调用登录接口：

```bash
curl -X POST http://localhost:8522/api/wechat/login \
  -H "Content-Type: application/json" \
  -d '{"code": "test123"}'
```

预期返回：
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "openid": "dev_openid_test123",
    "username": "user_dev_...",
    "role": "user"
  }
}
```

### 方案 B：使用 Postman/Apifox 手动获取 Token

1. 直接调用后端 `/api/me` 端点（带 Bearer token）
2. 先用方案 A 获取一个 token
3. 在 Postman 的 Authorization 栏选择 `Bearer Token`，粘贴 token
4. 后续请求自动携带该 token

---

## 四、API 测试示例

### 4.1 获取 Token（一次性）

```bash
TOKEN=$(curl -s -X POST http://localhost:8522/api/wechat/login \
  -H "Content-Type: application/json" \
  -d '{"code": "dev_user_1"}' \
  | jq -r '.access_token')
```

### 4.2 查看用户信息

```bash
curl -s http://localhost:8522/api/me \
  -H "Authorization: Bearer $TOKEN" | jq
```

### 4.3 查看策略列表

```bash
curl -s http://localhost:8522/api/strategy/list \
  -H "Authorization: Bearer $TOKEN" | jq
```

### 4.4 查看今日 ETF 结果

```bash
curl -s http://localhost:8522/api/strategy/today/etf \
  -H "Authorization: Bearer $TOKEN" | jq
```

### 4.5 管理用户有效期

```bash
# 延长 30 天（需要管理员权限）
curl -X PUT http://localhost:8522/api/admin/users/1/extend \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"days": 30}'
```

### 4.6 关注列表操作

```bash
# 添加关注
curl -X POST http://localhost:8522/api/watchlist/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code": "510300", "name": "沪深300ETF"}'

# 查看关注列表
curl -s http://localhost:8522/api/watchlist/ \
  -H "Authorization: Bearer $TOKEN" | jq

# 删除关注
curl -X DELETE http://localhost:8522/api/watchlist/1 \
  -H "Authorization: Bearer $TOKEN"
```

---

## 五、小程序端开发（uni-app）

### 5.1 创建 uni-app 项目

```bash
# 使用 HBuilderX 或 CLI
npx degit dcloudio/uni-preset-vue#vite-ts my-mini-program
cd my-mini-program
```

### 5.2 登录流程实现

```typescript
// src/utils/auth.ts
const API_BASE = 'http://localhost:8522'

export async function wxLogin(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (res) => {
        if (res.code) {
          // 发送 code 到后端换取 token
          uni.request({
            url: `${API_BASE}/api/wechat/login`,
            method: 'POST',
            header: { 'Content-Type': 'application/json' },
            data: { code: res.code },
            success: (response) => {
              const token = response.data.access_token
              // 保存到本地存储
              uni.setStorageSync('token', token)
              resolve(token)
            },
            fail: reject
          })
        }
      },
      fail: reject
    })
  })
}

// 带认证的请求封装
export async function request<T>(url: string): Promise<T> {
  let token = uni.getStorageSync('token')
  if (!token) {
    token = await wxLogin()
  }

  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE}${url}`,
      header: { 'Authorization': `Bearer ${token}` },
      success: (res) => resolve(res.data as T),
      fail: reject
    })
  })
}
```

### 5.3 页面示例 - 策略结果

```vue
<!-- src/pages/strategy/strategy.vue -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { request } from '../../utils/auth'

interface StrategyRecord {
  code: string
  name: string
  r60: number
  r20: number
  target_weight: number
}

const records = ref<StrategyRecord[]>([])
const loading = ref(false)

async function loadTodayResults() {
  loading.value = true
  try {
    const data = await request('/api/strategy/today/etf')
    records.value = data.records || []
  } catch (err) {
    console.error('Failed to load strategy results:', err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadTodayResults()
})
</script>

<template>
  <view class="container">
    <view v-if="loading">加载中...</view>
    <view v-else>
      <view
        v-for="item in records"
        :key="item.code"
        class="stock-card"
      >
        <view class="header">
          <text class="code">{{ item.code }}</text>
          <text class="name">{{ item.name }}</text>
        </view>
        <view class="details">
          <view class="row">
            <text>60日涨幅</text>
            <text class="value">{{ item.r60?.toFixed(2) }}%</text>
          </view>
          <view class="row">
            <text>20日涨幅</text>
            <text class="value">{{ item.r20?.toFixed(2) }}%</text>
          </view>
          <view class="row">
            <text>目标权重</text>
            <text class="value">{{ item.target_weight?.toFixed(2) }}%</text>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>
```

### 5.4 运行小程序

```bash
# HBuilderX: 运行 -> 运行到小程序模拟器 -> 微信开发者工具
# 或使用 CLI
npm run dev:mp-weixin
```

---

## 六、调试技巧

### 6.1 查看后端日志

```bash
# 后端日志（uvicorn + loguru）
tail -f logs/dashboard.log  # 如果有日志文件
# 或直接看终端输出
```

### 6.2 小程序端调试

- **微信开发者工具**：
  - 打开 `AppData` 面板查看 `storage`（token 是否正确保存）
  - 打开 `Network` 面板查看 API 请求
  - 打开 `Console` 查看 JS 错误

### 6.3 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `401 Unauthorized` | Token 无效或过期 | 重新登录获取新 token |
| `403 Forbidden` | 账户已过期 | 调用 `/api/admin/users/{id}/extend` |
| `CORS Error` | 跨域问题 | 小程序不存在此问题 |
| `Connection Refused` | 后端未启动 | `uv run quant-etf dashboard` |
| `code2session 失败` | 微信 AppID 未配置 | 开发环境用 Mock 模式 |

---

## 七、Postman 集合导入

创建一个 `quant-etf-mini-program.postman_collection.json`：

```json
{
  "info": {
    "name": "quant-ETF Mini Program API",
    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
  },
  "item": [
    {
      "name": "1. Login (Mock)",
      "request": {
        "method": "POST",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "url": {"raw": "http://localhost:8522/api/wechat/login", "host": ["localhost"], "port": "8522", "path": ["api", "wechat", "login"]},
        "body": {"mode": "raw", "raw": "{\"code\": \"dev_user_1\"}"}
      }
    },
    {
      "name": "2. Get User Profile",
      "request": {
        "method": "GET",
        "header": [{"key": "Authorization", "value": "Bearer {{token}}"}],
        "url": {"raw": "http://localhost:8522/api/user/profile", "host": ["localhost"], "port": "8522", "path": ["api", "user", "profile"]}
      }
    },
    {
      "name": "3. Strategy List",
      "request": {
        "method": "GET",
        "header": [{"key": "Authorization", "value": "Bearer {{token}}"}],
        "url": {"raw": "http://localhost:8522/api/strategy/list", "host": ["localhost"], "port": "8522", "path": ["api", "strategy", "list"]}
      }
    },
    {
      "name": "4. Today ETF Results",
      "request": {
        "method": "GET",
        "header": [{"key": "Authorization", "value": "Bearer {{token}}"}],
        "url": {"raw": "http://localhost:8522/api/strategy/today/etf", "host": ["localhost"], "port": "8522", "path": ["api", "strategy", "today", "etf"]}
      }
    },
    {
      "name": "5. Watchlist",
      "request": {
        "method": "GET",
        "header": [{"key": "Authorization", "value": "Bearer {{token}}"}],
        "url": {"raw": "http://localhost:8522/api/watchlist/", "host": ["localhost"], "port": "8522", "path": ["api", "watchlist"]}
      }
    }
  ],
  "variable": [{"key": "token", "value": ""}]
}
```

在 Postman 中：
1. 导入上述 JSON
2. 先运行 `1. Login`，复制返回的 `access_token`
3. 设置环境变量 `token` 为该值
4. 运行其他请求

---

## 八、数据库直接验证

```bash
# 查看用户列表及有效期
psql -U postgres -d quant_etf -c "SELECT id, username, openid, expires_at, last_login_at, role FROM users;"

# 手动延长有效期
psql -U postgres -d quant_etf -c "UPDATE users SET expires_at = NOW() + INTERVAL '30 days' WHERE id = 1;"

# 查看关注列表
psql -U postgres -d quant_etf -c "SELECT * FROM watchlist WHERE user_id = 1;"
```

---

## 九、开发环境快速启动脚本

创建 `scripts/dev-mini.sh`（Linux/Mac）或 `scripts/dev-mini.ps1`（Windows）：

```powershell
# scripts/dev-mini.ps1
Write-Host "Starting quant-ETF mini program dev environment..." -ForegroundColor Green

# 1. Check PostgreSQL
Write-Host "[1/3] Checking PostgreSQL..." -ForegroundColor Yellow
uv run quant-etf check --port 8522

# 2. Start dashboard in background
Write-Host "[2/3] Starting dashboard..." -ForegroundColor Yellow
Start-Process -FilePath "uv" -ArgumentList "run", "quant-etf", "dashboard" -WindowStyle Normal

# 3. Wait for startup
Write-Host "[3/3] Waiting for server to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# 4. Test health
$response = Invoke-RestMethod -Uri "http://localhost:8522/health"
Write-Host "Health check: $($response.status)" -ForegroundColor Green

Write-Host "`nDev environment ready at http://localhost:8522" -ForegroundColor Cyan
Write-Host "Login mock: POST /api/wechat/login with {`"code`": `"test123`"}" -ForegroundColor Cyan
```

运行：
```powershell
.\scripts\dev-mini.ps1
```

---

## 十、下一步

1. **完成 Mock 模式改造** - 在 `wechat_mini.py` 添加开发环境 bypass
2. **初始化 uni-app 项目** - 使用 HBuilderX 或 CLI
3. **实现登录页面** - wx.login → Bearer token
4. **实现策略结果页** - `/api/strategy/today/etf`
5. **实现关注列表页** - `/api/watchlist/`
6. **实现用户中心** - `/api/user/profile`
