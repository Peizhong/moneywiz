import copy
from pathlib import Path

import pytest
import yaml

from src.config import ConfigError, FundCfg, StockCfg, load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RULES = {
    "stocks": {
        "indicators": {
            "dividend_yield": {"weight": 60, "thresholds": {"high": 4.0, "mid": 2.0}},
            "dividend_years": {"weight": 40, "thresholds": {"high": 5, "mid": 3}},
        }
    },
    "funds": {
        "indicators": {
            "discount_rate": {
                "weight": 100,
                "thresholds": {"discount": -1, "premium": 1},
            }
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

FUNDS = {
    "funds": [
        {"code": "510880", "name": "红利ETF", "type": "etf", "index": "上证红利"},
    ]
}


def _write_config(tmp_path, *, stocks=STOCKS, funds=FUNDS, rules=RULES):
    """把给定内容写成 config/ 下的三个 YAML 文件，传入 None 表示不写该文件。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename, data in (
        ("stocks.yaml", stocks),
        ("funds.yaml", funds),
        ("rules.yaml", rules),
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
    assert cfg.funds == [
        FundCfg(code="510880", name="红利ETF", type="etf", index="上证红利")
    ]
    assert cfg.rules["stocks"]["indicators"]["dividend_yield"]["weight"] == 60
    assert cfg.rules["data"]["pe_cache_days"] == 7


def test_missing_code_raises_config_error(tmp_path):
    stocks = copy.deepcopy(STOCKS)
    del stocks["stocks"][0]["code"]

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_config(tmp_path, stocks=stocks))

    message = str(excinfo.value)
    assert "stocks.yaml" in message
    assert "code" in message


def test_invalid_fund_type_raises_config_error(tmp_path):
    funds = copy.deepcopy(FUNDS)
    funds["funds"][0]["type"] = "abc"

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_config(tmp_path, funds=funds))

    message = str(excinfo.value)
    assert "funds.yaml" in message
    assert "type" in message


def test_stocks_weights_must_sum_to_100(tmp_path):
    rules = copy.deepcopy(RULES)
    rules["stocks"]["indicators"]["dividend_years"]["weight"] = 35  # 60 + 35 = 95

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_config(tmp_path, rules=rules))

    message = str(excinfo.value)
    assert "rules.yaml" in message
    assert "stocks.indicators" in message
    assert "95" in message


def test_funds_weights_must_sum_to_100(tmp_path):
    rules = copy.deepcopy(RULES)
    rules["funds"]["indicators"]["discount_rate"]["weight"] = 90

    with pytest.raises(ConfigError) as excinfo:
        load_config(_write_config(tmp_path, rules=rules))

    message = str(excinfo.value)
    assert "rules.yaml" in message
    assert "funds.indicators" in message


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
    assert cfg.rules["data"] == {"kline_days": 120, "pe_cache_days": 7}


def test_fund_index_defaults_to_none(tmp_path):
    funds = copy.deepcopy(FUNDS)
    del funds["funds"][0]["index"]

    cfg = load_config(_write_config(tmp_path, funds=funds))

    assert cfg.funds[0].index is None


def test_load_real_project_config():
    cfg = load_config(PROJECT_ROOT / "config")

    assert cfg.rules["data"]["pe_cache_days"] == 7
    assert [s.code for s in cfg.stocks] == ["000001", "600036"]
    assert cfg.funds[0].code == "510880"
    assert cfg.output["avoid_bottom_n"] == 5
