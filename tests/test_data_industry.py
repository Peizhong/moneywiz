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

    # 新浪行业回退默认不可用：失败路径不得触发真实的 84 行业扫描；
    # 专门测试 B 计划时再自行覆盖 stock_sector_spot / stock_sector_detail
    def fake_sector_spot(**kwargs):
        raise ConnectionError("新浪行业未打桩（测试防真实网络）")

    fake_sector_spot.__name__ = "stock_sector_spot"
    monkeypatch.setattr(ak, "stock_sector_spot", fake_sector_spot)
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


# ---------------------------------------------------------------------------
# B 计划：新浪行业回退（东财不可用时）
# ---------------------------------------------------------------------------


def _sina_sectors_frame(indicator):
    """两套分类的板块列表：证监会行业（第一套）与新浪行业（第二套）。

    两套都不全：第一套覆盖面广但漏掉部分个股（实测含伊利股份），第二套补漏。
    """
    if indicator == "新浪行业":
        return pd.DataFrame(
            {
                "label": ["new_sphy", "new_swzz"],
                "板块": ["食品行业", "生物制药"],
                "公司家数": [58, 155],
            }
        )
    return pd.DataFrame(
        {
            "label": ["hangye_ZA01", "hangye_ZC27"],
            "板块": ["农业", "医药制造业"],
            "公司家数": [15, 100],
        }
    )


def _sina_detail_frame(entries):
    """sina stock_sector_detail 原始帧（只保留被测列）：entries 为 (code, per, pb)。"""
    return pd.DataFrame(
        {
            "code": [code for code, _, _ in entries],
            "name": [f"股票{code}" for code, _, _ in entries],
            "per": [per for _, per, _ in entries],
            "pb": [pb for _, _, pb in entries],
        }
    )


def _patch_sina(monkeypatch):
    calls = {"sectors": [], "detail": []}

    def fake_sectors(indicator):
        calls["sectors"].append(indicator)
        return _sina_sectors_frame(indicator)

    def fake_detail(sector):
        calls["detail"].append(sector)
        return {
            "hangye_ZA01": _sina_detail_frame(
                [("600108", 30.0, 1.5), ("600109", 10.0, 0.5), ("600110", -5.0, None)]
            ),
            "hangye_ZC27": _sina_detail_frame([("000001", 5.0, 0.6)]),
            "new_sphy": _sina_detail_frame([("600887", 12.0, 2.0), ("600873", 8.0, 1.0)]),
            # 600108 两套都有：第一套（农业）必须胜出，改动不得影响已能解析的个股
            "new_swzz": _sina_detail_frame([("600108", 99.0, 9.0)]),
        }[sector]

    monkeypatch.setattr(ak, "stock_sector_spot", fake_sectors)
    monkeypatch.setattr(ak, "stock_sector_detail", fake_detail)
    return calls


def test_sina_fallback_notice_logged_once_across_codes(monkeypatch, tmp_path, caplog):
    """东财失败回退新浪的提示每次运行只打一行，不再逐只出现。"""
    _patch_ak(monkeypatch, info_exc=ConnectionError("东财不可用"))
    _patch_sina(monkeypatch)

    with caplog.at_level(logging.INFO, logger="src.data"):
        out_a = get_industry_pe_pb("600108", tmp_path)  # 首只：东财失败 → warning
        out_b = get_industry_pe_pb("000001", tmp_path)  # 已熔断：info 只此一行
        out_c = get_industry_pe_pb("600109", tmp_path)  # 同运行内第三只：静默

    assert out_a is not None and out_b is not None and out_c is not None
    notices = [m for m in caplog.messages if "回退新浪行业" in m]
    assert len(notices) == 2  # warning（首只失败）+ info（首次熔断路径），各一条
    assert "600108" in notices[0]
    assert "000001" in notices[1]
    assert "600109" not in caplog.text


def test_sina_fallback_builds_table_and_returns_medians(monkeypatch, tmp_path):
    _patch_ak(monkeypatch, info_exc=ConnectionError("东财不可用"))
    calls = _patch_sina(monkeypatch)

    out = get_industry_pe_pb("600108", tmp_path)

    # 农业：PE 中位数 median(30, 10)（-5 被剔除）= 20；PB median(1.5, 0.5) = 1.0
    assert out == {"industry": "农业", "pe": 20.0, "pb": 1.0}
    # 两套分类各扫一遍板块列表（84 + 49 个板块的缩影）
    assert calls["sectors"] == ["行业", "新浪行业"]
    assert len(calls["detail"]) == 4
    assert (tmp_path / "sina_industry.json").exists()


def test_sina_table_is_cached_across_calls(monkeypatch, tmp_path):
    _patch_ak(monkeypatch, info_exc=ConnectionError("东财不可用"))
    calls = _patch_sina(monkeypatch)
    get_industry_pe_pb("600108", tmp_path)  # 首次构建

    def sector_boom(**kwargs):
        raise AssertionError("缓存命中后不应再扫描行业板块")

    monkeypatch.setattr(ak, "stock_sector_spot", sector_boom)
    out = get_industry_pe_pb("000001", tmp_path)

    assert out == {"industry": "医药制造业", "pe": 5.0, "pb": 0.6}
    assert calls["sectors"] == ["行业", "新浪行业"]  # 仍然只扫过一遍


def test_sina_fallback_unavailable_returns_none(monkeypatch, tmp_path):
    _patch_ak(monkeypatch, info_exc=ConnectionError("东财不可用"))
    # _patch_ak 已把 stock_sector_spot 打桩为失败 → 新浪也不可用

    assert get_industry_pe_pb("600108", tmp_path) is None


def test_sina_second_taxonomy_covers_codes_missing_from_first(monkeypatch, tmp_path):
    """证监会行业表查不到的代码（实测含 600887 伊利股份）回落到新浪行业表。"""
    _patch_ak(monkeypatch, info_exc=ConnectionError("东财不可用"))
    _patch_sina(monkeypatch)

    out = get_industry_pe_pb("600887", tmp_path)

    # 食品行业：PE median(12, 8) = 10；PB median(2, 1) = 1.5
    assert out == {"industry": "食品行业", "pe": 10.0, "pb": 1.5}


def test_sina_cache_without_schema_is_rebuilt(monkeypatch, tmp_path):
    """改动前写入的旧表没有第二套分类 → 无 schema 键的缓存必须重建。"""
    _patch_ak(monkeypatch, info_exc=ConnectionError("东财不可用"))
    calls = _patch_sina(monkeypatch)
    (tmp_path / "sina_industry.json").write_text(
        json.dumps(
            {
                "updated_at": datetime.now().isoformat(),
                "stocks": {"600108": "农业"},
                "medians": {"农业": {"pe": 20.0, "pb": 1.0}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    out = get_industry_pe_pb("600887", tmp_path)

    assert out == {"industry": "食品行业", "pe": 10.0, "pb": 1.5}
    assert calls["sectors"] == ["行业", "新浪行业"]  # 旧表被丢弃，两套都重扫
