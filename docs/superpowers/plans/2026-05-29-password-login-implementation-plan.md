# 实现计划：登录页增强 - 密码登录

## 目标

在现有登录页增加用户名/密码登录方式，与 GitHub OAuth 和企业微信 OAuth 并存，统一签发 JWT + HttpOnly Cookie。

---

## Step 1: 添加依赖

**文件**: `pyproject.toml`
**改动**: 新增 `argon2-cffi` 依赖

```toml
argon2-cffi = "^23.1.0"
```

**执行**: `uv sync`

---

## Step 2: 配置项

**文件**: `src/quant_etf/dashboard/config.py`
**改动**: 新增两个环境变量配置

```python
INIT_ADMIN_USER: Optional[str] = None  # 初始管理员用户名
INIT_ADMIN_PASS: Optional[str] = None  # 初始管理员密码
```

---

## Step 3: 数据库初始化

**文件**: `src/quant_etf/dashboard/db.py`
**改动**: 新增 `init_local_users_table()` 函数

```python
def init_local_users_table():
    """创建 local_users 表（如不存在）"""
    execute("""
        CREATE TABLE IF NOT EXISTS local_users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            display_name VARCHAR(100),
            role VARCHAR(20) DEFAULT 'user',
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now()
        )
    """)
```

**调用**: 在 `db.py` 初始化时调用，或在 `auth.py` 首次加载时调用。

---

## Step 4: auth.py 核心逻辑

**文件**: `src/quant_etf/dashboard/auth.py`
**改动**:

1. **新增 import**:
   ```python
   import argon2
   from argon2 import PasswordHasher
   ```

2. **新增暴力锁定内存结构**:
   ```python
   _login_attempts: dict[str, tuple] = {}  # username -> (count, locked_until)
   ```

3. **新增函数**:

   ```python
   _ph = PasswordHasher()

   def hash_password(password: str) -> str:
       """Argon2id 哈希密码"""
       return _ph.hash(password)

   def verify_password(password: str, hash: str) -> bool:
       """验证密码，返回 bool"""
       try:
           _ph.verify(hash, password)
           return True
       except:
           return False

   def is_locked(username: str) -> bool:
       """检查账号是否被锁定"""
       if username not in _login_attempts:
           return False
       count, locked_until = _login_attempts[username]
       if count >= 5 and time.time() < locked_until:
           return True
       return False

   def record_failure(username: str):
       """记录一次失败登录"""
       now = time.time()
       if username not in _login_attempts:
           _login_attempts[username] = (1, now + 900)
       else:
           count, _ = _login_attempts[username]
           if time.time() >= _:
               _login_attempts[username] = (1, now + 900)
           else:
               _login_attempts[username] = (count + 1, now + 900)

   def clear_attempts(username: str):
       """清除失败计数（登录成功后）"""
       _login_attempts.pop(username, None)

   def get_local_user_by_username(username: str) -> Optional[dict]:
       """根据用户名查找本地用户"""
       return query_one(
           "SELECT * FROM local_users WHERE username = %s AND is_active = true",
           [username]
       )

   def create_local_user(username: str, password: str, role: str = "user", display_name: str = "") -> int:
       """创建本地用户，返回 user_id"""
       password_hash = hash_password(password)
       result = execute(
           "INSERT INTO local_users (username, password_hash, display_name, role) "
           "VALUES (%s, %s, %s, %s) RETURNING id",
           [username, password_hash, display_name, role]
       )
       return result[0]["id"]
   ```

4. **初始化管理员**: 在模块级别读取 `INIT_ADMIN_USER/INIT_ADMIN_PASS`，若 `local_users` 中不存在则调用 `create_local_user()` 创建管理员。

---

## Step 5: auth_routes.py 路由

**文件**: `src/quant_etf/dashboard/routes/auth_routes.py`
**改动**: 新增路由

### 5a. 表单登录 (HTML Form)

