"""测试前复权性能优化"""
import time
from loguru import logger
from quant_etf.data_source import ETFDataSource

# 配置日志
logger.remove()
logger.add(lambda msg: print(msg, end=""), level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

def test_performance():
    """测试加载所有ETF的性能"""
    ds = ETFDataSource()
    
    # 测试ETF列表（部分）
    test_codes = [
        "510050", "510310", "159352", "510880", "561280",
        "159957", "159949", "159991", "159780", "159811"
    ]
    
    start = time.time()
    logger.info(f"开始加载 {len(test_codes)} 支ETF（带前复权）...")
    
    for code in test_codes:
        t0 = time.time()
        df = ds.load_data(code, adjust_qfq=True)
        elapsed = time.time() - t0
        logger.info(f"{code}: {len(df)} 条数据, 耗时 {elapsed:.2f}秒")
    
    total = time.time() - start
    logger.info(f"\n总耗时: {total:.2f}秒")
    logger.info(f"平均每支ETF: {total/len(test_codes):.2f}秒")
    
    return total

if __name__ == "__main__":
    test_performance()
