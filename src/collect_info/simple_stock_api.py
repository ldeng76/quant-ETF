#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单的A股股票API查询工具
使用公开的API接口查询股票信息
"""

import requests
import json
import time
from typing import Dict, List, Optional
import hashlib

class SimpleStockAPI:
    """简单的股票API查询类"""
    
    def __init__(self, timeout: float = 2.0):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        self.timeout = timeout
    
    def get_market(self, code: str) -> str:
        """获取市场代码"""
        if code.startswith(('6', '5', '9')):
            return 'sh'  # 上海
        else:
            return 'sz'  # 深圳
    
    def query_by_sina_api(self, code: str) -> Optional[Dict]:
        """
        使用新浪财经API查询
        
        新浪API格式: http://hq.sinajs.cn/list=sh601006
        返回格式: var hq_str_sh601006="大秦铁路,6.390,6.390,6.370,...";
        """
        market = self.get_market(code)
        url = f"http://hq.sinajs.cn/list={market}{code}"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                content = response.text
                # 解析返回的数据
                if '="' in content:
                    data_str = content.split('="')[1].split('"')[0]
                    fields = data_str.split(',')
                    if len(fields) > 0:
                        return {
                            'code': code,
                            'name': fields[0],
                            'market': market,
                            'source': 'sina_api'
                        }
        except Exception as e:
            print(f"新浪API查询失败 {code}: {e}")
        
        return None
    
    def query_by_tencent_api(self, code: str) -> Optional[Dict]:
        """
        使用腾讯财经API查询
        
        腾讯API格式: http://qt.gtimg.cn/q=sh601006
        返回格式: v_sh601006="1~大秦铁路~601006~6.39~6.39~6.37~...";
        """
        market = self.get_market(code)
        url = f"http://qt.gtimg.cn/q={market}{code}"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                content = response.text
                # 解析返回的数据
                if '="' in content:
                    data_str = content.split('="')[1].split('"')[0]
                    fields = data_str.split('~')
                    if len(fields) > 1:
                        return {
                            'code': code,
                            'name': fields[1],
                            'market': market,
                            'source': 'tencent_api'
                        }
        except Exception as e:
            print(f"腾讯API查询失败 {code}: {e}")
        
        return None
    
    def query_by_163_api(self, code: str) -> Optional[Dict]:
        """
        使用网易财经API查询
        
        网易API格式: http://api.money.126.net/data/feed/0601398
        返回格式: _ntes_quote_callback({"0601398":{"name":"平安银行",...}});
        """
        market = self.get_market(code)
        # 网易使用不同的代码格式
        if market == 'sh':
            n163_code = '0' + code
        else:
            n163_code = '1' + code
        
        url = f"http://api.money.126.net/data/feed/{n163_code}"
        
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                content = response.text
                # 解析JSONP格式
                if content.startswith('_ntes_quote_callback('):
                    json_str = content[21:-2]  # 去掉回调函数包装
                    data = json.loads(json_str)
                    
                    if n163_code in data:
                        stock_data = data[n163_code]
                        return {
                            'code': code,
                            'name': stock_data.get('name', ''),
                            'market': market,
                            'source': '163_api'
                        }
        except Exception as e:
            print(f"网易API查询失败 {code}: {e}")
        
        return None
    
    def query_by_eastmoney_api(self, code: str) -> Optional[Dict]:
        """
        使用东方财富API查询
        
        东方财富API: https://push2.eastmoney.com/api/qt/stock/get
        """
        market = self.get_market(code)
        # 东方财富的市场代码: 1=上海, 0=深圳
        secid = f"{1 if market == 'sh' else 0}.{code}"
        
        url = f"https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            'secid': secid,
            'fields': 'f12,f13,f14',
            'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
            'invt': '2',
            'fltt': '2',
        }
        
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                if data.get('rc') == 0 and data.get('data'):
                    stock_data = data['data']
                    return {
                        'code': code,
                        'name': stock_data.get('f14', ''),
                        'market': market,
                        'source': 'eastmoney_api'
                    }
        except Exception as e:
            print(f"东方财富API查询失败 {code}: {e}")
        
        return None
    
    def query_stock(self, code: str) -> Optional[Dict]:
        """
        查询股票信息，尝试多个API
        
        Args:
            code: 股票代码
            
        Returns:
            Dict: 股票信息字典
        """
        print(f"查询股票: {code}")
        
        # 尝试不同的API（按可靠性排序：腾讯最稳，新浪偶有超时）
        apis = [
            self.query_by_tencent_api,
            self.query_by_sina_api,
            self.query_by_eastmoney_api,
            self.query_by_163_api,
        ]
        
        for api_func in apis:
            result = api_func(code)
            if result and result.get('name'):
                print(f"  成功: {result['name']} (来源: {result['source']})")
                return result
            
            # 避免请求过快
            time.sleep(0.1)
        
        print(f"  失败: 所有API查询均失败")
        return None
    
    def batch_query(self, codes: List[str], delay: float = 0.3) -> List[Dict]:
        """
        批量查询股票
        
        Args:
            codes: 股票代码列表
            delay: 查询间隔
            
        Returns:
            List[Dict]: 查询结果列表
        """
        results = []
        
        print(f"开始批量查询 {len(codes)} 个股票...")
        
        for i, code in enumerate(codes, 1):
            print(f"[{i}/{len(codes)}] ", end='')
            
            result = self.query_stock(code)
            if result:
                results.append(result)
            else:
                # 添加失败记录
                results.append({
                    'code': code,
                    'name': None,
                    'market': self.get_market(code),
                    'source': None,
                    'error': '查询失败'
                })
            
            # 延迟
            if i < len(codes):
                time.sleep(delay)
        
        print(f"\n批量查询完成，成功: {len([r for r in results if r['name']])}/{len(codes)}")
        return results
    
    def save_to_json(self, results: List[Dict], filename: str = "stock_api_results.json"):
        """保存结果到JSON文件"""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"结果已保存到 {filename}")
        except Exception as e:
            print(f"保存失败: {e}")


def example_usage():
    """使用示例"""
    print("=" * 60)
    print("简单股票API查询工具")
    print("=" * 60)
    
    # 创建API实例
    api = SimpleStockAPI()
    
    # 示例1: 查询单个股票
    print("\n1. 查询单个股票:")
    result = api.query_stock("510050")
    if result:
        print(f"   代码: {result['code']}")
        print(f"   名称: {result['name']}")
        print(f"   市场: {result['market']}")
        print(f"   来源: {result['source']}")
    
    # 示例2: 批量查询
    print("\n2. 批量查询示例:")
    test_codes = ["510310", "159352", "000001", "600036"]
    results = api.batch_query(test_codes, delay=0.2)
    
    # 显示结果
    print("\n查询结果:")
    for result in results:
        if result['name']:
            print(f"  ✓ {result['code']}: {result['name']}")
        else:
            print(f"  ✗ {result['code']}: 查询失败")
    
    # 示例3: 保存结果
    print("\n3. 保存结果:")
    api.save_to_json(results, "example_api_results.json")
    
    print("\n" + "=" * 60)
    print("示例完成！")
    print("=" * 60)


def main():
    """命令行入口"""
    import sys
    
    if len(sys.argv) > 1:
        # 从命令行参数获取代码
        codes = sys.argv[1:]
        print(f"查询 {len(codes)} 个股票: {', '.join(codes)}")
        
        api = SimpleStockAPI()
        results = api.batch_query(codes, delay=0.3)
        api.save_to_json(results)
        
        # 显示统计
        success = len([r for r in results if r['name']])
        print(f"\n统计: 成功 {success}/{len(codes)}")
    else:
        # 显示使用说明和示例
        print("使用说明:")
        print("  python simple_stock_api.py <股票代码1> <股票代码2> ...")
        print("\n示例:")
        print("  python simple_stock_api.py 510050 510310 000001")
        print("\n运行演示示例:")
        example_usage()


if __name__ == "__main__":
    main()
