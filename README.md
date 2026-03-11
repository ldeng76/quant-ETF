# quant-etf

基于动量策略的 ETF 选股工具，使用通达信(TDX)数据源。

## 功能特点

- **动量选股策略**: 基于 60/20/10/5 日收益率的加权动量排名
- **通达信数据集成**: 支持本地 TDX 数据文件和在线数据获取
- **自动导出**: 生成 TDX 导入文件和自定义公式
- **跨平台**: 支持 Windows 和 Linux

## 安装

### 前置要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (推荐) 或 pip

### 安装步骤

```bash
# 克隆仓库
git clone http://c202601:3000/dzy/quant-etf.git
cd quant-etf

# 使用 uv 安装 (推荐)
uv sync

# 或使用 pip
pip install -e .
```

## 配置

### 通达信数据源配置

项目支持两种数据源方式：

#### 1. 本地 TDX 数据文件

配置通达信数据目录路径（用于读取本地 `.day` 数据文件）：

**Windows**:
```python
# 编辑 src/quant_etf/conf.py
TDX_DIR = Path(r"C:\new_hxzq_hc")
```

**Linux**:
```bash
# 设置环境变量
export TDX_DATA_PATH="$HOME/.local/share/tdx"
```

或直接修改配置文件：
```python
TDX_DIR = Path.home() / ".local" / "share" / "tdx"
```

#### 2. 在线数据获取

项目使用 `pytdx` 库支持在线数据获取，当本地数据不可用时自动回退到在线数据源。

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TDX_DATA_PATH` | 通达信数据目录 | Windows: `C:\new_hxzq_hc`<br>Linux: `~/.local/share/tdx` |

## 使用

### 运行选股任务

```bash
# ETF 选股
uv run python src/main.py etf

# 短线股票选股
uv run python src/main.py short

# 中期反弹股票选股
uv run python src/main.py mid

# 列出所有可用任务
uv run python src/main.py --list
```

### 输出文件

运行后会在 `output/` 目录生成：

- `TDX_Strategy_Pick.txt` - TDX 导入文件
- `TDX_Formula_Momentum.txt` - TDX 自定义公式文件

将 `TDX_Strategy_Pick.txt` 内容复制到通达信的自定义板块中即可使用。

## 测试

```bash
# 运行所有测试
uv run pytest

# 运行特定测试文件
uv run pytest tests/test_tdx.py -v

# 跳过集成测试（需要真实 TDX 数据）
uv run pytest -m "not integration"
```

## 项目结构

```
quant-etf/
├── src/
│   └── quant_etf/
│       ├── conf.py          # 配置文件
│       ├── data_source.py   # 数据源管理
│       ├── tdx.py           # 通达信数据处理
│       ├── tdx_realtime.py  # 实时行情获取
│       └── ...
├── tests/                   # 测试文件
├── output/                  # 输出目录
├── data/                    # 数据缓存目录
├── plan_*.md               # 计划文档
└── README.md
```

## 配置说明

### 动量权重调整

编辑 `src/quant_etf/conf.py`:

```python
MOMENTUM_WEIGHTS = {
    "r60": 0.1,  # 60日收益率权重
    "r20": 0.2,  # 20日收益率权重
    "r10": 0.3,  # 10日收益率权重
    "r5": 0.4    # 5日收益率权重
}
```

### 持仓数量

```python
TOP_N = 15  # 选出前 N 只标的
```

## 常见问题

### Q: Linux 下测试失败？

A: 部分测试需要真实的 TDX 数据。可以使用 `TDX_DATA_PATH` 环境变量指定数据目录，或跳过集成测试：

```bash
uv run pytest -m "not integration"
```

### Q: 在线数据获取失败？

A: 检查网络连接和 pytdx 库是否正确安装：

```bash
uv run pip show pytdx
```

## 许可证

MIT License
