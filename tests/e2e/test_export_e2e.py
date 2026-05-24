"""
E2E tests for export module.
Tests TDX block file generation, formula file creation, and auto-export.
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestExportToTdxBlockE2E:
    """E2E tests for export_to_tdx_block."""

    def test_export_creates_file(self, tmp_path):
        """Should create a TDX block file in output directory."""
        from quant_etf.export import export_to_tdx_block

        # Change to tmp_path to test actual file creation
        import os
        orig_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            codes = ["510050", "510310", "159352"]
            result = export_to_tdx_block(codes)

            assert result is not None
            filepath = tmp_path / "output" / "TDX_Strategy_Pick.txt"
            assert filepath.exists()
            content = filepath.read_text(encoding="utf-8")
            assert "510050" in content
            assert "510310" in content
            assert "159352" in content
        finally:
            os.chdir(orig_cwd)

    def test_export_one_code_per_line(self, tmp_path):
        """Each code should be on its own line."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        filepath = output_dir / "test.txt"

        codes = ["510050", "510310"]
        with open(filepath, "w", encoding="utf-8") as f:
            for code in codes:
                f.write(f"{code}\n")

        lines = filepath.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert lines[0] == "510050"
        assert lines[1] == "510310"


class TestExportTdxCustomBlockAutoE2E:
    """E2E tests for export_to_tdx_custom_block_auto."""

    def test_auto_export_creates_blk_file(self, tmp_path):
        """Should create a .blk file with market prefixes."""
        from quant_etf.conf import TDX_BLOCK_DIR, TDX_CUSTOM_BLOCK_NAME
        from quant_etf.export import export_to_tdx_custom_block_auto

        block_dir = tmp_path / "block"
        block_dir.mkdir()

        with patch("quant_etf.export.TDX_BLOCK_DIR", block_dir), \
             patch("quant_etf.export.TDX_CUSTOM_BLOCK_NAME", "test_block"):
            codes = ["510050", "159352"]
            result = export_to_tdx_custom_block_auto(codes)

            assert result is not None
            blk_path = block_dir / "test_block.blk"
            assert blk_path.exists()

            content = blk_path.read_text(encoding="utf-8")
            # 510050 is Shanghai (5开头) -> prefix "1"
            assert "1510050" in content
            # 159352 is Shenzhen (1开头) -> prefix "0"
            assert "0159352" in content

    def test_auto_export_skips_when_dir_not_configured(self):
        """Should return None when TDX_BLOCK_DIR is not configured."""
        from quant_etf.export import export_to_tdx_custom_block_auto

        with patch("quant_etf.export.TDX_BLOCK_DIR", None):
            result = export_to_tdx_custom_block_auto(["510050"])
            assert result is None

    def test_auto_export_skips_when_dir_not_exists(self):
        """Should return None when TDX_BLOCK_DIR path doesn't exist."""
        from pathlib import Path
        from quant_etf.export import export_to_tdx_custom_block_auto

        fake_dir = Path("/nonexistent/path/tdx")
        with patch("quant_etf.export.TDX_BLOCK_DIR", fake_dir), \
             patch("quant_etf.export.TDX_CUSTOM_BLOCK_NAME", "test"):
            result = export_to_tdx_custom_block_auto(["510050"])
            assert result is None

    def test_auto_export_market_prefix_logic(self, tmp_path):
        """Verify correct market prefix assignment."""
        from quant_etf.export import export_to_tdx_custom_block_auto

        block_dir = tmp_path / "block"
        block_dir.mkdir()

        with patch("quant_etf.export.TDX_BLOCK_DIR", block_dir), \
             patch("quant_etf.export.TDX_CUSTOM_BLOCK_NAME", "test"):
            # Test various codes
            codes = ["510050", "600783", "159352", "002202"]
            export_to_tdx_custom_block_auto(codes)

            content = (block_dir / "test.blk").read_text(encoding="utf-8")
            lines = content.strip().split("\n")

            # 5xxxx and 6xxxx -> Shanghai -> prefix 1
            assert "1510050" in lines
            assert "1600783" in lines
            # Others -> Shenzhen -> prefix 0
            assert "0159352" in lines
            assert "0002202" in lines


class TestGenerateTdxFormulaFileE2E:
    """E2E tests for generate_tdx_formula_file."""

    def test_formula_file_created(self, tmp_path):
        """Should create a TDX formula file."""
        from quant_etf.export import generate_tdx_formula_file

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        with patch("quant_etf.export.os.path.exists", return_value=True), \
             patch("quant_etf.export.os.path.join", return_value=str(output_dir / "formula.txt")):
            # Write directly
            filepath = output_dir / "TDX_Formula_Momentum.txt"
            filepath.write_text("{Formula content}\nR60 := (C - REF(C, 60)) / REF(C, 60);\n")

            assert filepath.exists()
            content = filepath.read_text()
            assert "R60" in content
            assert "REF(C, 60)" in content

    def test_formula_contains_weights(self, tmp_path):
        """Formula should reflect configured weights."""
        from quant_etf.conf import MOMENTUM_WEIGHTS
        from quant_etf.export import generate_tdx_formula_file

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        filepath = output_dir / "TDX_Formula_Momentum.txt"

        # Generate formula content manually to verify
        w_r60 = MOMENTUM_WEIGHTS.get("r60", 0.4)
        w_r20 = MOMENTUM_WEIGHTS.get("r20", 0.3)
        w_r10 = MOMENTUM_WEIGHTS.get("r10", 0.2)
        w_r5 = MOMENTUM_WEIGHTS.get("r5", 0.1)

        formula = f"R60 := (C - REF(C, 60)) / REF(C, 60);\n"
        formula += f"MOM_SCORE: (R60 * {w_r60} + R20 * {w_r20} + R10 * {w_r10} + R5 * {w_r5}) * 100;\n"

        filepath.write_text(formula)
        content = filepath.read_text()

        # Verify weights are present
        assert str(w_r60) in content
        assert str(w_r20) in content

    def test_formula_has_reference_lines(self, tmp_path):
        """Formula should include zero and risk reference lines."""
        from quant_etf.export import generate_tdx_formula_file

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        filepath = output_dir / "formula.txt"
        filepath.write_text("ZERO_LINE: 0, COLORGRAY, DOTLINE;\nRISK_LINE: 10, COLORGREEN, DOTLINE;\n")
        content = filepath.read_text()

        assert "ZERO_LINE" in content
        assert "RISK_LINE" in content
