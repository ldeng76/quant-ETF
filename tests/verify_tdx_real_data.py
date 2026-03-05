import sys
from pathlib import Path
from loguru import logger
import pandas as pd

# 将项目根目录添加到 sys.path，以便导入 quant_etf
project_root = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(project_root))

from quant_etf.tdx import get_tdx_path, parse_tdx_day_file
from quant_etf.conf import TDX_VIPDOC_DIR

def verify_tdx_read(code: str, name: str):
    logger.info(f"正在尝试读取 {name} ({code}) 的通达信数据...")
    
    # 1. 获取文件路径
    file_path = get_tdx_path(code)
    if not file_path:
        logger.error(f"未找到 {code} 的通达信数据文件！")
        logger.info(f"预期查找路径: {TDX_VIPDOC_DIR}")
        return

    logger.info(f"找到文件: {file_path}")
    
    # 2. 解析文件
    try:
        df = parse_tdx_day_file(file_path)
        if df.empty:
            logger.warning(f"文件 {file_path} 解析结果为空！")
            return

        logger.info(f"成功读取 {len(df)} 条记录")
        
        # 3. 展示最近 5 条数据
        logger.info("最近 5 个交易日数据:")
        print(df.tail())
        
        # 4. 价格合理性检查
        last_close = df.iloc[-1]["close"]
        logger.info(f"最新收盘价: {last_close}")
        
        if last_close > 100 or last_close < 0.1:
            logger.warning(f"价格 ({last_close}) 看起来不太合理（过高或过低），请检查是否缩放因子有误！")
            logger.info("如果是 ETF，通常价格在 0.5 ~ 10.0 之间。")
        else:
            logger.info("价格范围看起来是合理的。")
            
    except Exception as e:
        logger.exception(f"读取或解析过程中发生错误: {e}")

if __name__ == "__main__":
    # 配置 logger 输出到控制台
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    logger.info(f"当前配置的通达信 VIPDOC 目录: {TDX_VIPDOC_DIR}")
    if not TDX_VIPDOC_DIR.exists():
        logger.error(f"目录不存在: {TDX_VIPDOC_DIR}，请检查 conf.py 中的配置！")
    else:
        # 测试几个典型的 ETF
        verify_tdx_read("510050", "上证50ETF")  # 沪市 ETF
        verify_tdx_read("159915", "创业板ETF")  # 深市 ETF (如果有的话)
        verify_tdx_read("510300", "沪深300ETF")
