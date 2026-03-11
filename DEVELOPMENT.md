# quant-etf 开发指南

本文档面向希望参与 quant-etf 项目开发的开发者。

## 目录

- [开发环境设置](#开发环境设置)
- [项目结构](#项目结构)
- [运行测试](#运行测试)
- [代码风格](#代码风格)
- [提交规范](#提交规范)

## 开发环境设置

### 前置要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) - 快速 Python 包管理器
- Git

### 环境安装

```bash
# 1. 克隆仓库
git clone http://c202601:3000/dzy/quant-etf.git
cd quant-etf

# 2. 安装依赖（使用 uv）
uv sync

# 3. 激活虚拟环境
source .venv/bin/activate  # Linux/macOS
# 或
.venv\Scripts\activate     # Windows
```

### 开发工具

```bash
# 安装开发依赖
uv pip install -e ".[dev]"

# 或手动安装
uv pip install pytest pytest-cov black ruff mypy
```

## 项目结构

```
quant-etf/
├── src/
│   └── quant_etf/
│       ├── __init__.py
│       ├── conf.py              # 配置常量
│       ├── data_source.py       # 数据源管理（三层加载策略）
│       ├── tdx.py               # TDX 文件解析
│       ├── tdx_realtime.py      # 实时行情获取
│       ├── tdx_export.py        # TDX 导出功能
│       ├── comparison.py        # 策略比较
│       ├── main.py              # CLI 入口
│       └── ...
├── tests/
│   ├── __init__.py
│   ├── test_tdx.py              # TDX 解析测试
│   ├── test_data_source.py      # 数据源测试
│   ├── test_tdx_realtime.py     # 实时行情测试
│   └── ...
├── data/                        # 数据缓存目录
├── output/                      # 输出文件目录
├── logs/                        # 日志目录
├── plan_*.md                   # 各类计划文档
├── pyproject.toml              # 项目配置
└── README.md
```

### 核心模块说明

#### `data_source.py` - 数据源管理

实现三层加载策略：
1. 本地 TDX 文件
2. 缓存数据
3. 在线数据获取（pytdx）

#### `tdx.py` - TDX 文件解析

- 解析通达信 `.day` 格式文件
- 支持上海/深圳市场
- 生成标准化的 OHLCV DataFrame

#### `tdx_realtime.py` - 实时行情

- 使用 pytdx 获取实时行情
- 自动服务器切换和重试

## 运行测试

### 全部测试

```bash
uv run pytest
```

### 特定测试文件

```bash
uv run pytest tests/test_tdx.py -v
```

### 跳过集成测试

集成测试需要真实的 TDX 数据：

```bash
uv run pytest -m "not integration"
```

### 测试覆盖率

```bash
uv run pytest --cov=quant_etf --cov-report=html
```

## 代码风格

### 格式化

项目使用 [Black](https://github.com/psf/black) 进行代码格式化：

```bash
# 格式化所有代码
uv run black src/ tests/

# 检查格式
uv run black --check src/ tests/
```

### Linting

项目使用 [Ruff](https://github.com/astral-sh/ruff)：

```bash
# 运行 linter
uv run ruff check src/ tests/

# 自动修复
uv run ruff check --fix src/ tests/
```

### 类型检查

项目使用 [mypy](https://github.com/python/mypy)：

```bash
uv run mypy src/
```

## 开发工作流

### 1. 创建功能分支

```bash
git checkout -b feature/your-feature-name
```

### 2. 进行开发

- 编写代码
- 添加/更新测试
- 确保测试通过

### 3. 代码检查

```bash
# 格式化
uv run black src/ tests/

# Lint
uv run ruff check --fix src/ tests/

# 测试
uv run pytest -v
```

### 4. 提交代码

```bash
git add -A
git commit -m "feat: 添加新功能描述"
```

### 5. 推送并创建 PR

```bash
git push origin feature/your-feature-name
```

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type 类型

- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `style`: 代码风格（不影响功能）
- `refactor`: 重构
- `test`: 测试相关
- `chore`: 构建/工具相关

### 示例

```bash
feat(data-source): 添加在线数据获取支持

- 实现 pytdx 在线数据获取
- 添加服务器自动切换
- 支持数据缓存

Closes #7
```

## 添加新功能

### 添加新的选股任务

1. 在 `src/quant_etf/` 中创建新的策略模块
2. 在 `src/main.py` 中注册新任务
3. 添加相应的测试

### 添加新的数据源

1. 在 `data_source.py` 中添加新的加载方法
2. 实现相应的解析逻辑
3. 添加单元测试

## 常见问题

### Q: 如何调试 TDX 数据解析？

A: 使用 `parse_tdx_day_file()` 函数并打印结果：

```python
from quant_etf.tdx import parse_tdx_day_file

df = parse_tdx_day_file("path/to/file.day")
print(df.head())
print(df.dtypes)
```

### Q: 如何添加新的测试？

A: 在 `tests/` 目录下创建相应的测试文件：

```python
# tests/test_new_feature.py
import pytest

def test_new_feature():
    assert True
```

## 相关资源

- [pytest 文档](https://docs.pytest.org/)
- [Black 文档](https://black.readthedocs.io/)
- [Ruff 文档](https://docs.astral.sh/ruff/)
- [pytdx 文档](https://github.com/raidenii/pytdx)
