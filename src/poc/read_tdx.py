import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pytdx.reader import TdxDailyBarReader, TdxFileNotFoundException

def test1():
    """读取并打印指定 TDX 日线文件尾部数据。"""
    reader = TdxDailyBarReader()
    # df = reader.get_df(r"C:\new_hxzq_hc\vipdoc\sz\lday\sz000001.day")
    df = reader.get_df(r"C:\new_hxzq_hc\vipdoc\sh\lday\sh510310.day")
    print(df.tail(5))

if __name__ == '__main__':
    test1()
