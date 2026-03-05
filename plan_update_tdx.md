# 计划：使用 pytdx 库重写通达信数据读取

## 目标
使用 pytdx 库的 `TdxDailyBarReader` 类来正确读取本地通达信 .day 文件

## 修改步骤

### 1. 添加 pytdx 依赖
- 在 pyproject.toml 中添加 pytdx 依赖

### 2. 修改 tdx.py
- 导入 `TdxDailyBarReader` 
- 修改 `parse_tdx_day_file` 函数使用 pytdx 读取数据
- 保持函数返回值格式与原来一致（添加 pct_chg 列）

### 3. 更新测试用例
- 验证使用 pytdx 读取的数据是否正确

## pytdx 使用方法
```python
from pytdx.reader import TdxDailyBarReader
reader = TdxDailyBarReader()
df = reader.get_df("/path/to/sz000001.day")
# 返回 DataFrame: open, high, low, close, amount, volume，索引为 date
```
