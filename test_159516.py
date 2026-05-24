import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from quant_etf.data_source import ETFDataSource
from quant_etf.strategy import StrategyEngine

ds = ETFDataSource()
df = ds.load_data('159516', check_freshness=False)

engine = StrategyEngine()
returns = engine.calculate_returns(df)

print(f'r60 = {returns["r60"]:.4f}')
print(f'r20 = {returns["r20"]:.4f}')
print(f'r10 = {returns["r10"]:.4f}')
print(f'r5  = {returns["r5"]:.4f}')
