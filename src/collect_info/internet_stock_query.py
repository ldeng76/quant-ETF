#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从互联网实时查询A股股票名称的Python脚本
支持多种数据源和查询方式
"""

import requests
import json
import time
import re
import urllib.parse
from typing import Dict, List, Optional, Tuple
import logging
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class StockQuery:
    """股票查询类"""
    
    def __init__(self, timeout: int = 10, retry_times: int = 2):
        """
        初始化股票查询
        
        Args:
            timeout: 请求超时时间（秒）
            retry_times: 重试次数
        """
        self.timeout = timeout
        self.retry_times = retry_times
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        # 数据源配置
        self.data_sources = [
            {
                'name': '东方财富',
                'url_template': 'http://quote.eastmoney.com/{market}{code}.html',
                'parser': self._parse_eastmoney
            },
            {
                'name': '新浪财经',
                'url_template': 'https://finance.sina.com.cn/realstock/company/{market}{code}/nc.shtml',
                'parser': self._parse_sina
            },
            {
                'name': '腾讯财经',
                'url_template': 'https://gu.qq.com/{market}{code}',
                'parser': self._parse_tencent
            },
            {
                'name': '网易财经',
                'url_template': 'https://quotes.money.163.com/{market}{code}.html',
                'parser': self._parse_163
            },
            {
                'name': '雪球',
                'url_template': 'https://xueqiu.com/S/{market}{code}',
                'parser': self._parse_xueqiu
            }
        ]
    
    def get_market_prefix(self, code: str) -> str:
        """
        根据股票代码判断市场
        
        Args:
            code: 股票代码
            
        Returns:
            str: 市场前缀 ('sh' 或 'sz')
        """
        # 沪市: 6开头, 5开头(ETF), 9开头(B股)
        # 深市: 0开头, 3开头(创业板), 1开头(ETF), 2开头(B股)
        if code.startswith('6') or code.startswith('5') or code.startswith('9'):
            return 'sh'  # 上海
        elif code.startswith('0') or code.startswith('3') or code.startswith('1') or code.startswith('2'):
            return 'sz'  # 深圳
        else:
            # 默认按深市处理
            return 'sz'
    
    def _parse_eastmoney(self, html: str, code: str) -> Optional[str]:
        """解析东方财富页面"""
        try:
            # 方法1: 从title中提取
            title_pattern = r'<title>([^<]+)</title>'
            match = re.search(title_pattern, html)
            if match:
                title = match.group(1).strip()
                # 清理标题
                title = title.replace('()', '')
                title = title.replace('行情中心_东方财富网', '')
                title = title.replace('股票行情', '')
                title = title.replace('_东方财富', '')
                if title and len(title) > 1:
                    return title.split()[0] if ' ' in title else title
            
            # 方法2: 从股票名称标签中提取
            name_patterns = [
                r'<div class="qphox[^>]*>([^<]+)</div>',
                r'<h1[^>]*>([^<]+)</h1>',
                r'"stockName":"([^"]+)"',
                r'<span class="stock-name">([^<]+)</span>',
            ]
            
            for pattern in name_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    if name and len(name) > 1:
                        return name
            
            return None
            
        except Exception as e:
            logger.error(f"解析东方财富页面失败: {e}")
            return None
    
    def _parse_sina(self, html: str, code: str) -> Optional[str]:
        """解析新浪财经页面"""
        try:
            # 从title中提取
            title_pattern = r'<title>([^<]+)</title>'
            match = re.search(title_pattern, html)
            if match:
                title = match.group(1).strip()
                # 清理标题
                title = title.replace('_新浪财经_新浪网', '')
                title = title.replace('股票行情', '')
                title = title.replace('()', '')
                if title and len(title) > 1:
                    # 提取股票名称部分
                    parts = title.split('_')
                    if len(parts) > 0:
                        return parts[0]
            
            # 尝试其他模式
            name_patterns = [
                r'<h1[^>]*>([^<]+)</h1>',
                r'<div class="stock-name">([^<]+)</div>',
                r'"name":"([^"]+)"',
            ]
            
            for pattern in name_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    if name and len(name) > 1:
                        return name
            
            return None
            
        except Exception as e:
            logger.error(f"解析新浪财经页面失败: {e}")
            return None
    
    def _parse_tencent(self, html: str, code: str) -> Optional[str]:
        """解析腾讯财经页面"""
        try:
            # 腾讯财经通常有JSON数据
            json_pattern = r'window\.__INITIAL_STATE__\s*=\s*({[^;]+});'
            match = re.search(json_pattern, html)
            if match:
                json_str = match.group(1)
                try:
                    data = json.loads(json_str)
                    # 尝试从不同路径获取名称
                    paths = [
                        'stockInfo.name',
                        'stockInfo.stock_name',
                        'quote.name',
                        'quote.stock_name'
                    ]
                    
                    for path in paths:
                        keys = path.split('.')
                        value = data
                        for key in keys:
                            if isinstance(value, dict) and key in value:
                                value = value[key]
                            else:
                                value = None
                                break
                        
                        if value and isinstance(value, str) and len(value) > 1:
                            return value.strip()
                except json.JSONDecodeError:
                    pass
            
            # 从title中提取
            title_pattern = r'<title>([^<]+)</title>'
            match = re.search(title_pattern, html)
            if match:
                title = match.group(1).strip()
                title = title.replace('_腾讯财经', '')
                title = title.replace('股票行情', '')
                if title and len(title) > 1:
                    return title.split()[0] if ' ' in title else title
            
            return None
            
        except Exception as e:
            logger.error(f"解析腾讯财经页面失败: {e}")
            return None
    
    def _parse_163(self, html: str, code: str) -> Optional[str]:
        """解析网易财经页面"""
        try:
            # 从title中提取
            title_pattern = r'<title>([^<]+)</title>'
            match = re.search(title_pattern, html)
            if match:
                title = match.group(1).strip()
                title = title.replace('_行情中心_网易财经', '')
                title = title.replace('股票行情', '')
                if title and len(title) > 1:
                    parts = title.split('_')
                    if len(parts) > 0:
                        return parts[0]
            
            # 尝试其他模式
            name_patterns = [
                r'<h1[^>]*>([^<]+)</h1>',
                r'<div class="stock_name">([^<]+)</div>',
                r'"name":"([^"]+)"',
            ]
            
            for pattern in name_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    if name and len(name) > 1:
                        return name
            
            return None
            
        except Exception as e:
            logger.error(f"解析网易财经页面失败: {e}")
            return None
    
    def _parse_xueqiu(self, html: str, code: str) -> Optional[str]:
        """解析雪球页面"""
        try:
            # 雪球通常需要处理重定向或动态内容
            # 简单从title中提取
            title_pattern = r'<title>([^<]+)</title>'
            match = re.search(title_pattern, html)
            if match:
                title = match.group(1).strip()
                title = title.replace(' - 雪球', '')
                if title and len(title) > 1:
                    return title
            
            return None
            
        except Exception as e:
            logger.error(f"解析雪球页面失败: {e}")
            return None
    
    def query_stock_name(self, code: str) -> Optional[Dict]:
        """
        查询股票名称
        
        Args:
            code: 股票代码
            
        Returns:
            Optional[Dict]: 包含股票信息的字典，或None
        """
        market = self.get_market_prefix(code)
        logger.info(f"开始查询股票 {code} (市场: {market})")
        
        for source in self.data_sources:
            try:
                url = source['url_template'].format(market=market, code=code)
                logger.debug(f"尝试数据源: {source['name']}, URL: {url}")
                
                for attempt in range(self.retry_times):
                    try:
                        response = requests.get(
                            url, 
                            headers=self.headers, 
                            timeout=self.timeout,
                            allow_redirects=True
                        )
                        
                        if response.status_code == 200:
                            html_content = response.text
                            name = source['parser'](html_content, code)
                            
                            if name:
                                logger.info(f"从 {source['name']} 成功获取: {code} -> {name}")
                                return {
                                    'code': code,
                                    'name': name,
                                    'market': market,
                                    'source': source['name'],
                                    'url': url,
                                    'timestamp': datetime.now().isoformat()
                                }
                            else:
                                logger.debug(f"从 {source['name']} 解析名称失败")
                        else:
                            logger.debug(f"{source['name']} 返回状态码: {response.status_code}")
                        
                        # 短暂延迟后重试
                        if attempt < self.retry_times - 1:
                            time.sleep(0.5)
                            
                    except requests.exceptions.RequestException as e:
                        logger.debug(f"{source['name']} 请求失败 (尝试 {attempt+1}/{self.retry_times}): {e}")
                        if attempt < self.retry_times - 1:
                            time.sleep(1)
                
            except Exception as e:
                logger.error(f"处理数据源 {source['name']} 时出错: {e}")
                continue
            
            # 切换数据源前短暂延迟
            time.sleep(0.2)
        
        logger.warning(f"所有数据源查询失败: {code}")
        return None
    
    def batch_query(self, codes: List[str], delay: float = 0.5) -> List[Dict]:
        """
        批量查询股票名称
        
        Args:
            codes: 股票代码列表
            delay: 查询间隔（秒）
            
        Returns:
            List[Dict]: 查询结果列表
        """
        results = []
        total = len(codes)
        
        logger.info(f"开始批量查询 {total} 个股票代码")
        
        for i, code in enumerate(codes, 1):
            logger.info(f"进度: {i}/{total} - 查询 {code}")
            
            result = self.query_stock_name(code)
            if result:
                results.append(result)
                logger.info(f"成功: {code} -> {result['name']}")
            else:
                logger.warning(f"失败: {code}")
                # 添加失败记录
                results.append({
                    'code': code,
                    'name': None,
                    'market': self.get_market_prefix(code),
                    'source': None,
                    'error': '查询失败',
                    'timestamp': datetime.now().isoformat()
                })
            
            # 避免请求过快
            if i < total:
                time.sleep(delay)
        
        logger.info(f"批量查询完成，成功: {len([r for r in results if r['name']])}/{total}")
        return results
    
    def save_results(self, results: List[Dict], filename: str = "stock_query_results.json"):
        """
        保存查询结果到JSON文件
        
        Args:
            results: 查询结果列表
            filename: 保存的文件名
        """
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            logger.info(f"结果已保存到 {filename}")
        except Exception as e:
            logger.error(f"保存结果失败: {e}")
    
    def print_results(self, results: List[Dict]):
        """打印查询结果"""
        print("\n" + "=" * 80)
        print("股票查询结果")
        print("=" * 80)
        
        success_count = 0
        fail_count = 0
        
        for result in results:
            if result.get('name'):
                success_count += 1
                print(f"✓ {result['code']}: {result['name']} ({result.get('market', 'N/A')})")
                if result.get('source'):
                    print(f"  数据源: {result['source']}")
            else:
                fail_count += 1
                print(f"✗ {result['code']}: 查询失败")
                if result.get('error'):
                    print(f"  错误: {result['error']}")
        
        print("=" * 80)
        print(f"总计: {len(results)} | 成功: {success_count} | 失败: {fail_count}")
        print("=" * 80)


def example_usage():
    """使用示例"""
    print("股票查询工具使用示例")
    print("=" * 60)
    
    # 创建查询实例
    query = StockQuery(timeout=5, retry_times=1)
    
    # 示例1: 查询单个股票
    print("\n1. 查询单个股票:")
    result = query.query_stock_name("510050")
    if result:
        print(f"   代码: {result['code']}")
        print(f"   名称: {result['name']}")
        print(f"   市场: {result['market']}")
        print(f"   数据源: {result.get('source', 'N/A')}")
    else:
        print("   查询失败")
    
    # 示例2: 批量查询
    print("\n2. 批量查询示例:")
    test_codes = ["510310", "159352", "512480", "000001"]
    results = query.batch_query(test_codes, delay=0.3)
    
    # 打印结果
    query.print_results(results)
    
    # 示例3: 保存结果
    print("\n3. 保存结果到文件:")
    query.save_results(results, "example_query_results.json")
    
    # 示例4: 处理原始查询列表
    print("\n4. 处理原始查询列表（前5个）:")
    original_codes = [
        "510050", "510310", "159352", "510880", "561280",
        "159957", "159949", "159991", "159780", "159811",
    ]
    
    # 只查询前5个作为示例
    sample_results = query.batch_query(original_codes[:5], delay=0.5)
    query.print_results(sample_results)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='从互联网查询A股股票名称')
    parser.add_argument('codes', nargs='*', help='股票代码列表')
    parser.add_argument('--file', '-f', help='包含股票代码的文件（每行一个代码）')
    parser.add_argument('--output', '-o', default='stock_results.json', help='输出文件名')
    parser.add_argument('--delay', '-d', type=float, default=0.5, help='查询间隔（秒）')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    
    args = parser.parse_args()
    
    # 设置日志级别
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 获取股票代码
    codes = []
    
    if args.file:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                for line in f:
                    code = line.strip()
                    if code and not code.startswith('#'):  # 跳过空行和注释
                        codes.append(code)
            print(f"从文件 {args.file} 读取了 {len(codes)} 个股票代码")
        except Exception as e:
            print(f"读取文件失败: {e}")
            return
    
    if args.codes:
        codes.extend(args.codes)
    
    if not codes:
        print("错误: 未提供股票代码")
        parser.print_help()
        return
    
    # 去重
    codes = list(set(codes))
    print(f"开始查询 {len(codes)} 个股票代码...")
    
    # 创建查询实例并执行
    query = StockQuery(timeout=8, retry_times=2)
    results = query.batch_query(codes, delay=args.delay)
    
    # 保存结果
    query.save_results(results, args.output)
    
    # 打印摘要
    success_count = len([r for r in results if r.get('name')])
    print(f"\n查询完成！成功: {success_count}/{len(codes)}")
    print(f"结果已保存到 {args.output}")


if __name__ == "__main__":
    # 运行示例
    # example_usage()
    
    # 或者运行命令行版本
    import sys
    if len(sys.argv) > 1:
        main()
    else:
        print("使用说明:")
        print("  1. 直接运行示例: python internet_stock_query.py")
        print("  2. 命令行查询: python internet_stock_query.py 510050 510310")
        print("  3. 从文件查询: python internet_stock_query.py --file codes.txt")
        print("\n运行示例:")
        example_usage()
