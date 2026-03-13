# quant-etf 运行步骤文档

## 环境要求

- Python 3.12+
- uv (包管理器)

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 运行 ETF 选股任务

```bash
uv run python src/main.py etf
```

### 3. 查看输出文件

运行完成后，以下文件将被生成：

| 文件 | 路径 | 说明 |
|------|------|------|
| CSV 结果 | `data/results/YYYY-MM-DD/etf.csv` | 详细分析结果 |
| TDX 导入 | `output/TDX_Strategy_Pick.txt` | 通达信自选股导入文件 |
| TDX 公式 | `output/TDX_Formula_Momentum.txt` | 通达信公式文件 |

## 其他任务

### 短线选股

```bash
uv run python src/main.py short
```

### 中期反弹选股

```bash
uv run python src/main.py mid
```

### 列出所有任务

```bash
uv run python src/main.py --list
```

## 数据源配置

项目使用三层加载策略：
1. 本地 TDX 文件（通达信安装目录）
2. 缓存文件
3. 在线数据（pytdx）

### 配置 TDX 数据路径

设置环境变量：

```bash
export TDX_DATA_PATH=/path/to/your/tdx
```

或使用默认路径：
- Linux: `~/.local/share/tdx`
- Windows: `C:\new_hxzq_hc`

## 运行示例（feature/online-data-source 分支）

```bash
# 1. 切换到 feature 分支
git checkout feature/online-data-source

# 2. 运行 ETF 选股
uv run python src/main.py etf

# 3. 查看结果
cat data/results/2026-03-11/etf.csv
cat output/TDX_Strategy_Pick.txt
```

## 输出示例

```
2026-03-11 11:18:22 | INFO     | Quant ETF System Starting...
2026-03-11 11:18:22 | INFO     | Loading data for 66 securities...
2026-03-11 11:18:22 | INFO     | Results saved to CSV: data/results/2026-03-11/etf.csv
2026-03-11 11:18:22 | INFO     | TDX Import File created: output/TDX_Strategy_Pick.txt
2026-03-11 11:18:22 | INFO     | System finished successfully.
```

## 常见问题

### Q: 如何清除缓存重新获取数据？

删除缓存目录：
```bash
rm -rf data/cache/*
```

### Q: 如何配置通达信路径？

参考 [CONFIG.md](../CONFIG.md) 获取详细配置说明。

### Q: 运行时提示数据不存在怎么办？

项目会自动尝试在线获取数据。如果失败，请检查网络连接或配置本地 TDX 路径。
