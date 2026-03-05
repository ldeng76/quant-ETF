#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
准确的A股ETF基金数据库
包含沪市和深市的主要ETF基金信息

使用说明：
1. 导入模块：from accurate_stock_database import get_stock_database
2. 获取数据库：stock_db = get_stock_database()
3. 查询股票：name = stock_db.get("510050", "未知")

数据特点：
- 包含完整的ETF基金官方名称
- 包含基金管理公司信息
- 覆盖沪市(51/58开头)和深市(159开头)ETF
- 数据基于公开的金融信息整理
"""

import json
from typing import Dict

def get_stock_database() -> Dict[str, str]:
    """
    返回准确的股票信息数据库
    
    Returns:
        Dict[str, str]: 股票代码到名称的映射字典
    """
    accurate_stock_db = {
        # ==================== 沪市ETF (51/58开头) ====================
        
        # 宽基指数ETF
        "510050": "华夏上证50ETF",
        "510310": "易方达沪深300ETF",
        "510880": "华泰柏瑞红利ETF",
        "588010": "易方达上证科创板50ETF",
        
        # 行业ETF
        "512480": "国联安半导体ETF",
        "512660": "国泰中证军工ETF",
        "512070": "易方达中证证券ETF",
        "515020": "华宝中证银行ETF",
        "515220": "国泰中证煤炭ETF",
        "512690": "鹏华中证酒ETF",
        
        # 主题ETF
        "513090": "易方达中证港股通50ETF",
        "513100": "国泰纳斯达克100ETF",
        "513500": "博时标普500ETF",
        "513130": "华泰柏瑞南方东英恒生科技ETF",
        "513330": "华夏恒生互联网科技业ETF",
        
        # 商品ETF
        "518880": "华安黄金ETF",
        "511160": "国泰上证5年期国债ETF",
        "510170": "国联安上证商品ETF",
        
        # ==================== 深市ETF (159开头) ====================
        
        # 宽基指数ETF
        "159352": "易方达创业板ETF",
        "159957": "华夏创业板ETF",
        "159949": "华安创业板50ETF",
        "159991": "招商创业板50ETF",
        "159780": "鹏华中证科创创业50ETF",
        "159811": "博时科创板50ETF",
        "159560": "南方中证500ETF",
        "159516": "易方达中证1000ETF",
        "159590": "华夏中证2000ETF",
        
        # 行业ETF
        "159819": "嘉实中证稀有金属主题ETF",
        "159363": "华夏中证新能源汽车ETF",
        "159526": "华泰柏瑞中证医疗ETF",
        "159206": "国泰中证消费ETF",
        "159667": "华夏国证半导体芯片ETF",
        "159638": "华富中证人工智能产业ETF",
        "159713": "华泰柏瑞中证稀土产业ETF",
        "159652": "华夏中证5G通信主题ETF",
        "159886": "广发中证环保产业ETF",
        "159859": "天弘中证医疗ETF",
        "159567": "国泰中证生物科技ETF",
        "159775": "建信中证电池主题ETF",
        
        # 主题ETF
        "159202": "易方达中证一带一路主题ETF",
        "159742": "博时中证国企改革ETF",
        "159605": "广发中证央企创新驱动ETF",
        "159750": "南方中证粤港澳大湾区ETF",
        "159712": "汇添富中证长三角一体化发展ETF",
        "159312": "华夏中证京津冀协同发展ETF",
        "159941": "广发纳斯达克100ETF",
        "159265": "华夏中证智能汽车主题ETF",
        "159869": "华夏中证教育ETF",
        "159856": "国泰中证物联网主题ETF",
        
        # 其他ETF
        "159001": "易方达保证金货币ETF",
        "159985": "华夏豆粕ETF",
        "159697": "国泰中证畜牧养殖ETF",
        
        # ==================== 其他深市ETF (56开头) ====================
        
        "561280": "华泰柏瑞中证电力ETF",
        "562820": "国泰中证基建ETF",
        "562920": "华夏中证物流ETF",
        "561220": "国泰中证影视ETF",
        "560980": "富国中证智能制造ETF",
        "561380": "广发中证新材料主题ETF",
        "561700": "易方达中证高端装备制造ETF",
        "560080": "易方达中证创新药产业ETF",
        
        # ==================== 其他沪市ETF ====================
        
        "516390": "华泰柏瑞中证光伏产业ETF",
        "516020": "华宝中证化工产业ETF",
        "517520": "国泰中证动漫游戏ETF",
        "516130": "银华中证农业主题ETF",
    }
    
    return accurate_stock_db

def get_stock_info(code: str) -> Dict[str, str]:
    """
    获取单个股票的完整信息
    
    Args:
        code (str): 股票代码
        
    Returns:
        Dict[str, str]: 包含code, name, market的字典
    """
    stock_db = get_stock_database()
    
    if code in stock_db:
        # 判断市场
        if code.startswith('5') or code.startswith('1'):
            market = 'sz'  # 深市
        else:
            market = 'sh'  # 沪市
        
        return {
            'code': code,
            'name': stock_db[code],
            'market': market
        }
    else:
        return {
            'code': code,
            'name': f"未知基金{code}",
            'market': 'sz' if code.startswith('5') or code.startswith('1') else 'sh'
        }

def query_multiple_codes(codes: list) -> list:
    """
    批量查询多个股票代码
    
    Args:
        codes (list): 股票代码列表
        
    Returns:
        list: 股票信息列表
    """
    results = []
    for code in codes:
        stock_info = get_stock_info(code)
        results.append(stock_info)
    return results

def save_to_json(codes: list = None, filename: str = "stock_results.json"):
    """
    将股票信息保存为JSON文件
    
    Args:
        codes (list, optional): 股票代码列表，如果为None则保存所有
        filename (str, optional): 保存的文件名
    """
    if codes is None:
        # 保存所有数据
        stock_db = get_stock_database()
        results = []
        for code, name in stock_db.items():
            market = 'sz' if code.startswith('5') or code.startswith('1') else 'sh'
            results.append({
                'code': code,
                'name': name,
                'market': market
            })
    else:
        # 保存指定代码
        results = query_multiple_codes(codes)
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"数据已保存到 {filename}，共 {len(results)} 条记录")

def print_database_stats():
    """打印数据库统计信息"""
    stock_db = get_stock_database()
    
    # 统计各类ETF数量
    categories = {
        '沪市宽基': 0,
        '沪市行业': 0,
        '沪市主题': 0,
        '沪市商品': 0,
        '深市宽基': 0,
        '深市行业': 0,
        '深市主题': 0,
        '深市其他': 0,
        '其他': 0
    }
    
    for code in stock_db.keys():
        if code.startswith('51') and code[2] in '0123':
            categories['沪市宽基'] += 1
        elif code.startswith('51') and code[2] in '4567':
            categories['沪市行业'] += 1
        elif code.startswith('513'):
            categories['沪市主题'] += 1
        elif code.startswith('518') or code.startswith('511'):
            categories['沪市商品'] += 1
        elif code.startswith('159') and int(code[3]) < 5:
            categories['深市宽基'] += 1
        elif code.startswith('159') and int(code[3]) >= 5:
            categories['深市行业'] += 1
        elif code.startswith('1592') or code.startswith('1593'):
            categories['深市主题'] += 1
        elif code.startswith('56'):
            categories['其他'] += 1
        elif code.startswith('516') or code.startswith('517'):
            categories['其他'] += 1
        else:
            categories['深市其他'] += 1
    
    print("=" * 50)
    print("股票数据库统计信息")
    print("=" * 50)
    print(f"总记录数: {len(stock_db)}")
    print("\n分类统计:")
    for category, count in categories.items():
        if count > 0:
            print(f"  {category}: {count}")
    print("=" * 50)

def main():
    """主函数 - 演示如何使用"""
    print("A股ETF基金数据库")
    print("=" * 50)
    
    # 1. 获取数据库
    stock_db = get_stock_database()
    print(f"数据库包含 {len(stock_db)} 个ETF基金")
    
    # 2. 查询示例
    print("\n查询示例:")
    test_codes = ["510050", "159352", "512480", "518880"]
    for code in test_codes:
        info = get_stock_info(code)
        print(f"  {code}: {info['name']} ({info['market']})")
    
    # 3. 批量查询示例
    print("\n批量查询示例:")
    codes_to_query = ["510310", "510880", "159957"]
    results = query_multiple_codes(codes_to_query)
    for item in results:
        print(f"  {item['code']}: {item['name']}")
    
    # 4. 统计信息
    print_database_stats()
    
    # 5. 保存数据示例
    print("\n保存数据示例:")
    save_to_json(codes_to_query, "sample_stock_results.json")
    
    print("\n使用说明:")
    print("1. 导入: from accurate_stock_database import get_stock_database")
    print("2. 查询: name = get_stock_database().get('510050')")
    print("3. 批量: results = query_multiple_codes(['510050', '510310'])")

if __name__ == "__main__":
    main()
