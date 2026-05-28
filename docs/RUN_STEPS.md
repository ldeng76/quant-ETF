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

## 分钟级数据采集

### 实时采集（持续运行）

启动分钟级K线数据采集服务，每60秒自动采集一次所有ETF的1分钟K线数据：

```bash
uv run quant-etf minute-collect
```

该服务会：
- 自动判断是否在交易时段
- 非交易时段自动等待
- 支持 Ctrl+C 优雅退出
- 日志输出到 `logs/minute_collector_YYYY-MM-DD.log`

### 补采历史数据

补采最近N个交易日的历史分钟数据：

```bash
# 补采最近30个交易日
uv run quant-etf minute-backfill --days 30

# 补采最近10个交易日
uv run quant-etf minute-backfill --days 10
```

指定日期范围补采：

```bash
# 指定起止日期
uv run quant-etf minute-backfill --start 2025-12-01 --end 2026-05-28

# 仅指定开始日期（直到今天）
uv run quant-etf minute-backfill --start 2025-12-01
```

指定特定ETF代码：

```bash
# 仅补采指定ETF
uv run quant-etf minute-backfill --start 2025-12-01 --codes 510050,159957,512480

# 指定日期范围+指定代码
uv run quant-etf minute-backfill --start 2025-12-01 --end 2026-01-01 --codes 510050,159991
```

### 智能增量补全（推荐）

自动检测每个代码的最新时间戳，只拉取缺失部分的数据。Dashboard 启动时也会自动为 ETF 池执行补全。

```bash
# 默认补全 ETF 池，最近 60 天
uv run quant-etf minute-fill

# 补全全部池，最近 30 天
uv run quant-etf minute-fill --pool all --days 30

# 指定代码
uv run quant-etf minute-fill --codes 510050,159949

# 补全股票池
uv run quant-etf minute-fill --pool stock
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `--pool` | 股票池: `etf` / `stock` / `all` | `etf` |
| `--days` | 最大回溯天数 | `60` |
| `--codes` | 逗号分隔的代码（覆盖 `--pool`） | — |

### 审计数据缺失

基于交易日历检测分钟 K 线数据的缺失情况（某日数据少于 100 根视为缺失），可选自动修复：

```bash
# 审计 ETF 池最近 60 天
uv run quant-etf minute-audit

# 审计并自动修复缺失
uv run quant-etf minute-audit --fix

# 审计单个代码最近 5 天
uv run quant-etf minute-audit --codes 510050 --days 5

# 审计全部池
uv run quant-etf minute-audit --pool all
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `--pool` | 股票池: `etf` / `stock` / `all` | `etf` |
| `--days` | 审计最近 N 个交易日 | `60` |
| `--codes` | 逗号分隔的代码（覆盖 `--pool`） | — |
| `--fix` | 自动修复缺失 | — |

审计报告输出示例：

```
审计范围: 2025-03-28 ~ 2025-05-28 (42 个交易日)

代码     缺失天数  状态
510050   0        完整
159352   3        2025-04-15, 2025-04-16, 2025-04-17
518880   42       全部缺失

汇总: 2/62 代码有缺失, 共 45 个代码天
```

### 验证数据完整性

使用Python脚本验证数据库中每个ETF的数据量和最新时间：

```bash
uv run python -c "
import os, psycopg2
from quant_etf.dashboard.config import POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB

conn = psycopg2.connect(host=POSTGRES_HOST, port=POSTGRES_PORT, user=POSTGRES_USER, password=POSTGRES_PASSWORD, database=POSTGRES_DB)
cur = conn.cursor()

cur.execute('''
    SELECT code, COUNT(*) as cnt, MAX(time) as latest, MIN(time) as earliest
    FROM minute_bars
    GROUP BY code
    ORDER BY code
''')
rows = cur.fetchall()

print(f'Total codes: {len(rows)}')
print(f'Total rows: {sum(r[1] for r in rows)}')
for code, cnt, latest, earliest in rows:
    print(f'{code}: rows={cnt}, latest={latest}')

cur.close()
conn.close()
"
```

### 清理过期数据

清理 `minute_bars` 表中的过期数据，默认只保留最近6个月的数据：

```bash
# 清理6个月前的数据（实际删除）
uv run quant-etf clean-minute-data

# 保留最近3个月的数据
uv run quant-etf clean-minute-data --months 3

# 预览将删除多少数据（不实际删除）
uv run quant-etf clean-minute-data --dry-run

# 保留12个月+预览
uv run quant-etf clean-minute-data --months 12 --dry-run
```

输出示例：
```
=== Minute Data Cleanup ===
  Mode: DRY RUN
  Cutoff time: 2025-11-29 12:06:36
  Total rows before: 1530105
  Deleted: 0
  Total rows after: 1530105
  Codes affected: 0
```
