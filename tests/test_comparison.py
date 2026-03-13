import json
from pathlib import Path

import pandas as pd

from quant_etf.comparison import ResultComparator
from quant_etf.data_source import ETFDataSource


def test_compare_preserves_leading_zeros_for_stock_codes(tmp_path, monkeypatch):
    meta_dir = tmp_path / "data" / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "stock_code_name.json").write_text(
        json.dumps(
            [
                {"code": "001400", "name": "江顺科技"},
                {"code": "600673", "name": "东阳光"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    results_dir = tmp_path / "data" / "results"
    date_prev = "2026-03-12"
    date_curr = "2026-03-13"
    (results_dir / date_prev).mkdir(parents=True, exist_ok=True)
    (results_dir / date_curr).mkdir(parents=True, exist_ok=True)

    df_prev = pd.DataFrame([{"date": date_prev, "code": "600673", "score": 0.1}])
    df_curr = pd.DataFrame([{"date": date_curr, "code": "001400", "score": 0.2}])
    df_prev.to_csv(results_dir / date_prev / "short.csv", index=False)
    df_curr.to_csv(results_dir / date_curr / "short.csv", index=False)

    monkeypatch.setattr("quant_etf.comparison.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("quant_etf.comparison.ETFDataSource", lambda: ETFDataSource(data_dir=tmp_path / "data"))

    comparator = ResultComparator()
    report = comparator.compare("short", date_curr)

    assert "  + 001400 江顺科技" in report
    assert "  + 1400" not in report
