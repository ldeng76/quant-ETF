#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专门用于查询A股ETF基金的脚本
针对ETF基金的查询优化
"""

import requests
import json
import time
import re
from typing import Dict, List, Optional
from datetime import datetime

class ETFQuery:
    """ETF基金查询类"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        
        # ETF特定的数据源
        self.etf_sources = [
            {
                'name': '天天基金网',
                'url_template': 'http://fund.eastmoney.com/{code}.html',
                'parser': self._parse_eastmoney_fund
            },
            {
                'name': '新浪基金',
                'url_template': 'https://finance.sina.com.cn/fund/quotes/{code}/bc.shtml',
                'parser': self._parse_sina_fund
            },
            {
                'name': '腾讯财经ETF',
                'url_template': 'https://gu.qq.com/{market}{code}',
                'parser': self._parse_tencent_etf
            },
        ]
    
    def is_etf_code(self, code: str) -> bool:
        """判断是否为ETF代码"""
        # ETF代码通常以 51, 58, 159 开头
        return code.startswith(('51', '58', '159', '56'))
    
    def get_etf_market(self, code: str) -> str:
        """获取ETF市场代码"""
        if code.startswith(('51', '58')):
            return 'sh'  # 沪市ETF
        elif code.startswith(('159', '56')):
            return 'sz'  # 深市ETF
        else:
            return 'sz'  # 默认
    
    def _parse_eastmoney_fund(self, html: str, code: str) -> Optional[str]:
        """解析天天基金网页面"""
        try:
            # 从title中提取
            title_pattern = r'<title>([^<]+)</title>'
            match = re.search(title_pattern, html)
            if match:
                title = match.group(1).strip()
                # 清理标题
                title = title.replace('()', '')
                title = title.replace('基金档案', '')
                title = title.replace('天天基金网', '')
                title = title.replace('基金', '')
                if title and len(title) > 1:
                    return title.split()[0]
            
            # 从基金名称标签中提取
            name_patterns = [
                r'<div class="fundDetail-tit">([^<]+)</div>',
                r'<h1[^>]*>([^<]+)</h1>',
                r'"fundName":"([^"]+)"',
                r'<span class="funCur-FundName">([^<]+)</span>',
            ]
            
            for pattern in name_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    if name and len(name) > 1:
                        return name
            
            return None
            
        except Exception as e:
            print(f"解析天天基金网失败 {code}: {e}")
            return None
    
    def _parse_sina_fund(self, html: str, code: str) -> Optional[str]:
        """解析新浪基金页面"""
        try:
            # 从title中提取
            title_pattern = r'<title>([^<]+)</title>'
            match = re.search(title_pattern, html)
            if match:
                title = match.group(1).strip()
                title = title.replace('_基金净值_新浪财经_新浪网', '')
                title = title.replace('基金', '')
                if title and len(title) > 1:
                    parts = title.split('_')
                    if len(parts) > 0:
                        return parts[0]
            
            return None
            
        except Exception as e:
            print(f"解析新浪基金失败 {code}: {e}")
            return None
    
    def _parse_tencent_etf(self, html: str, code: str) -> Optional[str]:
        """解析腾讯财经ETF页面"""
        try:
            # 查找ETF名称
            patterns = [
                r'<title>([^<]+)</title>',
                r'"name":"([^"]+)"',
                r'<h1[^>]*>([^<]+)</h1>',
                r'ETF名称[^>]*>([^<]+)<',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    name = match.group(1).strip()
                    name = name.replace('_腾讯财经', '')
                    name = name.replace('ETF', '')
                    if name and len(name) > 1:
                        return name
            
            return None
            
        except Exception as e:
            print(f"解析腾讯财经ETF失败 {code}: {e}")
            return None
    
    def query_etf_by_api(self, code: str) -> Optional[Dict]:
        """使用API查询ETF信息"""
        market = self.get_etf_market(code)
        
        # 尝试新浪API
        try:
            url = f"http://hq.sinajs.cn/list={market}{code}"
            response = self.session.get(url, timeout=5)
            if response.status_code == 200:
                content = response.text
                if '="' in content:
                    data_str = content.split('="')[1].split('"')[0]
                    fields = data_str.split(',')
                    if len(fields) > 0 and fields[0]:
                        return {
                            'code': code,
                            'name': fields[0],
                            'market': market,
                            'source': 'sina_api',
                            'type': 'etf'
                        }
        except:
            pass
        
        # 尝试腾讯API
        try:
            url = f"http://qt.gtimg.cn/q={market}{code}"
            response = self.session.get(url, timeout=5)
            if response.status_code == 200:
                content = response.text
                if '="' in content:
                    data_str = content.split('="')[1].split('"')[0]
                    fields = data_str.split('~')
                    if len(fields) > 1 and fields[1]:
                        return {
                            'code': code,
                            'name': fields[1],
                            'market': market,
                            'source': 'tencent_api',
                            'type': 'etf'
                        }
        except:
            pass
        
        return None
    
    def query_etf(self, code: str) -> Optional[Dict]:
        """查询ETF基金信息"""
        if not self.is_etf_code(code):
            print(f"警告: {code} 可能不是ETF代码")
        
        print(f"查询ETF: {code}")
        
        # 首先尝试API查询（更快）
        api_result = self.query_etf_by_api(code)
        if api_result:
            print(f"  API查询成功: {api_result['name']}")
            return api_result
        
        # 如果API失败，尝试网页查询
        market = self.get_etf_market(code)
        
        for source in self.etf_sources:
            try:
                url = source['url_template'].format(market=market, code=code)
                print(f"  尝试 {source['name']}: {url}")
                
                response = self.session.get(url, timeout=8)
                if response.status_code == 200:
                    name = source['parser'](response.text, code)
                    if name:
                        result = {
                            'code': code,
                            'name': name,
                            'market': market,
                            'source': source['name'],
                            'type': 'etf',
                            'url': url
                        }
                        print(f"  网页查询成功: {name}")
                        return result
                
                time.sleep(0.5)
                
            except Exception as e:
                print(f"  {source['name']} 查询失败: {e}")
                continue
        
        print(f"  所有查询方式均失败")
        return None
    
    def batch_query_etfs(self, codes: List[str], delay: float = 0.5) -> List[Dict]:
        """批量查询ETF基金"""
        results = []
        
        print(f"开始批量查询 {len(codes)} 个ETF基金...")
        print(f"预计时间: {len(codes) * delay:.1f} 秒")
        
        start_time = time.time()
        
        for i, code in enumerate(codes, 1):
            print(f"[{i}/{len(codes)}] ", end='')
            
            result = self.query_etf(code)
            if result:
                results.append(result)
            else:
                results.append({
                    'code': code,
                    'name': None,
                    'market': self.get_etf_market(code),
                    'source': None,
                    'type': 'etf',
                    'error': '查询失败'
                })
            
            # 延迟
            if i < len(codes):
                time.sleep(delay)
        
        elapsed = time.time() - start_time
        success = len([r for r in results if r['name']])
        
        print(f"\n批量查询完成!")
        print(f"总耗时: {elapsed:.1f} 秒")
        print(f"成功率: {success}/{len(codes)} ({success/len(codes)*100:.1f}%)")
        
        return results
    
    def save_results(self, results: List[Dict], filename: str = "etf_results.json"):
        """保存结果到JSON文件"""
        try:
            # 只保存成功的记录
            successful_results = [r for r in results if r['name']]
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(successful_results, f, ensure_ascii=False, indent=2)
            
            print(f"成功保存 {len(successful_results)} 条记录到 {filename}")
            
            # 同时保存原始记录（包含失败）
            raw_filename = filename.replace('.json', '_raw.json')
            with open(raw_filename, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            
            print(f"原始记录保存到 {raw_filename}")
            
        except Exception as e:
            print(f"保存失败: {e}")
    
    def print_summary(self, results: List[Dict]):
        """打印查询摘要"""
        print("\n" + "=" * 70)
        print("ETF查询结果摘要")
        print("=" * 70)
        
        successful = [r for r in results if r['name']]
        failed = [r for r in results if not r['name']]
        
        print(f"总计: {len(results)}")
        print(f"成功: {len(successful)}")
        print(f"失败: {len(failed)}")
        
        if successful:
            print("\n成功查询的ETF:")
            for result in successful[:10]:  # 只显示前10个
                print(f"  {result['code']}: {result['name']}")
            
            if len(successful) > 10:
                print(f"  ... 还有 {len(successful) - 10} 个")
        
        if failed:
            print(f"\n查询失败的ETF ({len(failed)}个):")
            failed_codes = [r['code'] for r in failed]
            print(f"  {', '.join(failed_codes[:10])}")
            if len(failed) > 10:
                print(f"  ... 还有 {len(failed) - 10} 个")
        
        print("=" * 70)


def main():
    """主函数 - 专门处理ETF查询"""
    # 您的原始ETF代码列表
    etf_codes = [
        "510050", "510310", "159352", "510880", "561280",
        "159957", "159949", "159991", "159780", "159811",
        "512480", "159560", "159516", "562820", "159590",
        "562920", "159819", "159363", "159526", "159206",
        "561220", "159667", "159638",
        "516390", "159565", "159261", "560980", "159775",
        "561380", "561700", "159713", "516020", "159652",
        "512660", "515220", "588010", "159886",
        "512070", "515020", "513090", "517520",
        "516130", "512690", "560080", "159859", "159567",
        "159265", "159869", "159856",
        "159202", "159742", "159605", "159750", "159712",
        "159312", "513100", "513500", "159941", "513130",
        "513330",
        "518880", "159001", "511160", "510170", "159985",
        "159697"
    ]
    
    print("=" * 70)
    print("A股ETF基金批量查询工具")
    print("=" * 70)
    print(f"共 {len(etf_codes)} 个ETF代码需要查询")
    
    # 创建查询实例
    etf_query = ETFQuery()
    
    # 批量查询
    results = etf_query.batch_query_etfs(etf_codes, delay=0.4)
    
    # 保存结果
    etf_query.save_results(results, "etf_final_results.json")
    
    # 打印摘要
    etf_query.print_summary(results)
    
    # 生成简化的JSON输出
    simplified = []
    for result in results:
        if result['name']:
            simplified.append({
                'code': result['code'],
                'name': result['name']
            })
    
    with open('etf_simple_results.json', 'w', encoding='utf-8') as f:
        json.dump(simplified, f, ensure_ascii=False, indent=2)
    
    print(f"\n简化结果已保存到 etf_simple_results.json")
    print("=" * 70)


if __name__ == "__main__":
    main()
