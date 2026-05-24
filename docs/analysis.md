# 项目现状分析报告

## 项目概况

- **名称**: quant-etf — 基于动量策略的 ETF/股票选股工具
- **语言/运行时**: Python 3.12+, uv 包管理
- **数据源**: 通达信(TDX)本地数据 + pytdx 在线回退
- **数据库**: DuckDB（分钟K线、Dashboard 数据持久化）
- **Web 服务**: FastAPI + uvicorn + Jinja2（Dashboard 监控系统）
- **代码行数**: ~4500 行（核心包）+ ~800 行测试

## 当前功能模块

| 模块 | 状态 | 说明 |
|------|------|------|
| ETF 动量选股 | 稳定 | 60/20/10/5日加权动量排名，输出TDX导入文件 |
| 短线股票策略 | 稳定 | 短期动量 + 量价比选股 |
| 中期反弹策略 | 稳定 | 回撤反弹策略，含 Drawdown/Bounce/Stabilization 指标 |
| Dashboard 监控 | 新增 | FastAPI Web 应用，含告警引擎、组合同步、调度器、SSE推送 |
| 分钟级K线采集 | 新增 | DuckDB存储分钟级行情，盘中持续采集 |
| 信息收集 | 独立 | `collect_info/` 下独立数据采集工具 |
| 策略(新) | 开发中 | `strategies/` 目录下 `momentum_breakthrough`, `volume_price` |

## 代码架构

```
src/quant_etf/          # 核心包
├── dashboard/          # Dashboard Web应用
│   ├── app.py          # FastAPI 入口 + 路由挂载
│   ├── db.py           # DuckDB 初始化
│   ├── models.py       # 数据模型
│   ├── config.py       # Dashboard 配置
│   ├── routes/         # 路由模块 (5个)
│   │   ├── pages.py, alerts.py, market.py, portfolio.py, strategy.py
│   ├── services/       # 服务模块 (5个)
│   │   ├── alert_engine.py, portfolio_sync.py, scheduler.py
│   │   ├── sse_manager.py, strategy_runner.py
│   └── templates/      # Jinja2模板
├── strategies/         # 新策略模块 (开发中)
│   ├── momentum_breakthrough.py, volume_price.py
├── tasks.py            # 任务抽象基类 + 3个任务实现 + 注册表
├── conf.py             # 全局配置
├── data_source.py      # 数据源管理 (三级加载)
├── strategy.py         # 策略引擎 (旧)
├── risk.py / risk_manager.py  # 风险管理
├── tdx.py              # TDX文件解析
├── minute_collector.py # 分钟级采集
├── minute_data_manager.py  # 分钟数据管理
├── export.py           # TDX导出
├── comparison.py       # 结果比较
└── *.py                # 辅助模块

src/collect_info/       # 外部数据采集
├── accurate_stock_database.py
├── etf_stock_query.py
├── internet_stock_query.py
├── missing_code_finder.py
└── simple_stock_api.py
```

## 关键发现

### 1. 多个独立入口脚本

根目录存在 6 个入口脚本，缺少统一调度：

| 脚本 | 功能 |
|------|------|
| `run_daily.py` | 运行每日选股任务 (etf/short/mid) |
| `run_dashboard.py` | 启动 Dashboard 服务 |
| `run_minute_collector.py` | 盘中分钟级K线采集器 |
| `restart_dashboard.py` | 一键重启 Dashboard |
| `backfill_daily.py` | 批量补跑历史日期 |
| `_check.py` | 未知检查脚本 |

所有脚本都包含 `sys.path.append` 这段重复代码，这是包导入路径问题的征兆。

### 2. pyproject.toml 中 entry_points 未充分利用

已定义 `quant-dashboard = "quant_etf.dashboard.app:main"` 但未定义其他入口点。所有入口脚本可以通过 CLI 子命令统一。

### 3. 文档与代码不一致

- README 未覆盖 Dashboard、分钟K线、新策略模块
- DEVELOPMENT.md 引用了 Black/Ruff/mypy 但 pyproject.toml 中未配置
- `docs/design.md` 中提到的技术栈 (akshare) 与实际实现 (pytdx/TDX) 不一致
- `CONFIG.md` 存在但内容未知

### 4. 新旧两份策略代码并存

- `strategy.py` (旧): StrategyEngine, ETFScore, StockScore 等 — 被 tasks.py 使用
- `strategies/__init__.py` (新): MomentumBreakthroughStrategy, VolumePriceStrategy — 未集成到任务系统

### 5. 根目录规划文档散落

`plan.md`, `plan_daily_run.md`, `plan_main_refactor.md`, `plan_update_tdx.md` 均是执行完毕或过期的规划，应归档。

### 6. 测试覆盖率可提升

- 15 个测试文件，约 800 行
- 缺少对 Dashboard 模块、`collect_info`、分钟级采集、`strategies/` 的测试
- 无 CI 自动化

### 7. 配置管理

- `conf.py` 硬编码多个股票池，未从外部文件加载
- `TDX_DIR` 在 Windows 上默认指向 `C:\new_hxzq_hc_error`（疑似笔误）
- 缺少 `.env` 或 yaml 配置文件支持

## 优先级建议

### 高优先级

1. **统一入口脚本** — 创建 CLI 子命令系统，消除 6 个根目录脚本的重复代码
2. **更新 README** — 反映 Dashboard、分钟K线、新策略等当前功能
3. **整合新旧策略代码** — 明确 `strategy.py` 与 `strategies/` 的职责分工

### 中优先级

4. **补齐 dev 工具链** — pyproject.toml 添加 black/ruff/mypy 依赖和配置
5. **归档根目录 plan.md** — 清理已执行完毕的规划文档
6. **增加测试覆盖** — 针对 Dashboard、`collect_info`、新策略模块

### 低优先级

7. **配置外部化** — 股票池迁移到外部配置文件
8. **CI/CD 建立** — GitHub Actions 自动化测试
9. **修复 `TDX_DIR` 笔误** — `C:\new_hxzq_hc_error` → `C:\new_hxzq_hc`
10. **CodeGraph 索引** — 初始化 `.codegraph/` 提升导航效率
