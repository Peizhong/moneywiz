"""数据层：唯一允许调用 akshare 的模块。

对外只暴露归一化后的规范输出（下游 indicators/scorer 依赖）：

- ``get_stock_spot()`` → 列 ``code, name, price, pe, pb``
  （str, str, float, float, float；缺失值为 NaN，由下游组装 values 时转 None）。
- ``get_kline(code, days=120, as_of=None)`` → 列 ``date, close``
  （datetime64，升序，float；已剔除收盘价 NaN 的行；最多 days 行）。
- ``get_dividend_history(code)`` → 列 ``date, dividend_per_share``
  （datetime64 升序，税前每股分红 float，源数据元/10 股已换算）。
- ``get_industry_pe_pb(code, cache_dir, cache_days=7)`` →
  ``{"industry": str, "pe": float | None, "pb": float | None}``
  （行业成分股 PE/PB 中位数，结果缓存在 ``cache_dir/pe_cache.json``）。

失败契约：任一次获取失败都记 warning（含 akshare 函数名）并重试 1 次，
仍失败返回 ``None``；成功但无数据返回**带规范列的空帧**（绝不返回裸空帧）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

SPOT_COLUMNS = ("code", "name", "price", "pe", "pb")
KLINE_COLUMNS = ("date", "close")
DIVIDEND_COLUMNS = ("date", "dividend_per_share")
PE_CACHE_FILENAME = "pe_cache.json"


def _call(ak_fn, *args, **kwargs):
    """调用 akshare 函数：异常 → warning（含函数名）+ 重试 1 次；仍失败返回 None。"""
    name = getattr(ak_fn, "__name__", repr(ak_fn))
    for attempt in (1, 2):
        try:
            return ak_fn(*args, **kwargs)
        except Exception as exc:  # 网络/解析等上游异常一律降级为 None
            logger.warning(
                "akshare %s 获取失败（第 %d 次尝试）：%s: %s",
                name,
                attempt,
                type(exc).__name__,
                exc,
            )
    return None


def get_stock_spot() -> pd.DataFrame | None:
    """全市场 A 股实时行情，归一为 ``code, name, price, pe, pb``；失败 → None。"""
    raw = _call(ak.stock_zh_a_spot_em)
    if raw is None:
        return None
    if raw.empty:
        return pd.DataFrame(columns=list(SPOT_COLUMNS))

    return pd.DataFrame(
        {
            "code": raw["代码"].astype(str),
            "name": raw["名称"].astype(str),
            "price": pd.to_numeric(raw["最新价"], errors="coerce"),
            "pe": pd.to_numeric(raw["市盈率-动态"], errors="coerce"),
            "pb": pd.to_numeric(raw["市净率"], errors="coerce"),
        }
    ).reset_index(drop=True)


def get_kline(
    code: str, days: int = 120, as_of: date | None = None
) -> pd.DataFrame | None:
    """单只日 K 线（前复权），归一为 ``date, close``；失败 → None。

    请求区间为 ``as_of - 2×days`` 自然日至 ``as_of``（默认今天），
    取最后 days 行并按日期升序，剔除收盘价为 NaN 的行。
    """
    as_of = as_of or date.today()
    raw = _call(
        ak.stock_zh_a_hist,
        symbol=code,
        period="daily",
        start_date=(as_of - timedelta(days=2 * days)).strftime("%Y%m%d"),
        end_date=as_of.strftime("%Y%m%d"),
        adjust="qfq",
    )
    if raw is None:
        return None
    if raw.empty:
        return pd.DataFrame(columns=list(KLINE_COLUMNS))

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["日期"]),
            "close": pd.to_numeric(raw["收盘"], errors="coerce"),
        }
    )
    return (
        frame.sort_values("date")
        .tail(days)
        .dropna(subset=["close"])  # indicators 不允许看到 NaN 收盘价
        .reset_index(drop=True)
    )


def get_dividend_history(code: str) -> pd.DataFrame | None:
    """单只股票分红明细，归一为 ``date, dividend_per_share``；失败 → None。

    仅保留 ``进度 == "实施"`` 且除权除息日非空的记录（预案/未实施剔除），
    ``派息``（元/10 股，税前）折算为每股分红，按除权除息日升序。
    """
    raw = _call(ak.stock_history_dividend_detail, symbol=code, indicator="分红")
    if raw is None:
        return None
    if raw.empty:
        return pd.DataFrame(columns=list(DIVIDEND_COLUMNS))

    implemented = raw.loc[raw["进度"] == "实施"].copy()
    implemented["除权除息日"] = pd.to_datetime(implemented["除权除息日"])
    implemented = implemented.dropna(subset=["除权除息日"])

    return (
        pd.DataFrame(
            {
                "date": implemented["除权除息日"],
                "dividend_per_share": pd.to_numeric(
                    implemented["派息"], errors="coerce"
                )
                / 10.0,
            }
        )
        .sort_values("date")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# 行业 PE/PB 与缓存
# ---------------------------------------------------------------------------


def get_industry_pe_pb(
    code: str, cache_dir: Path, cache_days: int = 7
) -> dict | None:
    """个股所属行业的 PE/PB 中位数（缓存 cache_days 天）；任一次获取失败 → None。

    先经 ``stock_individual_info_em`` 取 ``行业``，再经
    ``stock_board_industry_cons_em`` 取该行业成分股的 ``市盈率-动态`` / ``市净率``，
    剔除缺失值与非正值后取中位数；某字段无有效值则为 None（仍返回行业名）。
    个股→行业映射与行业中位数分别缓存于 ``cache_dir/pe_cache.json``，
    两者 TTL 均为 cache_days。
    """
    cache_path = Path(cache_dir) / PE_CACHE_FILENAME
    cache = _load_cache(cache_path)
    stamp = datetime.now().isoformat()

    industry = _cached_industry(cache, code, cache_days)
    if industry is None:
        raw = _call(ak.stock_individual_info_em, symbol=code)
        industry = _industry_from_info(raw)
        if industry is None:
            return None
        cache["stocks"][code] = {"industry": industry, "updated_at": stamp}

    medians = _cached_medians(cache, industry, cache_days)
    if medians is None:
        cons = _call(ak.stock_board_industry_cons_em, symbol=industry)
        if cons is None:
            return None
        medians = {
            "pe": _positive_median(cons, "市盈率-动态"),
            "pb": _positive_median(cons, "市净率"),
        }
        cache["industries"][industry] = {**medians, "updated_at": stamp}

    _save_cache(cache_path, cache)
    return {"industry": industry, **medians}


def _industry_from_info(raw: pd.DataFrame | None) -> str | None:
    """从 ``stock_individual_info_em`` 的 item/value 帧中取 ``行业``；缺失 → None。"""
    if (
        raw is None
        or raw.empty
        or "item" not in raw.columns
        or "value" not in raw.columns
    ):
        return None
    matched = raw.loc[raw["item"] == "行业", "value"]
    if matched.empty:
        return None
    industry = matched.iloc[0]
    if industry is None or pd.isna(industry):
        return None
    industry = str(industry).strip()
    return industry or None


def _positive_median(frame: pd.DataFrame, column: str) -> float | None:
    """列值转数值后剔除 NaN 与非正值，取中位数；无有效值 → None。"""
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        return None
    return float(values.median())


def _load_cache(cache_path: Path) -> dict:
    """读取缓存；文件缺失、JSON 损坏或形状不符一律视为空缓存（绝不抛错）。"""
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        stocks = raw["stocks"]
        industries = raw["industries"]
        if not isinstance(stocks, dict) or not isinstance(industries, dict):
            raise TypeError("缓存形状不符")
        return {"stocks": stocks, "industries": industries}
    except (OSError, ValueError, KeyError, TypeError):
        return {"stocks": {}, "industries": {}}


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _is_fresh(updated_at, cache_days: int) -> bool:
    """时间戳晚于 ``now - cache_days`` 视为新鲜；缺失/非法/比较失败 → 不新鲜。"""
    try:
        stamp = datetime.fromisoformat(updated_at)
        return datetime.now() - stamp < timedelta(days=cache_days)
    except (TypeError, ValueError):
        return False


def _cached_industry(cache: dict, code: str, cache_days: int) -> str | None:
    """命中的个股→行业映射；无条目、过期或形状不符 → None（触发重拉）。"""
    entry = cache["stocks"].get(code)
    if not isinstance(entry, dict) or not _is_fresh(
        entry.get("updated_at"), cache_days
    ):
        return None
    industry = entry.get("industry")
    return industry if isinstance(industry, str) and industry else None


def _cached_medians(cache: dict, industry: str, cache_days: int) -> dict | None:
    """命中的行业中位数；无条目、过期或 pe/pb 非数值 → None（触发重算）。"""
    entry = cache["industries"].get(industry)
    if not isinstance(entry, dict) or not _is_fresh(
        entry.get("updated_at"), cache_days
    ):
        return None
    pe, pb = entry.get("pe"), entry.get("pb")
    if any(v is not None and not isinstance(v, (int, float)) for v in (pe, pb)):
        return None
    return {"pe": pe, "pb": pb}
