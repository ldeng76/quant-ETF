import os
from typing import List, Dict
from loguru import logger

def export_to_tdx_block(codes: List[str], filename: str = "TDX_Strategy_Pick.txt"):
    """
    将 ETF 代码列表导出为通达信可导入的板块文件（文本格式）。
    通达信导入格式：每行一个代码。为了兼容性，可以尝试添加市场前缀。
    但通常通达信导入文本文件时，纯代码即可识别。
    
    Args:
        codes: ETF 代码列表 (e.g. ["159915", "510300"])
        filename: 输出文件名
    """
    try:
        # 确保输出目录存在
        output_dir = "output"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            for code in codes:
                # 简单的纯代码格式，通达信导入自定义板块时通常能自动识别
                f.write(f"{code}\n")
                
        logger.info(f"Successfully exported {len(codes)} ETFs to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to export to TDX block file: {e}")
        return None

from quant_etf.conf import MOMENTUM_WEIGHTS, TDX_BLOCK_DIR, TDX_CUSTOM_BLOCK_NAME

def export_to_tdx_custom_block_auto(codes: List[str]):
    """
    自动将代码导出到通达信自定义板块目录。
    需要用户在 conf.py 中配置 TDX_BLOCK_DIR。
    
    生成的文件格式为 .blk，内容为带市场前缀的股票代码：
    1510050
    0159915
    """
    if not TDX_BLOCK_DIR:
        logger.warning("TDX_BLOCK_DIR not configured in conf.py. Skipping auto export to TDX.")
        return None

    # 检查目录是否存在
    if not os.path.exists(TDX_BLOCK_DIR):
        logger.info(f"TDX_BLOCK_DIR path does not exist: {TDX_BLOCK_DIR}. Skipping auto export.")
        return None
        
    filename = f"{TDX_CUSTOM_BLOCK_NAME}.blk"
    filepath = os.path.join(TDX_BLOCK_DIR, filename)
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            for code in codes:
                # 添加市场前缀
                # 沪市 ETF (5开头) -> 前缀 1
                # 深市 ETF (1开头) -> 前缀 0
                # 这里简单判断：如果是5或6开头则是沪市，否则默认为深市（包括1开头）
                if code.startswith("5") or code.startswith("6"):
                    prefix = "1"
                else:
                    prefix = "0"
                f.write(f"{prefix}{code}\n")
        
        logger.info(f"Successfully auto-exported {len(codes)} ETFs to TDX block file: {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to auto export to TDX block file: {e}")
        return None

def generate_tdx_formula_file():
    """
    生成通达信副图指标公式的文本文件，方便用户复制。
    """
    w_p60 = MOMENTUM_WEIGHTS.get("p60", 0.4)
    w_p20 = MOMENTUM_WEIGHTS.get("p20", 0.3)
    w_p10 = MOMENTUM_WEIGHTS.get("p10", 0.2)
    w_p5 = MOMENTUM_WEIGHTS.get("p5", 0.1)

    formula_content = f"""
{{Quant-ETF 动量综合评分指标}}
{{参数设置: 无}}

{{计算各周期涨幅}}
P60 := (C - REF(C, 60)) / REF(C, 60);
P20 := (C - REF(C, 20)) / REF(C, 20);
P10 := (C - REF(C, 10)) / REF(C, 10);
P5  := (C - REF(C, 5)) / REF(C, 5);

{{计算加权得分}}
{{权重: P60({w_p60}), P20({w_p20}), P10({w_p10}), P5({w_p5})}}
MOM_SCORE: (P60 * {w_p60} + P20 * {w_p20} + P10 * {w_p10} + P5 * {w_p5}) * 100, COLORRED, LINETHICK2;

{{绘制参考线}}
ZERO_LINE: 0, COLORGRAY, DOTLINE;
RISK_LINE: 10, COLORGREEN, DOTLINE; {{示例：得分超过10%为强势区}}
"""
    try:
        output_dir = "output"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        filepath = os.path.join(output_dir, "TDX_Formula_Momentum.txt")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(formula_content)
            
        logger.info(f"Generated TDX formula file at {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to generate TDX formula file: {e}")
        return None