```python
from fastapi import Form

@router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """用户名密码表单登录"""
    # 1. 检查是否锁定
    if is_locked(username):
        return RedirectResponse(url="/auth/login?error=locked", status_code=302)

    # 2. 查找用户
    user = get_local_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        record_failure(username)
        return RedirectResponse(url="/auth/login?error=invalid", status_code=302)

    # 3. 签发 JWT
    clear_attempts(username)
    token = create_jwt(user["id"], user["role"], provider="local")

    # 4. 写 Cookie + 重定向
    response = RedirectResponse(url="/pages/overview", status_code=302)
    response.set_cookie(
        key="token", value=token,
        httponly=True, samesite="strict",
        secure=not _is_dev(), max_age=7 * 86400
    )
    return response
```

### 5b. JSON API 登录

```python
class LocalLoginRequest(BaseModel):
    username: str
    password: str

@api_router.post("/auth/login")
async def api_login(request: Request, body: LocalLoginRequest):
    """用户名密码 JSON API 登录"""
    if is_locked(body.username):
        raise HTTPException(401, "登录尝试次数过多，请15分钟后重试")

    user = get_local_user_by_username(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        record_failure(body.username)
        raise HTTPException(401, "用户名或密码错误")

    clear_attempts(body.username)
    token = create_jwt(user["id"], user["role"], provider="local")

    response = JSONResponse({"message": "登录成功", "user": {"id": user["id"], "username": user["username"]}})
    response.set_cookie(key="token", value=token, httponly=True, samesite="strict", ...)
    return response
```

### 5c. 初始化管理员

在路由模块加载时：
```python
from ..config import INIT_ADMIN_USER, INIT_ADMIN_PASS

def _init_admin():
    if INIT_ADMIN_USER and INIT_ADMIN_PASS:
        existing = get_local_user_by_username(INIT_ADMIN_USER)
        if not existing:
            create_local_user(INIT_ADMIN_USER, INIT_ADMIN_PASS, role="admin", display_name="Admin")
            logger.info(f"Created initial admin user: {INIT_ADMIN_USER}")

_init_admin()
```

---

## Step 6: 登录页模板

**文件**: `src/quant_etf/dashboard/templates/auth/login.html`
**改动**: 在 `<div class="brand">` 下方、`<h4>` 上方插入用户名密码表单

```html
<!-- 用户名密码登录 -->
<div id="local-login-form" class="mb-4">
    <form method="post" action="/auth/login">
        <div class="mb-3">
            <input type="text" name="username" class="form-control" placeholder="用户名" required autofocus>
        </div>
        <div class="mb-3">
            <input type="password" name="password" class="form-control" placeholder="密码" required>
        </div>
        <div class="d-grid">
            <button type="submit" class="btn btn-primary">
                <i class="bi bi-box-arrow-in-right"></i> 登录
            </button>
        </div>
    </form>
</div>

{% if error == 'invalid' %}
<div class="alert alert-danger small">用户名或密码错误</div>
{% elif error == 'locked' %}
<div class="alert alert-warning small">登录尝试次数过多，请15分钟后重试</div>
{% endif %}

<div class="login-divider">或</div>

<!-- 保留原有 OAuth 按钮区块 -->
```

**隐藏逻辑**: 如果 `INIT_ADMIN_USER` 未配置，用 `{% if INIT_ADMIN_USER %}` 包裹表单区块，或在后端传给模板一个 `local_login_enabled` 变量。

---

## Step 7: 数据库 Migration

**文件**: `src/quant_etf/dashboard/db_migrate.py`
**改动**: 新增 migration 步骤

```python
if step == N:
    init_local_users_table()
```

或在 `db.py` 模块初始化时 `CREATE TABLE IF NOT EXISTS` 自动处理。

---

## 验收标准

- [ ] 用户名/密码可正常登录，收到 HttpOnly Cookie
- [ ] 三种登录方式行为一致（都能访问受保护页面）
- [ ] 错误信息统一为"用户名或密码错误"，不区分具体原因
- [ ] 暴力破解 5 次后锁定 15 分钟
- [ ] 管理员可增删改本地用户
- [ ] 未配置 `INIT_ADMIN_USER` 时表单不显示