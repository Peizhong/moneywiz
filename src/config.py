"""加载并校验 config/ 下的 YAML 配置。

任何问题都以 :class:`ConfigError` 抛出，错误信息包含出错文件路径与字段名。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

FUND_TYPES = {"etf", "lof", "normal"}
DEFAULT_DATA: dict[str, int] = {
    "kline_days": 120,
    "pe_cache_days": 7,
    "financial_cache_days": 30,
    "index_refresh_days": 14,
}
DEFAULT_OUTPUT: dict[str, int] = {"buy_top_n": 5, "avoid_bottom_n": 5}

FILENAMES = {
    "stocks": "stocks.yaml",
    "funds": "funds.yaml",
    "rules": "rules.yaml",
    "constituents": "dividend_index.yaml",
}


class ConfigError(Exception):
    """配置文件缺失、无法解析或未通过校验。"""


@dataclass
class StockCfg:
    code: str
    name: str


@dataclass
class FundCfg:
    code: str
    name: str
    type: str
    index: str | None = None


@dataclass
class ConstituentCfg:
    code: str
    name: str
    added: str | None = None  # 加入指数成分的日期（YYYY-MM-DD，自动刷新时写入）


@dataclass
class Config:
    stocks: list[StockCfg]
    funds: list[FundCfg]
    rules: dict
    output: dict
    index_code: str
    constituents: list[ConstituentCfg]
    constituents_updated_at: datetime


def load_config(config_dir: Path = Path("config")) -> Config:
    """读取 config_dir 下的四个 YAML（自选/基金/规则/指数成分股）并校验。"""
    config_dir = Path(config_dir)
    raw = {
        key: _load_yaml(config_dir / filename)
        for key, filename in FILENAMES.items()
    }

    stocks = _parse_stocks(config_dir / FILENAMES["stocks"], raw["stocks"])
    funds = _parse_funds(config_dir / FILENAMES["funds"], raw["funds"])
    rules, output = _parse_rules(config_dir / FILENAMES["rules"], raw["rules"])
    index_code, constituents, updated_at = _parse_constituents(
        config_dir / FILENAMES["constituents"], raw["constituents"]
    )
    return Config(
        stocks=stocks,
        funds=funds,
        rules=rules,
        output=output,
        index_code=index_code,
        constituents=constituents,
        constituents_updated_at=updated_at,
    )


def _parse_constituents(path: Path, data: Any) -> tuple[str, list[ConstituentCfg], datetime]:
    """校验 dividend_index.yaml：index_code、updated_at、constituents（code/name/可选 added）。"""
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: 顶层必须是映射，实际为 {type(data).__name__}")
    index_code = data.get("index_code")
    if not isinstance(index_code, str) or not index_code:
        raise ConfigError(f"{path}: 'index_code' 缺失或不是非空字符串")

    updated_raw = data.get("updated_at")
    try:
        updated_at = datetime.fromisoformat(str(updated_raw))
    except ValueError as exc:
        raise ConfigError(
            f"{path}: 'updated_at' 缺失或不是 ISO 时间格式（实际为 {updated_raw!r}）"
        ) from exc

    items = _list_section(path, "constituents", data)
    constituents = []
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError(f"{path}: constituents 的每项必须是映射")
        code = _required_str(path, item, "code", "成分股 code")
        name = _required_str(path, item, "name", "成分股 name")
        added = item.get("added")
        if added is not None:
            try:
                date.fromisoformat(str(added))
            except ValueError as exc:
                raise ConfigError(
                    f"{path}: 成分股 {code} 的 'added' 不是 YYYY-MM-DD 日期（{added!r}）"
                ) from exc
            added = str(added)
        constituents.append(ConstituentCfg(code=code, name=name, added=added))
    return index_code, constituents, updated_at


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"{path}: 配置文件不存在")
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: 读取或解析失败: {exc}") from exc
    if data is None:
        raise ConfigError(f"{path}: 文件内容为空")
    return data


def _list_section(path: Path, key: str, data: Any) -> list:
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: 顶层必须是映射，实际为 {type(data).__name__}")
    if key not in data:
        raise ConfigError(f"{path}: 缺少 '{key}' 段")
    items = data[key]
    if not isinstance(items, list):
        raise ConfigError(f"{path}: '{key}' 段必须是列表")
    return items


def _required_str(path: Path, item: dict, key: str, field: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{path}: '{field}' 缺失或不是非空字符串")
    return value


def _parse_stocks(path: Path, data: Any) -> list[StockCfg]:
    stocks: list[StockCfg] = []
    for i, item in enumerate(_list_section(path, "stocks", data)):
        if not isinstance(item, dict):
            raise ConfigError(f"{path}: 'stocks[{i}]' 必须是映射")
        stocks.append(
            StockCfg(
                code=_required_str(path, item, "code", f"stocks[{i}].code"),
                name=_required_str(path, item, "name", f"stocks[{i}].name"),
            )
        )
    return stocks


def _parse_funds(path: Path, data: Any) -> list[FundCfg]:
    funds: list[FundCfg] = []
    for i, item in enumerate(_list_section(path, "funds", data)):
        if not isinstance(item, dict):
            raise ConfigError(f"{path}: 'funds[{i}]' 必须是映射")
        fund_type = _required_str(path, item, "type", f"funds[{i}].type")
        if fund_type not in FUND_TYPES:
            raise ConfigError(
                f"{path}: 'funds[{i}].type' 必须是 {sorted(FUND_TYPES)} 之一，"
                f"实际为 {fund_type!r}"
            )
        funds.append(
            FundCfg(
                code=_required_str(path, item, "code", f"funds[{i}].code"),
                name=_required_str(path, item, "name", f"funds[{i}].name"),
                type=fund_type,
                index=item.get("index"),
            )
        )
    return funds


def _parse_rules(path: Path, data: Any) -> tuple[dict, dict]:
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: 顶层必须是映射，实际为 {type(data).__name__}")

    for section in ("stocks", "funds"):
        _validate_indicators(path, section, data.get(section))

    rules = dict(data)
    rules["data"] = _section_with_defaults(path, data, "data", DEFAULT_DATA)
    output = _section_with_defaults(path, data, "output", DEFAULT_OUTPUT)
    return rules, output


def _validate_indicators(path: Path, section: str, spec: Any) -> None:
    indicators = spec.get("indicators") if isinstance(spec, dict) else None
    if not isinstance(indicators, dict) or not indicators:
        raise ConfigError(f"{path}: '{section}.indicators' 段缺失或为空")

    total = 0
    for name, item in indicators.items():
        field = f"{section}.indicators.{name}"
        if not isinstance(item, dict):
            raise ConfigError(f"{path}: '{field}' 必须是映射")
        weight = item.get("weight")
        if not isinstance(weight, int) or isinstance(weight, bool):
            raise ConfigError(f"{path}: '{field}.weight' 必须是整数")
        if not isinstance(item.get("thresholds"), dict):
            raise ConfigError(f"{path}: '{field}.thresholds' 必须是映射")
        total += weight

    if total != 100:
        raise ConfigError(
            f"{path}: '{section}.indicators' 的权重总和为 {total}，必须等于 100"
        )


def _section_with_defaults(path: Path, data: dict, key: str, defaults: dict) -> dict:
    section = data.get(key)
    if section is None:
        return dict(defaults)
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: '{key}' 段必须是映射")
    return {**defaults, **section}
