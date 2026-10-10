import copy
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from src.config import (
    ConfigError,
    ConstituentCfg,
    HoldingCfg,
    StockCfg,
    load_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RULES = {
    "stocks": {
        "indicators": {
            "dividend_yield": {"weight": 60, "thresholds": {"high": 4.0, "mid": 2.0}},
            "dividend_years": {"weight": 40, "thresholds": {"high": 5, "mid": 3}},
        }
    },
    "data": {"kline_days": 120, "pe_cache_days": 7},
}

STOCKS = {
    "stocks": [
        {"code": "000001", "name": "平安银行"},
        {"code": "600036", "name": "招商银行"},
    ]
}

CONSTITUENTS = {
    "indices": [
        {
            "index_code": "000015",
            "index_name": "上证红利",
            "updated_at": "2026-10-06T12:00:00",
            "constituents": [
                {"code": "600015", "name": "华夏银行"},
                {"code": "601088", "name": "中国神华", "added": "2026-10-01"},
            ],
        },
        {
            "index_code": "931468",
            "index_name": "红利质量",
            "updated_at": "2026-10-05T09:00:00",
            "constituents": [{"code": "600519", "name": "贵州茅台"}],
        },
    ]
}

HOLDINGS = {
    "holdings": [{"code": "600036", "cost": 32.5}],
}


def _write_config(
    tmp_path, *, stocks=STOCKS, rules=RULES, constituents=CONSTITUENTS, holdings=HOLDINGS
):
    """把给定内容写成 config/ 下的四个 YAML 文件，传入 None 表示不写该文件。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename, data in (
        ("stocks.yaml", stocks),
        ("rules.yaml", rules),
        ("dividend_index.yaml", constituents),
        ("holdings.yaml", holdings),
    ):
        if data is not None:
            (config_dir / filename).write_text(
                yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
            )
    return config_dir


def test_load_valid_config(tmp_path):
    cfg = load_config(_write_config(tmp_path))

    assert cfg.stocks == [
        StockCfg(code="000001", name="平安银行"),
        StockCfg(code="600036", name="招商银行"),
    ]
    assert cfg.rules["stocks"]["indicators"]["dividend_yield"]["weight"] == 60
    assert cfg.rules["data"]["pe_cache_days"] == 7
    assert cfg.holdings == [HoldingCfg(code="600036", cost=32.5)]


def test_missing_code_raises_config_error(tmp_path):
    stocks = copy.deepcopy(STOCKS)
    del stocks["stocks"][0]["code"]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_config(tmp_path, stocks=stocks))

    message = str(excinfo.value)
    assert "stocks.yaml" in message
    assert "code" in message


def test_holdings_cost_must_be_positive(tmp_path):
    holdings = copy.deepcopy(HOLDINGS)
    holdings["holdings"][0]["cost"] = 0
    with pytest.raises(ConfigError) as exc_info:
        load_config(_write_config(tmp_path, holdings=holdings))
    assert "holdings.yaml" in str(exc_info.value)
    assert "cost" in str(exc_info.value)


def test_holdings_cost_rejects_bool(tmp_path):
    holdings = copy.deepcopy(HOLDINGS)
    holdings["holdings"][0]["cost"] = True  # bool 是 int 子类，必须显式拒绝
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, holdings=holdings))


def test_holdings_missing_code_raises_config_error(tmp_path):
    holdings = copy.deepcopy(HOLDINGS)
    holdings["holdings"][0]["code"] = ""
    with pytest.raises(ConfigError) as exc_info:
        load_config(_write_config(tmp_path, holdings=holdings))
    assert "holdings[0].code" in str(exc_info.value)


def test_stocks_weights_must_sum_to_100(tmp_path):
    rules = copy.deepcopy(RULES)
    rules["stocks"]["indicators"]["dividend_years"]["weight"] = 35  # 60 + 35 = 95

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_config(tmp_path, rules=rules))

    message = str(excinfo.value)
    assert "rules.yaml" in message
    assert "stocks.indicators" in message
    assert "95" in message


def test_missing_file_raises_config_error(tmp_path):
    config_dir = _write_config(tmp_path, rules=None)

    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir)

    assert "rules.yaml" in str(excinfo.value)


def test_output_defaults_when_section_absent(tmp_path):
    rules = copy.deepcopy(RULES)
    rules.pop("data", None)

    cfg = load_config(_write_config(tmp_path, rules=rules))

    assert cfg.output["buy_top_n"] == 5
    assert cfg.output["avoid_bottom_n"] == 5
    assert cfg.rules["data"] == {
        "kline_days": 120,
        "pe_cache_days": 7,
        "financial_cache_days": 30,
        "index_refresh_days": 14,
        "quote_cache_minutes": 0,
        "daily_cache_hours": 6,
        "slow_cache_days": 7,
        "candidate_top_n": 0,
    }


def test_load_config_parses_index_constituents(tmp_path):
    cfg = load_config(_write_config(tmp_path))

    assert [item.index_code for item in cfg.indices] == ["000015", "931468"]
    first, second = cfg.indices
    assert first.index_name == "上证红利"
    assert first.updated_at == datetime.fromisoformat("2026-10-06T12:00:00")
    assert first.constituents == [
        ConstituentCfg(code="600015", name="华夏银行", added=None),
        ConstituentCfg(code="601088", name="中国神华", added="2026-10-01"),
    ]
    assert second.index_name == "红利质量"
    assert second.constituents == [ConstituentCfg(code="600519", name="贵州茅台")]


def test_missing_constituents_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="dividend_index"):
        load_config(_write_config(tmp_path, constituents=None))


def test_invalid_constituents_updated_at_raises_config_error(tmp_path):
    bad = copy.deepcopy(CONSTITUENTS)
    bad["indices"][0]["updated_at"] = "not-a-date"

    with pytest.raises(ConfigError, match="updated_at"):
        load_config(_write_config(tmp_path, constituents=bad))


def test_load_real_project_config():
    # 只校验 schema 与字段形状，不断言自选列表内容——
    # README 指导用户自行维护 config/，断言内容会随预期使用而变红。
    cfg = load_config(PROJECT_ROOT / "config")

    assert cfg.rules["data"]["pe_cache_days"] == 7
    assert cfg.rules["data"]["financial_cache_days"] == 30
    assert cfg.indices and all(
        item.index_code and item.constituents for item in cfg.indices
    )
    assert all(s.code and s.name for s in cfg.stocks)
    assert isinstance(cfg.holdings, list)


def test_non_utf8_config_raises_config_error(tmp_path):
    config_dir = _write_config(tmp_path)
    (config_dir / "stocks.yaml").write_bytes(b"\xff\xfe not valid utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir)

    assert "stocks.yaml" in str(excinfo.value)
