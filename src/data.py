"""数据层：唯一允许调用 akshare 的模块。

对外只暴露归一化后的规范输出（下游 indicators/scorer 依赖）：

- ``get_stock_spot()`` → 列 ``code, name, price, pe, pb``
  （str, str, float, float, float；缺失值为 NaN，由下游组装 values 时转 None）。
- ``get_kline(code, days=120, as_of=None)`` → 列 ``date, close``
  （datetime64，升序，float；已剔除收盘价 NaN 的行；最多 days 行）。
- ``get_dividend_history(code)`` → 列 ``date, dividend_per_share``
  （datetime64 升序，税前每股分红 float，源数据元/10 股已换算）。

失败契约：任一次获取失败都记 warning（含 akshare 函数名）并重试 1 次，
仍失败返回 ``None``；成功但无数据返回**带规范列的空帧**（绝不返回裸空帧）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

SPOT_COLUMNS = ("code", "name", "price", "pe", "pb")
KLINE_COLUMNS = ("date", "close")
DIVIDEND_COLUMNS = ("date", "dividend_per_share")


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
