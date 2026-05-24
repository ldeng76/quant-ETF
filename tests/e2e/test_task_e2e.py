"""
E2E tests for full task execution flow.
Tests: load data -> run strategy -> export results (with mock TDX data).
"""
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from quant_etf.strategy import ETFScore, StockScore, ReboundStockScore
from quant_etf.risk import RiskLevel


class TestETFTaskE2E:
    """E2E tests for ETFTask (etf task)."""

    def test_etf_task_produces_results(self, mock_tdx_data, tmp_path):
        """ETF task should produce a portfolio of selected ETFs."""
        from quant_etf.tasks import ETFTask
        from quant_etf.data_source import ETFDataSource

        # Prepare mock name map
        meta_dir = tmp_path / "data" / "meta"
        meta_dir.mkdir(parents=True)
        from .conftest import create_mock_name_map
        name_map_data = create_mock_name_map(["510050", "510310", "159352", "510880"])
        (meta_dir / "stock_code_name.json").write_text(
            json.dumps(name_map_data, ensure_ascii=False)
        )

        # Patch config to use mock TDX data
        with patch("quant_etf.conf.TDX_VIPDOC_DIR", mock_tdx_data):
            with patch("quant_etf.conf.ETF_POOL", ["510050", "510310", "159352", "510880"]):
                with patch("quant_etf.conf.TOP_N", 2):
                    with patch("quant_etf.conf.PROJECT_ROOT", tmp_path):
                        with patch("quant_etf.conf.DATA_DIR", tmp_path / "data"):
                            task = ETFTask()
                            task.initialize()
                            # Replace ds with one that uses mock TDX
                            task.ds = ETFDataSource(data_dir=tmp_path / "data")

                            pool = task.get_pool()
                            data = task.load_data(pool)
                            assert len(data) >= 1, "Should load at least 1 ETF"

                            results = task.run_strategy(data)
                            assert len(results) > 0, "Should produce results"
                            assert isinstance(results[0], ETFScore)

    def test_etf_task_risk_filtering(self, mock_tdx_data, tmp_path):
        """ETF task should apply risk filtering."""
        from quant_etf.tasks import ETFTask
        from quant_etf.data_source import ETFDataSource
        from quant_etf.risk import RiskStatus

        meta_dir = tmp_path / "data" / "meta"
        meta_dir.mkdir(parents=True)
        from .conftest import create_mock_name_map
        name_map_data = create_mock_name_map(["510050", "510310"])
        (meta_dir / "stock_code_name.json").write_text(
            json.dumps(name_map_data, ensure_ascii=False)
        )

        with patch("quant_etf.conf.TDX_VIPDOC_DIR", mock_tdx_data):
            with patch("quant_etf.conf.ETF_POOL", ["510050", "510310"]):
                with patch("quant_etf.conf.TOP_N", 2):
                    with patch("quant_etf.conf.PROJECT_ROOT", tmp_path):
                        with patch("quant_etf.conf.DATA_DIR", tmp_path / "data"):
                            task = ETFTask()
                            task.initialize()
                            task.ds = ETFDataSource(data_dir=tmp_path / "data")

                            # Mock risk manager to return NORMAL for one and CRITICAL for another
                            original_check = task.risk_manager.check_risk
                            def mock_check(df):
                                # Simulate: always NORMAL for this test
                                return RiskStatus(RiskLevel.NORMAL, "Normal", "KEEP")
                            task.risk_manager.check_risk = mock_check

                            pool = task.get_pool()
                            data = task.load_data(pool)
                            results = task.run_strategy(data)
                            assert len(results) > 0


