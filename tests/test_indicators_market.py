"""src.indicators 市场指标 calc_index_pe_position（指数 PE 历史分位）的单元测试。

全部使用本地构造的帧，不联网、不读文件。"""

import pandas as pd
import pytest

from src.indicators import calc_index_pe_position


def _pe_frame(values, column="pe"):
    """按 PE 序列构造指数 PE 帧（日期升序）。"""
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(values), freq="D"),
            column: [float(value) for value in values],
        }
    )


# ---------------------------------------------------------------------------
# calc_index_pe_position（指数 PE 历史分位）
# ---------------------------------------------------------------------------


def test_index_pe_position_is_percentile_of_history_range():
    # 历史区间 [10, 40]、最新 30 → (30 - 10) / (40 - 10) × 100 = 66.66...
    frame = _pe_frame([10.0, 20.0, 40.0, 30.0])

    assert calc_index_pe_position(frame) == pytest.approx(66.67, rel=1e-3)


def test_index_pe_position_uses_named_column():
    frame = _pe_frame([10.0, 30.0], column="pe_ttm")

    assert calc_index_pe_position(frame, column="pe_ttm") == pytest.approx(100.0)


def test_index_pe_position_insufficient_rows_returns_none():
    assert calc_index_pe_position(_pe_frame([30.0])) is None


def test_index_pe_position_flat_history_returns_none():
    assert calc_index_pe_position(_pe_frame([10.0, 10.0])) is None


def test_index_pe_position_none_returns_none():
    assert calc_index_pe_position(None) is None
