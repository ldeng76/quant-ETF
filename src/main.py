import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from quant_etf.conf import LOG_DIR, ETF_POOL, TOP_N, STOCK_POOL, PROJECT_ROOT, MID_TERM_STOCK_POOL
from quant_etf.data_source import ETFDataSource
from quant_etf.strategy import StrategyEngine
from quant_etf.risk import RiskManager, RiskLevel
from quant_etf.export import export_to_tdx_block, generate_tdx_formula_file, export_to_tdx_custom_block_auto

def setup_logger():
    """
    配置日志
    """
    logger.remove()
    # 控制台输出
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )
    # 文件输出
    logger.add(
        LOG_DIR / "quant_etf_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="DEBUG",
        encoding="utf-8"
    )

def main():
    """
    主入口函数
    """
    setup_logger()
    logger.info("Quant ETF System Starting...")
    
    try:
        if "--pick-stocks" in sys.argv:
            ds = ETFDataSource()
            strategy = StrategyEngine()

            stock_data = {}
            for code in STOCK_POOL:
                df = ds.load_stock_data(code)
                if df.empty:
                    logger.error(f"Failed to load stock data for {code}. Exiting.")
                    return
                stock_data[code] = df

            if not stock_data:
                logger.error("No stock data available. Exiting.")
                return

            picked = strategy.rank_stocks_for_short_term(stock_data, top_n=5)
            logger.info("=" * 30)
            logger.info("TOP 5 SHORT-TERM STOCK PICKS")
            logger.info("=" * 30)

            picked_codes = []
            for i, item in enumerate(picked, start=1):
                logger.info(
                    f"Rank {i}: {item.code} | Score: {item.score:.4f} "
                    f"(R5: {item.r5:.2%}, R10: {item.r10:.2%}, R20: {item.r20:.2%}, "
                    f"VolRatio: {item.volume_ratio_1d_20d:.2f}, TrendOK: {item.trend_ok})"
                )
                picked_codes.append(item.code)

            out_dir = PROJECT_ROOT / "output"
            out_dir.mkdir(exist_ok=True)
            out_path = out_dir / f"TDX_Stock_Pick_{datetime.now().strftime('%Y%m%d_%H%M%S')}.blk"
            lines = []
            for code in picked_codes:
                prefix = "1" if str(code).startswith(("5", "6")) else "0"
                lines.append(f"{prefix}{code}\n")
            out_path.write_text("".join(lines), encoding="utf-8")
            logger.info(f"Saved TDX stock pick block file: {out_path}")
            return

        if "--pick-mid-term-stocks" in sys.argv:
            ds = ETFDataSource()
            strategy = StrategyEngine()
            stock_name_map = ds.get_stock_name_map()

            stock_data = {}
            for code in MID_TERM_STOCK_POOL:
                df = ds.load_stock_data(code)
                if df.empty:
                    logger.error(f"Failed to load stock data for {code}. Exiting.")
                    return
                stock_data[code] = df

            if not stock_data:
                logger.error("No stock data available. Exiting.")
                return

            picked = strategy.rank_stocks_for_mid_term_rebound(stock_data, top_n=15)
            logger.info("=" * 30)
            logger.info("TOP 15 MID-TERM REBOUND STOCK PICKS")
            logger.info("=" * 30)

            picked_codes = []
            for i, item in enumerate(picked, start=1):
                stock_name = stock_name_map.get(item.code, "Unknown")
                logger.info(
                    f"Rank {i}: {item.code} ({stock_name}) | Score: {item.score:.4f} "
                    f"(Drawdown120: {item.drawdown_from_120d_high:.2%}, Bounce20: {item.bounce_from_20d_low:.2%}, "
                    f"R5: {item.r5:.2%}, R10: {item.r10:.2%}, R20: {item.r20:.2%}, "
                    f"VolRatio: {item.volume_ratio_1d_20d:.2f}, Stabilized: {item.stabilization_ok}, ReboundOK: {item.rebound_ok})"
                )
                picked_codes.append(item.code)

            logger.info("=" * 30)
            logger.info("PICKED STOCK LIST (CODE + NAME)")
            logger.info("=" * 30)
            for code in picked_codes:
                logger.info(f"{code}\t{stock_name_map.get(code, 'Unknown')}")

            out_dir = PROJECT_ROOT / "output"
            out_dir.mkdir(exist_ok=True)
            out_path = out_dir / f"TDX_MidTerm_Rebound_Pick_{datetime.now().strftime('%Y%m%d_%H%M%S')}.blk"
            lines = []
            for code in picked_codes:
                prefix = "1" if str(code).startswith(("5", "6")) else "0"
                lines.append(f"{prefix}{code}\n")
            out_path.write_text("".join(lines), encoding="utf-8")
            logger.info(f"Saved TDX mid-term rebound pick block file: {out_path}")
            return

        # 1. 初始化模块
        ds = ETFDataSource()
        strategy = StrategyEngine()
        risk_manager = RiskManager()
        
        # 获取 ETF 名称映射
        etf_name_map = ds.get_etf_name_map()
        
        # 2. 更新并加载数据
        # 默认只加载缓存，若需强制更新可开启 update_all
        # ds.update_all() 
        
        etf_data = {}
        for code in ETF_POOL:
            # 尝试加载数据，如果本地没有会自动下载
            df = ds.load_data(code)
            if df.empty:
                logger.error(f"Failed to load data for {code}. Exiting.")
                return
            etf_data[code] = df
        
        if not etf_data:
            logger.error("No ETF data available. Exiting.")
            return

        # 3. 运行策略排名
        logger.info("Running strategy engine...")
        ranked_scores = strategy.rank_etfs(etf_data)
        
        logger.info("Top 10 ETFs by Momentum Score:")
        for i, item in enumerate(ranked_scores[:10]):
            etf_name = etf_name_map.get(item.code, "Unknown")
            logger.info(f"Rank {i+1}: {item.code} ({etf_name}) - Score: {item.score:.4f} (R60: {item.r60:.2%}, R20: {item.r20:.2%}, R10: {item.r10:.2%}, R5: {item.r5:.2%})")
            
        # 4. 生成目标组合
        # 假设我们持有 Top N
        target_portfolio = strategy.get_target_portfolio(ranked_scores, top_n=TOP_N)
        
        # 5. 风控检查
        logger.info("Running risk checks on target portfolio...")
        final_portfolio = {}
        
        for code, weight in target_portfolio.items():
            df = etf_data[code]
            risk_status = risk_manager.check_risk(df)
            etf_name = etf_name_map.get(code, "Unknown")
            
            if risk_status.level == RiskLevel.CRITICAL:
                logger.critical(f"RISK ALERT for {code} ({etf_name}): {risk_status.reason}. Action: {risk_status.suggested_action}")
                # 严重风险，剔除出组合或降权至0
                final_portfolio[code] = 0.0
            elif risk_status.level == RiskLevel.WARNING:
                logger.warning(f"RISK WARNING for {code} ({etf_name}): {risk_status.reason}. Action: {risk_status.suggested_action}")
                # 警告状态，可以减半持仓
                final_portfolio[code] = weight * 0.5
            else:
                logger.info(f"Risk Check {code} ({etf_name}): PASSED")
                final_portfolio[code] = weight

        # 6. 输出最终建议
        logger.info("="*30)
        logger.info("FINAL PORTFOLIO TARGETS")
        logger.info("="*30)
        
        # Collect codes for TDX export
        tdx_export_codes = []
        
        total_weight = sum(final_portfolio.values())
        if total_weight < 0.01:
            logger.warning("Empty portfolio! Market risk might be too high.")
        else:
            for code, weight in final_portfolio.items():
                if weight > 0:
                    etf_name = etf_name_map.get(code, "Unknown")
                    logger.info(f"ETF: {code} ({etf_name}) | Target Weight: {weight:.2%}")
                    tdx_export_codes.append(code)
        
        logger.info("="*30)
        
        # Export to TDX
        if tdx_export_codes:
            # 1. 导出为普通文本文件 (Output目录)
            export_path = export_to_tdx_block(tdx_export_codes)
            if export_path:
                logger.info(f"TDX Import File created: {export_path}")
            
            # 2. 自动导出到通达信自定义板块目录 (如果配置了)
            auto_export_path = export_to_tdx_custom_block_auto(tdx_export_codes)
            if auto_export_path:
                logger.info(f"Auto-exported to TDX Block: {auto_export_path}")
                
        # Generate Formula File
        formula_path = generate_tdx_formula_file()
        if formula_path:
            logger.info(f"TDX Formula File created: {formula_path}")

        logger.info("System finished successfully.")
        
    except Exception as e:
        logger.exception(f"System execution failed: {e}")

if __name__ == "__main__":
    main()
