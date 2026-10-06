"""src.data 行业 PE/PB 查询与 7 天缓存单元测试。

全程 monkeypatch akshare 函数，不联网；缓存目录用 pytest ``tmp_path``。
"""

import json
import logging
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
import pytest

from src.data import get_industry_pe_pb

CODE = "600036"
INDUSTRY = "银行"


def _info_frame(industry=INDUSTRY):
    """stock_individual_info_em() 原始 item/value 帧；industry 可替换为坏值。"""
    return pd.DataFrame(
        {
            "item": ["股票代码", "行业", "上市时间"],
            "value": ["600036", industry, "2002-04-09"],
        }
    )


def _cons_frame(pe, pb):
    """stock_board_industry_cons_em() 原始帧（4 行；只保留被测两列）。"""
    return pd.DataFrame(
        {
            "代码": ["600001", "600002", "600003", "600004"],
            "名称": ["甲", "乙", "丙", "丁"],
            "市盈率-动态": pe,
            "市净率": pb,
        }
    )


def _cons_frame_default():
    """5/7/"-"/-3 与 0.6/0.8/None/-1 → pe 6.0、pb 0.7（剔除坏值后中位数）。"""
    return _cons_frame([5.0, 7.0, "-", -3.0], [0.6, 0.8, None, -1.0])


def _patch_ak(
    monkeypatch, *, info_frame=None, cons_frame=None, info_exc=None, cons_exc=None
):
    """替换两个 akshare 函数为可控假实现。

    返回调用记录 ``{"info": [...], "cons": [...]}``（元素为关键字参数 dict）。
    """
    calls = {"info": [], "cons": []}

    def fake_info(**kwargs):
        calls["info"].append(kwargs)
        if info_exc is not None:
            raise info_exc
        return _info_frame() if info_frame is None else info_frame

    def fake_cons(**kwargs):
        calls["cons"].append(kwargs)
        if cons_exc is not None:
            raise cons_exc
        return _cons_frame_default() if cons_frame is None else cons_frame

    # 仿冒真名，警告日志里应出现 akshare 原始函数名
    fake_info.__name__ = "stock_individual_info_em"
    fake_cons.__name__ = "stock_board_industry_cons_em"
    monkeypatch.setattr(ak, "stock_individual_info_em", fake_info)
    monkeypatch.setattr(ak, "stock_board_industry_cons_em", fake_cons)
    return calls


def _stamp(age_days):
    return (datetime.now() - timedelta(days=age_days)).isoformat()


