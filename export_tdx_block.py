#!/usr/bin/env python
"""
导出通达信板块股票列表

通达信板块文件格式：
- .blk 文件存储在 T0002/blocknew/ 目录
- 每个板块一个 .blk 文件
- 文件格式：每只股票6字节（市场代码2字节 + 股票代码4字节）
  市场代码：00=SZ, 01=SH
"""

import struct
import csv
from pathlib import Path

# 通达信安装路径
TDX_ROOT = Path(r"C:\new_hxzq_hc")
BLOCK_DIR = TDX_ROOT / "T0002" / "blocknew"

# 板块代码映射（文件名 -> 板块名称）
BLOCK_NAMES = {
    "TDXRG": "通达信热股",
    "MYETF": "我的ETF",
    "ALRD": "已发出警报",
    "ALZXC": "预警线持仓",
    "ZXG": "自选股",
    "ZXGMORE": "自选股扩展",
    "ETF66Z": "66只ETF",
    "GFETF": "广发ETF",
    "JLS0915": "金螺丝0915",
    "ZXFDC": "资金流热度",
    "TJG": "添加股",
    "高分etf": "高分ETF",
}


def parse_blk_file(blk_path: Path) -> list[tuple[str, str]]:
    """
    解析 .blk 文件，返回 (股票代码, 市场) 列表
    
    格式：纯文本，每行一只股票
    - 7位数字：第1位市场代码（0=SZ深市, 1=SH沪市），后6位股票代码
    - 使用 \r\n 分隔
    """
    stocks = []
    
    with open(blk_path, "r", encoding="gbk") as f:
        content = f.read()
    
    # 按行分割
    lines = content.strip().split("\n")
    
    print(f"  文件大小：{len(content)} 字节，{len(lines)} 行")
    print(f"  检测到格式：纯文本，\\r\\n 分隔")
    
    for line in lines:
        line = line.strip()
        if not line or not line.isdigit():
            print(f"  跳过无效行：{line}")
            continue
        
        if len(line) == 7:
            market_code = line[0]
            stock_code = line[1:]
            
            if market_code == "0":
                market = "SZ"
            elif market_code == "1":
                market = "SH"
            else:
                market = f"UNKNOWN({market_code})"
            
            stocks.append((stock_code, market))
            print(f"  {stock_code} ({market})")
        else:
            print(f"  警告：异常长度的代码 {line}")
    
    return stocks


def get_stock_names_via_akshare(codes: list[str]) -> dict[str, str]:
    """
    使用 akshare 获取股票名称（批量查询）
    
    Args:
        codes: 股票代码列表，如 ["000031", "600030", ...]
    
    Returns:
        {code: name} 字典，如 {"000031": "深物业A", "600030": "中信证券"}
    """
    try:
        import akshare as ak
        
        print("  使用 akshare 获取股票名称...")
        
        # 获取A股实时行情数据（包含代码和名称）
        df = ak.stock_zh_a_spot_em()
        
        # 筛选目标股票
        result_dict = {}
        for _, row in df[df["代码"].isin(codes)].iterrows():
            result_dict[row["代码"]] = row["名称"]
        
        found = set(result_dict.keys())
        missing = set(codes) - found
        
        if missing:
            print(f"  警告：以下股票未找到：{sorted(missing)}")
        
        return result_dict
        
    except Exception as e:
        print(f"  使用 akshare 获取股票名称失败：{e}")
        print("  回退到本地名称映射...")
        return {}


def get_stock_name_from_meta(stock_code: str, _cache: dict = {}) -> str:
    """从项目的stock_code_name.json获取股票名称（带缓存，备用方案）"""
    import json
    
    # 使用缓存避免重复读取
    if not _cache:
        meta_file = Path(__file__).parent / "data" / "meta" / "stock_code_name.json"
        if not meta_file.exists():
            return ""
        
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                stock_list = json.load(f)
            # 转换为字典：{"000031": "深物业A", ...}
            _cache.update({stock["code"]: stock["name"] for stock in stock_list})
        except Exception as e:
            print(f"  读取股票名称失败：{e}")
            return ""
    
    return _cache.get(stock_code, "")


def export_block_to_csv(
    block_name: str,
    output_path: Path,
    include_name: bool = True
) -> int:
    """
    导出板块到CSV文件
    
    Args:
        block_name: 板块文件名（不含.blk后缀）
        output_path: 输出CSV路径
        include_name: 是否包含股票名称
    
    Returns:
        导出的股票数量
    """
    blk_file = BLOCK_DIR / f"{block_name}.blk"
    
    if not blk_file.exists():
        print(f"错误：板块文件不存在 {blk_file}")
        return 0
    
    print(f"解析板块文件：{blk_file.name}")
    print(f"板块名称：{BLOCK_NAMES.get(block_name, block_name)}")
    
    # 解析股票列表
    stocks = parse_blk_file(blk_file)
    print(f"找到 {len(stocks)} 只股票")
    
    # 获取股票名称（如果需要）
    stock_data = []
    if include_name:
        print("获取股票名称...")
        
        # 优先使用 akshare 批量获取（实时数据，更准确）
        codes = [code for code, _ in stocks]
        name_map = get_stock_names_via_akshare(codes)
        
        # 如果 akshare 失败或部分缺失，使用本地映射补充
        if not name_map:
            print("  使用本地名称映射...")
            for code in codes:
                name = get_stock_name_from_meta(code)
                if name:
                    name_map[code] = name
        
        # 构建数据
        for code, market in stocks:
            name = name_map.get(code, "")
            stock_data.append({"code": code, "name": name, "market": market})
            if name:
                print(f"  {code} - {name}")
            else:
                print(f"  {code} (未找到名称)")
    else:
        stock_data = [{"code": code, "market": market} for code, market in stocks]
        for item in stock_data:
            print(f"  {item['code']}")
    
    # 写入CSV
    if include_name:
        fieldnames = ["code", "name", "market"]
    else:
        fieldnames = ["code", "market"]
    
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(stock_data)
    
    print(f"\n导出成功：{output_path}")
    print(f"总计 {len(stock_data)} 只股票")
    
    return len(stock_data)


def format_stock_pool_python(stocks: list[dict]) -> str:
    """格式化为Python列表格式（用于替换conf.py中的STOCK_POOL）"""
    codes = [f'    "{item["code"]}"' for item in stocks]
    # 每行5个代码
    lines = []
    for i in range(0, len(codes), 5):
        lines.append(", ".join(codes[i:i+5]))
    
    result = "STOCK_POOL = [\n"
    result += ",\n".join(lines)
    result += "\n]"
    return result


def main():
    print("=" * 60)
    print("通达信板块导出工具")
    print("=" * 60)
    print()
    
    # 导出【通达信热股】板块
    block_name = "TDXRG"
    output_csv = Path(__file__).parent / "tdx_redu_temp.csv"
    
    count = export_block_to_csv(block_name, output_csv, include_name=True)
    
    if count > 0:
        print("\n" + "=" * 60)
        print("Python STOCK_POOL 格式（可直接替换conf.py中的内容）：")
        print("=" * 60)
        
        # 读取CSV并格式化
        with open(output_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            stocks = list(reader)
        
        python_code = format_stock_pool_python(stocks)
        print(python_code)
        
        print("\n" + "=" * 60)
        print("下一步：")
        print("1. 检查临时CSV文件：", output_csv)
        print("2. 确认无误后，运行以下命令更新conf.py：")
        print(f"   python {Path(__file__).name} --update")
        print("=" * 60)
    else:
        print("\n导出失败，请检查板块文件是否存在")


if __name__ == "__main__":
    main()
