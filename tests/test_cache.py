"""src.cache 通用取数缓存（SQLite，按 TTL 判过期）的单元测试，不联网。

分档策略在 ``src/data.py``（见其 TTL_LIVE/TTL_DAILY/TTL_SLOW），本文件只测
「按 key 存取 + 按传入 TTL 判过期」这一层。
"""

import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest

from src.cache import Cache

NOW = datetime(2026, 10, 6, 12, 0, 0)


def _cache(tmp_path):
    return Cache(tmp_path / "market_cache.db")


def test_set_and_get_roundtrip_values(tmp_path):
    cache = _cache(tmp_path)

    cache.set("num", 4.14, now=NOW)
    cache.set("mapping", {"pe": 5.2, "pb": None}, now=NOW)

    assert cache.get("num", ttl_seconds=3600, now=NOW) == pytest.approx(4.14)
    assert cache.get("mapping", ttl_seconds=3600, now=NOW) == {"pe": 5.2, "pb": None}


def test_dataframe_roundtrip_preserves_dates_and_values(tmp_path):
    cache = _cache(tmp_path)
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-29", "2026-09-30"]),
            "close": [40.51, 41.26],
        }
    )

    cache.set("kline", frame, now=NOW)
    out = cache.get("kline", ttl_seconds=86400, now=NOW)

    assert out.equals(frame)  # 数值与 dtype 都应与原帧一致
    assert list(out.columns) == ["date", "close"]
    assert pd.api.types.is_datetime64_any_dtype(out["date"])
    assert out["date"].tolist() == frame["date"].tolist()
    assert out["close"].tolist() == pytest.approx([40.51, 41.26])


def test_string_code_column_keeps_leading_zeros(tmp_path):
    cache = _cache(tmp_path)
    frame = pd.DataFrame(
        {
            "code": ["000001", "600036"],
            "name": ["平安银行", "招商银行"],
            "price": [11.57, 41.26],
        }
    )

    cache.set("spot", frame, now=NOW)
    out = cache.get("spot", ttl_seconds=86400, now=NOW)

    assert out.equals(frame)  # 字符串代码（含前导零）必须原样保留
    assert out["code"].tolist() == ["000001", "600036"]
    assert str(out["code"].dtype) in ("object", "string", "str")


def test_old_envelope_without_dtypes_still_reads(tmp_path):
    # 旧格式（无 dtypes 元数据）不应报错：正常读出，数值列按 float 还原
    cache = _cache(tmp_path)
    cache.set("warmup", 1.0, now=NOW)  # 先建表
    with sqlite3.connect(tmp_path / "market_cache.db") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, payload, updated_at) VALUES (?, ?, ?)",
            (
                "legacy",
                '{"type": "dataframe", "data": "{\\"columns\\":[\\"close\\"],'
                '\\"index\\":[0],\\"data\\":[[40.0]]}"}',
                NOW.isoformat(),
            ),
        )

    out = cache.get("legacy", ttl_seconds=86400, now=NOW)

    assert out is not None and list(out.columns) == ["close"]
    assert out["close"].tolist() == [40.0]
    assert out["close"].dtype == "float64"


def test_whole_number_floats_keep_float_dtype(tmp_path):
    # read_json 会把 40.0 推断成 int64 —— 缓存层必须还原为 float64
    cache = _cache(tmp_path)
    frame = pd.DataFrame(
        {"date": pd.to_datetime(["2026-09-30"]), "close": [40.0]}
    )

    cache.set("kline-int-like", frame, now=NOW)
    out = cache.get("kline-int-like", ttl_seconds=86400, now=NOW)

    assert out.equals(frame)
    assert out["close"].dtype == "float64"


def test_empty_frame_roundtrip_keeps_columns(tmp_path):
    cache = _cache(tmp_path)
    frame = pd.DataFrame(columns=["date", "dividend_per_share"])

    cache.set("empty", frame, now=NOW)
    out = cache.get("empty", ttl_seconds=86400, now=NOW)

    assert out is not None and out.empty
    assert list(out.columns) == ["date", "dividend_per_share"]


def test_expired_entry_is_a_miss(tmp_path):
    cache = _cache(tmp_path)
    cache.set("kline", 1.0, now=NOW - timedelta(hours=25))

    assert cache.get("kline", ttl_seconds=24 * 3600, now=NOW) is None
    # TTL 内的数据命中
    cache.set("fresh", 2.0, now=NOW - timedelta(hours=23))
    assert cache.get("fresh", ttl_seconds=24 * 3600, now=NOW) == pytest.approx(2.0)


def test_unknown_key_and_corrupt_payload_are_misses(tmp_path):
    cache = _cache(tmp_path)
    assert cache.get("missing", ttl_seconds=3600, now=NOW) is None

    # 手工写入损坏载荷 → 视为未命中而不是抛错
    cache.set("bad", 1.0, now=NOW)
    with sqlite3.connect(tmp_path / "market_cache.db") as conn:
        conn.execute("UPDATE cache SET payload = '{not json' WHERE key = 'bad'")
    assert cache.get("bad", ttl_seconds=3600, now=NOW) is None


def test_corrupt_updated_at_is_a_miss(tmp_path):
    cache = _cache(tmp_path)
    cache.set("bad-time", 1.0, now=NOW)
    with sqlite3.connect(tmp_path / "market_cache.db") as conn:
        conn.execute("UPDATE cache SET updated_at = 'not-a-time' WHERE key = 'bad-time'")
    assert cache.get("bad-time", ttl_seconds=3600, now=NOW) is None


def test_db_errors_do_not_raise(tmp_path):
    # 路径指向一个目录 → sqlite 无法打开：读写都必须安静降级
    cache = Cache(tmp_path)

    assert cache.get("any", ttl_seconds=3600) is None
    cache.set("any", 1.0)  # 不抛异常即可