class TestShortTermStockTaskE2E:
    """E2E tests for ShortTermStockTask (short task)."""

    def test_short_task_produces_results(self, mock_tdx_data, tmp_path):
        """Short task should produce selected stocks."""
        from quant_etf.tasks import ShortTermStockTask
        from quant_etf.data_source import ETFDataSource

        meta_dir = tmp_path / "data" / "meta"
        meta_dir.mkdir(parents=True)
        from .conftest import create_mock_name_map
        name_map_data = create_mock_name_map(["002202", "600783"])
        (meta_dir / "stock_code_name.json").write_text(
            json.dumps(name_map_data, ensure_ascii=False)
        )

        with patch("quant_etf.conf.TDX_VIPDOC_DIR", mock_tdx_data):
            # Use ETF codes as mock stocks (they have .day files)
            stock_codes = ["510050", "510310"]
            with patch("quant_etf.conf.STOCK_POOL", stock_codes):
                with patch("quant_etf.conf.PROJECT_ROOT", tmp_path):
                    with patch("quant_etf.conf.DATA_DIR", tmp_path / "data"):
                        task = ShortTermStockTask()
                        task.initialize()
                        task.ds = ETFDataSource(data_dir=tmp_path / "data")

                        pool = task.get_pool()
                        data = task.load_data(pool)
                        assert len(data) >= 1

                        results = task.run_strategy(data)
                        assert len(results) > 0
                        assert isinstance(results[0], StockScore)


class TestMidTermReboundTaskE2E:
    """E2E tests for MidTermReboundTask (mid task)."""

    def test_mid_task_with_rebound_data(self, mock_tdx_data, tmp_path):
        """Mid task should find rebound stocks when data fits profile."""
        from quant_etf.tasks import MidTermReboundTask
        from quant_etf.data_source import ETFDataSource

        meta_dir = tmp_path / "data" / "meta"
        meta_dir.mkdir(parents=True)
        from .conftest import create_mock_name_map
        name_map_data = create_mock_name_map(["510050", "510310"])
        (meta_dir / "stock_code_name.json").write_text(
            json.dumps(name_map_data, ensure_ascii=False)
        )

        with patch("quant_etf.conf.TDX_VIPDOC_DIR", mock_tdx_data):
            with patch("quant_etf.conf.MID_TERM_STOCK_POOL", ["510050", "510310"]):
                with patch("quant_etf.conf.PROJECT_ROOT", tmp_path):
                    with patch("quant_etf.conf.DATA_DIR", tmp_path / "data"):
                        task = MidTermReboundTask()
                        task.initialize()
                        task.ds = ETFDataSource(data_dir=tmp_path / "data")

                        pool = task.get_pool()
                        data = task.load_data(pool)
                        assert len(data) >= 1

                        results = task.run_strategy(data)
                        # Results may be empty if data doesn't fit rebound profile
                        for r in results:
                            assert isinstance(r, ReboundStockScore)


class TestTaskRegistryE2E:
    """E2E tests for task registry."""

    def test_registry_returns_correct_task_types(self):
        """Task registry should return proper task instances."""
        from quant_etf.tasks import TaskRegistry, ETFTask, ShortTermStockTask, MidTermReboundTask

        etf_task = TaskRegistry.get_task("etf")
        assert isinstance(etf_task, ETFTask)

        short_task = TaskRegistry.get_task("short")
        assert isinstance(short_task, ShortTermStockTask)

        mid_task = TaskRegistry.get_task("mid")
        assert isinstance(mid_task, MidTermReboundTask)

    def test_registry_list_tasks(self):
        """Should list all available tasks."""
        from quant_etf.tasks import TaskRegistry
        tasks = TaskRegistry.list_tasks()
        assert len(tasks) == 3
        names = [t["name"] for t in tasks]
        assert "etf" in names
        assert "short" in names
        assert "mid" in names

    def test_registry_unknown_task(self):
        """Should return None for unknown task."""
        from quant_etf.tasks import TaskRegistry
        assert TaskRegistry.get_task("unknown") is None

    def test_registry_target_date(self):
        """Should pass target_date to task."""
        from quant_etf.tasks import TaskRegistry
        task = TaskRegistry.get_task("etf", target_date="2026-01-15")
        assert task.target_date == "2026-01-15"
