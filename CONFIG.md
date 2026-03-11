# quant-etf 配置指南

本文档详细说明 quant-etf 项目的配置选项。

## 目录

- [数据源配置](#数据源配置)
- [环境变量](#环境变量)
- [策略配置](#策略配置)
- [平台差异](#平台差异)

## 数据源配置

### 通达信(TDX) 数据源

项目使用通达信软件的数据文件作为主要数据源，同时支持在线数据获取作为备用。

#### 数据目录结构

```
TDX_DIR/
├── vipdoc/           # K线数据目录
│   ├── ds2/          # 深圳市场数据
│   └── sh/           # 上海市场数据
└── T0002/
    └── blocknew/     # 自定义板块目录
```

#### 配置方法

**方法 1: 环境变量 (推荐)**

```bash
# Linux/macOS
export TDX_DATA_PATH="/path/to/your/tdx"

# Windows PowerShell
$env:TDX_DATA_PATH="C:\path\to\your\tdx"

# Windows CMD
set TDX_DATA_PATH=C:\path\to\your\tdx
```

**方法 2: 修改配置文件**

编辑 `src/quant_etf/conf.py`:

```python
TDX_DIR = Path(r"C:\your\custom\path")
```

### 在线数据源

项目使用 `pytdx` 库支持在线数据获取。当本地 TDX 数据文件不存在时，系统会自动尝试从在线服务器获取数据。

在线服务器列表在 `src/quant_etf/tdx_realtime.py` 中配置：

```python
DEFAULT_HQ_SERVER = [
    ("119.147.212.81", 7709),
    ("114.80.63.12", 7709),
    # ... 更多服务器
]
```

## 环境变量

| 变量名 | 说明 | 默认值 | 必需 |
|--------|------|--------|------|
| `TDX_DATA_PATH` | TDX 数据目录路径 | Windows: `C:\new_hxzq_hc`<br>Linux: `~/.local/share/tdx` | 否 |

## 策略配置

### 动量权重

动量策略使用不同时间周期的收益率加权排名。编辑 `src/quant_etf/conf.py`:

```python
MOMENTUM_WEIGHTS = {
    "r60": 0.4,  # 60日收益率
    "r20": 0.3,  # 20日收益率
    "r10": 0.2,  # 10日收益率
    "r5": 0.1    # 5日收益率
}
```

#### 权重调整建议

- **中长期趋势**: 增加 `r60` 权重，减少 `r5` 权重
- **短线快进快出**: 增加 `r5` 权重，减少 `r60` 权重
- **均衡配置**: 各权重相近

### 持仓数量

```python
TOP_N = 15  # 选出的标的数量
```

### 标的池

编辑 `src/quant_etf/conf.py` 中的标的池：

```python
ETF_POOL = [
    "510050",  # 华夏上证50ETF
    "510300",  # 华泰柏瑞沪深300ETF
    # ... 添加更多
]
```

## 平台差异

### Windows

- **默认 TDX 路径**: `C:\new_hxzq_hc`
- **路径格式**: 使用原始字符串 `r"C:\path\to\dir"`
- **通达信安装**: 通常位于 `C:\new_tdx` 或类似路径

### Linux

- **默认 TDX 路径**: `~/.local/share/tdx`
- **路径格式**: 使用 `Path` 对象
- **数据文件**: 需要从 Windows 复制或使用在线数据

### macOS

- **默认 TDX 路径**: `~/.local/share/tdx`
- 与 Linux 配置相同

## 自定义板块导出

配置通达信自定义板块：

```python
# 自定义板块名称（不含 .blk 后缀）
TDX_CUSTOM_BLOCK_NAME = "高分etf"
```

导出文件会生成 `TDX_Strategy_Pick.txt`，可直接导入通达信。

## 故障排除

### 问题: 数据目录不存在

**错误信息**: `通达信数据目录不存在`

**解决方案**:
1. 设置正确的 `TDX_DATA_PATH` 环境变量
2. 确保目录包含 `vipdoc/` 子目录

### 问题: 在线数据获取失败

**可能原因**:
- 网络连接问题
- TDX 服务器不可用

**解决方案**:
1. 检查网络连接
2. 配置本地 TDX 数据源

### 问题: 测试在 Linux 下失败

**错误信息**: `AssertionError: 通达信数据目录不存在`

**解决方案**:
1. 设置 `TDX_DATA_PATH` 环境变量
2. 或跳过集成测试: `pytest -m "not integration"`
