# 登录页增强 - 密码登录

**日期**: 2026-05-29
**状态**: 已批准

## 目标

在现有登录页上增加用户名/密码登录方式，与 GitHub OAuth 和企业微信 OAuth 并存，统一签发 JWT。

## 登录方式

```
┌─────────────────────────────────────────────────────────────────┐
│                         量化ETF 登录页                           │
│                    /auth/login (auth/login.html)                │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
     ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
     │ 用户名/密码   │  │  GitHub OAuth │  │ 企业微信 OAuth│
     │  (local_users)│  │              │  │              │
     └──────────────┘  └──────────────┘  └──────────────┘
              │               │               │
              ▼               │               │
     Argon2 验证密码          │               │
              │               ▼               ▼
              │        重定向到 GitHub  重定向到企业微信
              │        授权页面         授权页面
              │               │               │
              │               ▼               ▼
              │         回调验证 code    回调验证 code
              │               │               │
              └───────────────┼───────────────┘
                              ▼
                    统一签发 JWT + HttpOnly Cookie
                              │
                              ▼
              ┌──────────────────────────────┐
              │     /pages/* 受保护页面        │
              │  (base.html 检查 /auth/status) │
              └──────────────────────────────┘
```

## 架构

### 1. 数据库 Schema

新建 `local_users` 表：

```sql
CREATE TABLE local_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    role VARCHAR(20) DEFAULT 'user',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
```

### 2. JWT 统一

密码登录成功后签发 JWT，payload 与 OAuth 登录一致：

```python
payload = {
    "sub": user_id,
    "role": role,
    "provider": "local",
    "exp": datetime.utcnow() + timedelta(days=7)
}
```

### 3. Cookie 策略

- `HttpOnly=True` — 防 XSS 盗 token
- `Secure=True` (生产) — 仅 HTTPS
- `SameSite=Strict` — 防 CSRF

### 4. 安全措施

- **密码算法**: Argon2id (`argon2-cffi`)
- **防暴力**: 5 次失败后锁定 15 分钟（进程内内存计数）
- **防枚举**: 登录失败返回统一错误，不区分"用户名错"还是"密码错"
- **JWT 有效期**: 7 天（与 OAuth 一致）

### 5. 账号管理

- 初始管理员通过环境变量 `INIT_ADMIN_USER` / `INIT_ADMIN_PASS` 自动创建
- 管理员通过 `/auth/admin/users` API 管理本地账号（增删改）
- 普通用户不能自己注册

### 6. 登录页 UI

```
┌──────────────────────────────────┐
│  quant-ETF                       │
│  ─────────────────────────────── │
│  [ 用户名 .................. ]    │  ← 新增
│  [ 密码 .................... ]    │
│  [     登录                  ]    │
│  ─────────────────────────────── │
│            或                    │
│  [  使用 GitHub 登录      ]      │
│  [  使用企业微信登录      ]      │
│  ─────────────────────────────── │
│  登录即表示您同意服务条款       │
└──────────────────────────────────┘
```

### 7. 错误处理

| 场景 | 返回 |
|------|------|
| 用户不存在 | "用户名或密码错误" |
| 密码错误 | "用户名或密码错误" |
| 账号已锁定 | "登录尝试次数过多，请15分钟后重试" |
| 账号已禁用 | "用户名或密码错误" |
| 未配置本地登录 | 隐藏表单，仅显示 OAuth |

## 文件改动

| 文件 | 改动 |
|------|------|
| `dashboard/routes/auth_routes.py` | 新增 `/login` POST 路由、`/api/auth/login` API |
| `dashboard/auth.py` | 新增 `verify_password()`、`create_local_user()`、`get_local_user()` |
| `dashboard/db.py` | 新增 `init_local_users_table()` |
| `dashboard/templates/auth/login.html` | 新增用户名密码表单 |
| `dashboard/config.py` | 新增 `INIT_ADMIN_USER`、`INIT_ADMIN_PASS` 配置 |
| Migration | 新建 `add_local_users.py` |

## 验收标准

- [ ] 用户名/密码可正常登录，收到 HttpOnly Cookie
- [ ] 三种登录方式行为一致（都能访问受保护页面）
- [ ] 错误信息不区分用户名和密码
- [ ] 暴力破解 5 次后锁定 15 分钟
- [ ] 管理员可增删改本地用户
- [ ] 未配置时表单不显示