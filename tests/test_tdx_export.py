import os
import pytest
from unittest.mock import patch
from quant_etf.export import export_to_tdx_custom_block_auto

@pytest.fixture
def temp_tdx_dir(tmp_path):
    """创建临时通达信目录结构"""
    tdx_dir = tmp_path / "T0002" / "blocknew"
    tdx_dir.mkdir(parents=True)
    return tdx_dir

def test_export_to_tdx_custom_block_auto(temp_tdx_dir):
    # 准备测试数据
    codes = ["510050", "159915", "600000", "000001"]
    
    # Mock conf.TDX_BLOCK_DIR 和 conf.TDX_CUSTOM_BLOCK_NAME
    # 注意：由于 export.py 使用 from ... import ...，我们需要 patch export 模块中的变量
    with patch("quant_etf.export.TDX_BLOCK_DIR", str(temp_tdx_dir)), \
         patch("quant_etf.export.TDX_CUSTOM_BLOCK_NAME", "TestBlock"):
         
        # 执行导出
        result_path = export_to_tdx_custom_block_auto(codes)
        
        # 验证结果路径
        assert result_path is not None
        assert os.path.exists(result_path)
        assert os.path.basename(result_path) == "TestBlock.blk"
        
        # 验证文件内容
        with open(result_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        assert len(lines) == 4
        assert lines[0].strip() == "1510050"  # 5开头 -> 1
        assert lines[1].strip() == "0159915"  # 1开头 -> 0
        assert lines[2].strip() == "1600000"  # 6开头 -> 1
        assert lines[3].strip() == "0000001"  # 0开头 -> 0

def test_export_skip_when_dir_not_exists():
    # 指向一个不存在的目录
    non_existent_dir = "/path/to/non/existent"
    
    with patch("quant_etf.export.TDX_BLOCK_DIR", non_existent_dir):
        result = export_to_tdx_custom_block_auto(["510050"])
        assert result is None
        
def test_export_skip_when_not_configured():
    # 配置为空
    with patch("quant_etf.export.TDX_BLOCK_DIR", ""):
        result = export_to_tdx_custom_block_auto(["510050"])
        assert result is None
