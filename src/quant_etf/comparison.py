from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from loguru import logger
from quant_etf.conf import PROJECT_ROOT
from quant_etf.data_source import ETFDataSource

class ResultComparator:
    def __init__(self):
        self.results_dir = PROJECT_ROOT / "data" / "results"
        self._ds = None

    @property
    def ds(self):
        if self._ds is None:
            self._ds = ETFDataSource()
        return self._ds

    def find_previous_result_file(self, task_name: str, current_date_str: str, lookback_days: int = 3) -> Path | None:
        """
        查找指定日期之前最近的一个结果文件
        """
        try:
            current_date = datetime.strptime(current_date_str, "%Y-%m-%d")
        except ValueError:
            logger.error(f"Invalid date format: {current_date_str}")
            return None
        
        for i in range(1, lookback_days + 1):
            prev_date = current_date - timedelta(days=i)
            prev_date_str = prev_date.strftime("%Y-%m-%d")
            file_path = self.results_dir / prev_date_str / f"{task_name}.csv"
            if file_path.exists():
                return file_path
        return None

    def compare(self, task_name: str, current_date_str: str) -> str:
        """
        对比指定任务的今日结果与历史结果，返回格式化的报告字符串
        """
        current_file = self.results_dir / current_date_str / f"{task_name}.csv"
        if not current_file.exists():
            return f"No result file found for {task_name} on {current_date_str}"

        prev_file = self.find_previous_result_file(task_name, current_date_str)
        
        try:
            df_curr = pd.read_csv(current_file, dtype={"code": str} if current_file.exists() else None)
            if prev_file:
                df_prev = pd.read_csv(prev_file, dtype={"code": str} if prev_file.exists() else None)
            else:
                df_prev = pd.DataFrame()
        except Exception as e:
            return f"Error reading CSV files: {e}"

        if df_curr.empty and df_prev.empty:
            return "Both current and previous results are empty."

        # 确保 code 是字符串，去除可能的 .0 后缀（虽然保存时应该没问题，但读取时可能会被当做 float）
        if "code" in df_curr.columns:
            df_curr["code"] = df_curr["code"].astype(str).str.replace(r'\.0$', '', regex=True)
        if not df_prev.empty and "code" in df_prev.columns:
            df_prev["code"] = df_prev["code"].astype(str).str.replace(r'\.0$', '', regex=True)

        curr_codes = set(df_curr["code"]) if not df_curr.empty else set()
        prev_codes = set(df_prev["code"]) if not df_prev.empty else set()
        
        # 建立 code -> name 映射（优先使用 data_source 获取正确名称）
        if task_name == "etf":
            name_map = self.ds.get_etf_name_map()
        else:
            name_map = self.ds.get_stock_name_map()

        new_entries = curr_codes - prev_codes
        exits = prev_codes - curr_codes
        common = curr_codes & prev_codes

        lines = []
        lines.append(f"Comparison Report for {task_name.upper()}")
        if prev_file:
            lines.append(f"Current: {current_date_str} | Previous: {prev_file.parent.name}")
        else:
            lines.append(f"Current: {current_date_str} | Previous: None (First Run)")
        lines.append("-" * 40)

        if new_entries:
            lines.append("【NEW ENTRIES】(新增)")
            for code in new_entries:
                name = name_map.get(code, "Unknown")
                lines.append(f"  + {code} {name}")
            lines.append("")

        if exits:
            lines.append("【EXITS】(退出)")
            for code in exits:
                name = name_map.get(code, "Unknown")
                lines.append(f"  - {code} {name}")
            lines.append("")

        if common:
            lines.append("【CHANGES】(变动)")
            changes_found = False
            for code in common:
                row_curr = df_curr[df_curr["code"] == code].iloc[0]
                row_prev = df_prev[df_prev["code"] == code].iloc[0]
                
                # 根据任务类型比较不同字段
                if task_name == "etf":
                    val_curr = row_curr.get("target_weight", 0)
                    val_prev = row_prev.get("target_weight", 0)
                    diff = val_curr - val_prev
                    if abs(diff) > 0.001: # 忽略微小差异
                        changes_found = True
                        lines.append(f"  * {code} {name_map.get(code)}: Weight {val_prev:.2%} -> {val_curr:.2%} ({diff:+.2%})")
                
                elif task_name in ("short", "mid"):
                    val_curr = row_curr.get("score", 0)
                    val_prev = row_prev.get("score", 0)
                    diff = val_curr - val_prev
                    if abs(diff) > 0.0001:
                        changes_found = True
                        lines.append(f"  * {code} {name_map.get(code)}: Score {val_prev:.4f} -> {val_curr:.4f} ({diff:+.4f})")

            if not changes_found:
                lines.append("  (No significant changes)")
            lines.append("")
            
        return "\n".join(lines)