def _seed_cache(cache_dir, *, stock_age_days=0, industry_age_days=0):
    """手写合法缓存，两个时间戳分别回拨指定天数。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "pe_cache.json").write_text(
        json.dumps(
            {
                "stocks": {
                    CODE: {"industry": INDUSTRY, "updated_at": _stamp(stock_age_days)}
                },
                "industries": {
                    INDUSTRY: {
                        "pe": 6.0,
                        "pb": 0.7,
                        "updated_at": _stamp(industry_age_days),
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _load_cache(cache_dir):
    return json.loads((cache_dir / "pe_cache.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 中位数与首次缓存
# ---------------------------------------------------------------------------


def test_first_call_returns_medians_and_writes_cache(monkeypatch, tmp_path):
    cache_dir = tmp_path / "nested" / "cache"  # 目录不存在，应自动创建
    calls = _patch_ak(monkeypatch)

    out = get_industry_pe_pb(CODE, cache_dir)

    assert out["industry"] == INDUSTRY
    assert out["pe"] == pytest.approx(6.0)  # [5, 7] 的中位数，"-"/负值剔除
    assert out["pb"] == pytest.approx(0.7)  # [0.6, 0.8]
    assert calls["info"] == [{"symbol": CODE}]
    assert calls["cons"] == [{"symbol": INDUSTRY}]

    cached = _load_cache(cache_dir)
    assert cached["stocks"][CODE]["industry"] == INDUSTRY
    assert datetime.fromisoformat(cached["stocks"][CODE]["updated_at"])
    assert cached["industries"][INDUSTRY]["pe"] == pytest.approx(6.0)
    assert cached["industries"][INDUSTRY]["pb"] == pytest.approx(0.7)
    assert datetime.fromisoformat(cached["industries"][INDUSTRY]["updated_at"])


def test_second_call_hits_cache_without_akshare(monkeypatch, tmp_path):
    calls = _patch_ak(monkeypatch)

    first = get_industry_pe_pb(CODE, tmp_path)
    second = get_industry_pe_pb(CODE, tmp_path)

    assert second == first
    assert len(calls["info"]) == 1  # 缓存命中，不再请求
    assert len(calls["cons"]) == 1


def test_all_invalid_values_yield_none_fields_but_keep_industry(monkeypatch, tmp_path):
    # 市盈率全为 "-"/负值/缺失 → None；市净率 [0.5, 0.9] → 0.7
    cons = _cons_frame(["-", -3.0, None, float("nan")], [0.5, 0.9, None, "-"])
    _patch_ak(monkeypatch, cons_frame=cons)

    out = get_industry_pe_pb(CODE, tmp_path)

    assert out["industry"] == INDUSTRY
    assert out["pe"] is None
    assert out["pb"] == pytest.approx(0.7)


def test_cons_frame_without_valuation_columns_yields_none_fields(
    monkeypatch, tmp_path
):
    cons = pd.DataFrame({"代码": ["600001"], "名称": ["甲"]})
    _patch_ak(monkeypatch, cons_frame=cons)

    out = get_industry_pe_pb(CODE, tmp_path)

    assert out == {"industry": INDUSTRY, "pe": None, "pb": None}


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


def test_stale_stock_mapping_refetches_info(monkeypatch, tmp_path):
    _seed_cache(tmp_path, stock_age_days=8, industry_age_days=0)
    calls = _patch_ak(monkeypatch)

    out = get_industry_pe_pb(CODE, tmp_path)

    assert out["pe"] == pytest.approx(6.0)
    assert len(calls["info"]) == 1  # 8 天前 → 重新拉取
    assert calls["cons"] == []  # 行业中位数仍在 TTL 内
    age = datetime.now() - datetime.fromisoformat(
        _load_cache(tmp_path)["stocks"][CODE]["updated_at"]
    )
    assert age < timedelta(days=1)  # 时间戳已刷新


def test_stale_industry_medians_refetch_cons(monkeypatch, tmp_path):
    _seed_cache(tmp_path, stock_age_days=0, industry_age_days=8)
    calls = _patch_ak(monkeypatch)

    out = get_industry_pe_pb(CODE, tmp_path)

    assert out["pe"] == pytest.approx(6.0)
    assert calls["info"] == []  # 个股→行业映射仍新鲜
    assert calls["cons"] == [{"symbol": INDUSTRY}]  # 8 天前 → 重算中位数
    age = datetime.now() - datetime.fromisoformat(
        _load_cache(tmp_path)["industries"][INDUSTRY]["updated_at"]
    )
    assert age < timedelta(days=1)


def test_cache_days_parameter_extends_ttl(monkeypatch, tmp_path):
    _seed_cache(tmp_path, stock_age_days=8, industry_age_days=8)
    calls = _patch_ak(monkeypatch)

    out = get_industry_pe_pb(CODE, tmp_path, cache_days=30)

    assert out["industry"] == INDUSTRY
    assert out["pe"] == pytest.approx(6.0)
    assert out["pb"] == pytest.approx(0.7)
    assert calls == {"info": [], "cons": []}  # 8 天 < 30 天，全部命中


# ---------------------------------------------------------------------------
# 损坏 / 形状错误的缓存
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",  # 非法 JSON
        '{"stocks": "bad", "industries": {}}',  # stocks 非映射
        '{"stocks": {}, "industries": []}',  # industries 非映射
        '["stocks", "industries"]',  # 顶层非映射
        '{"stocks": {"600036": "bad"}, "industries": {}}',  # 条目非映射
        '{"stocks": {}}',  # 缺少 industries 键
        '{"stocks": {"600036": {"industry": "银行"}}, "industries": {}}',  # 无时间戳
        (  # 时间戳非法
            '{"stocks": {"600036": {"industry": "银行", '
            '"updated_at": "not-a-date"}}, "industries": {}}'
        ),
        (  # 行业名非字符串
            '{"stocks": {"600036": {"industry": null, '
            '"updated_at": "2999-01-01T00:00:00"}}, "industries": {}}'
        ),
    ],
    ids=[
        "invalid-json",
        "stocks-not-dict",
        "industries-not-dict",
        "top-not-dict",
        "entry-not-dict",
        "missing-industries-key",
        "missing-updated-at",
        "bad-updated-at",
        "non-string-industry",
    ],
)
def test_corrupt_or_wrong_shape_cache_is_rebuilt(monkeypatch, tmp_path, payload):
    (tmp_path / "pe_cache.json").write_text(payload, encoding="utf-8")
    calls = _patch_ak(monkeypatch)

    out = get_industry_pe_pb(CODE, tmp_path)

    assert out["industry"] == INDUSTRY
    assert out["pe"] == pytest.approx(6.0)
    assert out["pb"] == pytest.approx(0.7)
    assert len(calls["info"]) == 1  # 视为空缓存，正常重建
    assert len(calls["cons"]) == 1
    assert _load_cache(tmp_path)["industries"][INDUSTRY]["pe"] == pytest.approx(6.0)


def test_non_numeric_industry_medians_are_rebuilt(monkeypatch, tmp_path):
    (tmp_path / "pe_cache.json").write_text(
        json.dumps(
            {
                "stocks": {},
                "industries": {
                    INDUSTRY: {"pe": "坏值", "pb": 0.7, "updated_at": _stamp(0)}
                },
            }
        ),
        encoding="utf-8",
    )
    calls = _patch_ak(monkeypatch)

    out = get_industry_pe_pb(CODE, tmp_path)

    assert out["pe"] == pytest.approx(6.0)
    assert len(calls["cons"]) == 1  # 坏条目视为未缓存


# ---------------------------------------------------------------------------
# 失败降级
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failing", "info_calls", "cons_calls", "ak_name"),
    [
        ("info", 2, 0, "stock_individual_info_em"),
        ("cons", 1, 2, "stock_board_industry_cons_em"),
    ],
)
def test_fetch_failure_returns_none_after_retry_and_logs(
    monkeypatch, tmp_path, caplog, failing, info_calls, cons_calls, ak_name
):
    error = ConnectionError("网络超时")
    calls = _patch_ak(
        monkeypatch,
        info_exc=error if failing == "info" else None,
        cons_exc=error if failing == "cons" else None,
    )

    with caplog.at_level(logging.WARNING):
        out = get_industry_pe_pb(CODE, tmp_path)

    assert out is None
    assert len(calls["info"]) == info_calls  # _call 重试 1 次
    assert len(calls["cons"]) == cons_calls
    assert ak_name in caplog.text  # warning 含 akshare 函数名
    assert not (tmp_path / "pe_cache.json").exists()  # 失败不落盘


@pytest.mark.parametrize(
    "info_frame",
    [
        pd.DataFrame(
            {"item": ["股票代码", "上市时间"], "value": ["600036", "2002-04-09"]}
        ),
        pd.DataFrame({"item": ["股票代码", "行业"], "value": ["600036", float("nan")]}),
        pd.DataFrame(columns=["item", "value"]),
        pd.DataFrame({"foo": [1]}),
    ],
    ids=["no-industry-item", "nan-industry", "empty-frame", "no-item-column"],
)
def test_missing_industry_returns_none(monkeypatch, tmp_path, info_frame):
    calls = _patch_ak(monkeypatch, info_frame=info_frame)

    out = get_industry_pe_pb(CODE, tmp_path)

    assert out is None
    assert len(calls["info"]) == 1
    assert calls["cons"] == []  # 行业未知，不再请求成分股
    assert not (tmp_path / "pe_cache.json").exists()
