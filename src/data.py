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
- ``get_fund_quotes(fund_type)`` → 列 ``code, name, price, iopv``
  （etf/lof 行情表；normal 无行情表 → None；ETF 的 IOPV 为 "-" 时是 NaN）。
- ``get_fund_nav_history(code, fund_type, days=120)`` → 列 ``date, close``
  （datetime64 升序，float；已剔除净值 NaN 的行；最多 days 行）。
- ``get_fund_latest_nav(code)`` → 最新单位净值 float | None。
- ``get_fund_dividend_history(code)`` → 列 ``date``
  （除息日 datetime64 升序；所有基金类型同源）。
- ``get_fund_overview(code)`` → ``{"scale": str | None, "tracker": str | None}``
  （``净资产规模``/``跟踪标的`` 原始串，未做可用性判定）。
- ``resolve_index_symbol(configured, tracker)`` → 支持 PE 分位的指数名 | None
  （configured 优先；否则由 tracker 去掉 ``指数``/``全收益`` 后缀后校验）。
- ``get_index_pe_history(index_symbol)`` → 列 ``date, pe``
  （datetime64 升序，滚动市盈率 float；已剔除 NaN）。

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
import requests

from src.cache import DEFAULT_TTL_SECONDS, Cache

logger = logging.getLogger(__name__)

SPOT_COLUMNS = ("code", "name", "price", "pe", "pb")
KLINE_COLUMNS = ("date", "close")
DIVIDEND_COLUMNS = ("date", "dividend_per_share")
PE_CACHE_FILENAME = "pe_cache.json"
FINANCIAL_CACHE_FILENAME = "financial_cache.json"

FUND_QUOTE_COLUMNS = ("code", "name", "price", "iopv")
FUND_NAV_COLUMNS = ("date", "close")
FUND_DIVIDEND_COLUMNS = ("date",)
INDEX_PE_COLUMNS = ("date", "pe")

# 腾讯回退源（东财 push2 行情对海外 IP 拒绝服务时的备用行情/K线）
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# 东财行情熔断标记：行情集群（push2 系列：个股行情/K线/行业/基金行情）任一调用
# 失败（重试后仍失败）→ 判定不可用：本次运行内不再请求东财行情、直接走回退源或
# 按数据不足处理。启用取数缓存时该判定持久化 BREAKER_SECONDS（30 分钟），后续
# 运行直接沿用——东财恢复前不重复付出每只标的的重试等待。
# main.run() 开始时经 reset_quote_source_state() 重新读取持久化判定。
_eastmoney_quotes_down = False
EASTMONEY_BREAKER_SECONDS = 30 * 60
_BREAKER_CACHE_KEY = "state:eastmoney_quotes_down"


def reset_quote_source_state() -> None:
    """重置熔断标记：若缓存中存在 30 分钟内的持久化判定则沿用，否则清空。"""
    global _eastmoney_quotes_down
    _eastmoney_quotes_down = _read_persisted_breaker()
    if _eastmoney_quotes_down:
        logger.warning("沿用 %d 分钟内的判定：东财行情不可用，直接使用回退源", EASTMONEY_BREAKER_SECONDS // 60)


def _read_persisted_breaker() -> bool:
    cache = _market_cache
    if cache is None:
        return False
    return cache.get(_BREAKER_CACHE_KEY, EASTMONEY_BREAKER_SECONDS) is not None


def _eastmoney_quotes_available() -> bool:
    return not _eastmoney_quotes_down


def _mark_eastmoney_quotes_down() -> None:
    """置位熔断标记（并持久化到缓存，若已启用）；仅首次置位时记录 warning。"""
    global _eastmoney_quotes_down
    if not _eastmoney_quotes_down:
        _eastmoney_quotes_down = True
        logger.warning(
            "东财行情调用失败，已熔断 %d 分钟：期间直接使用回退源或按数据不足处理",
            EASTMONEY_BREAKER_SECONDS // 60,
        )
        if _market_cache is not None:
            _market_cache.set(_BREAKER_CACHE_KEY, {"at": datetime.now().isoformat()})

# stock_index_pe_lg 接受的指数名（"中证红利" 等不在其列，见 resolve_index_symbol）
SUPPORTED_INDEX_PE = {
    "上证50",
    "沪深300",
    "上证380",
    "创业板50",
    "中证500",
    "上证180",
    "深证红利",
    "深证100",
    "中证1000",
    "上证红利",
    "中证100",
    "中证800",
}


# 通用取数缓存（默认 24 小时）：main.run 开始时经 configure_cache 启用，
# 测试与不传 cache_dir 的场景保持关闭（None）。
_market_cache: "Cache | None" = None
_market_cache_ttl_seconds = DEFAULT_TTL_SECONDS


def configure_cache(cache_dir, ttl_hours: float = 24) -> None:
    """启用取数缓存（cache_dir/market_cache.db）；cache_dir 传 None 时关闭。"""
    global _market_cache, _market_cache_ttl_seconds
    if cache_dir is None:
        _market_cache = None
        return
    _market_cache_ttl_seconds = int(ttl_hours * 3600)
    _market_cache = Cache(Path(cache_dir) / "market_cache.db")


def _cached(key: str, fetch):
    """命中缓存直接返回；否则调用 fetch，成功（非 None）时写入缓存。"""
    cache = _market_cache
    if cache is None:
        return fetch()
    hit = cache.get(key, _market_cache_ttl_seconds)
    if hit is not None:
        return hit
    value = fetch()
    if value is not None:
        cache.set(key, value)
    return value


def _call(fn, *args, label=None, **kwargs):
    """调用数据源函数：异常 → warning（含函数名）+ 重试 1 次；仍失败返回 None。

    ``label`` 可覆盖日志里的数据源名称（默认 ``akshare <函数名>``）。
    """
    name = label or f"akshare {getattr(fn, '__name__', repr(fn))}"
    for attempt in (1, 2):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # 网络/解析等上游异常一律降级为 None
            logger.warning(
                "%s 获取失败（第 %d 次尝试）：%s: %s",
                name,
                attempt,
                type(exc).__name__,
                exc,
            )
    return None


def _tencent_symbol(code: str) -> str | None:
    """A 股代码 → 腾讯行情符号；6→sh、0/3→sz、4/8/9→bj，无法识别 → None。"""
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return None


def _http_text(url: str, params: dict | None = None, encoding: str = "gbk") -> str:
    """GET 并解码（腾讯行情为 GBK，K 线 JSON 为 UTF-8）；异常交给 _call 降级。"""
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.content.decode(encoding, errors="replace")


def _num_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_stock_spot(codes) -> pd.DataFrame | None:
    """自选 A 股实时行情（东财主源 → 腾讯回退），归一为 ``code, name, price, pe, pb``。

    结果按 ``market_cache_hours``（默认 24 小时）缓存，键为自选代码集合。
    """
    codes = list(codes)
    return _cached(
        f"spot:{','.join(sorted(codes))}", lambda: _fetch_stock_spot(codes)
    )


def _fetch_stock_spot(codes) -> pd.DataFrame | None:
    """自选 A 股实时行情，归一为 ``code, name, price, pe, pb`` 并只保留 codes。

    主源东财 ``stock_zh_a_spot_em`` 全市场表（push2 对海外 IP 拒绝服务）；
    失败时回退腾讯 ``qt.gtimg.cn`` 批量行情（一次请求，覆盖沪/深/北交所）。
    两个源都失败 → None；代码前缀无法识别时该代码不出现（也不会被请求）。
    """
    codes = list(codes)
    if _eastmoney_quotes_available():
        raw = _call(ak.stock_zh_a_spot_em)
        if raw is None:
            _mark_eastmoney_quotes_down()  # 行情集群失败即熔断（30 分钟内不重复重试）
    else:
        logger.info("东财行情已熔断，get_stock_spot 直接使用腾讯行情")
        raw = None
    if raw is None:
        logger.warning("get_stock_spot 东财行情不可用，回退腾讯行情")
        frame = _tencent_spot(codes)
        if frame is None:
            return None
    else:
        if raw.empty:
            return pd.DataFrame(columns=list(SPOT_COLUMNS))
        frame = pd.DataFrame(
            {
                "code": raw["代码"].astype(str),
                "name": raw["名称"].astype(str),
                "price": pd.to_numeric(raw["最新价"], errors="coerce"),
                "pe": pd.to_numeric(raw["市盈率-动态"], errors="coerce"),
                "pb": pd.to_numeric(raw["市净率"], errors="coerce"),
            }
        )
    return frame[frame["code"].isin(codes)].reset_index(drop=True)


def _tencent_spot(codes: list[str]) -> pd.DataFrame | None:
    """腾讯批量行情 → canonical 行情帧；全部代码前缀无法识别 → 空帧（不请求）。"""
    symbols = [symbol for c in codes if (symbol := _tencent_symbol(c)) is not None]
    if not symbols:
        return pd.DataFrame(columns=list(SPOT_COLUMNS))
    text = _call(
        _http_text,
        TENCENT_QUOTE_URL + ",".join(symbols),
        label="tencent qt.gtimg.cn",
    )
    if text is None:
        return None
    rows = []
    for line in text.strip().split(";"):
        if '="' not in line:
            continue
        parts = line.split('="', 1)[1].rstrip('"').split("~")
        if len(parts) < 47 or not parts[2]:
            continue
        rows.append(
            {
                "code": parts[2],
                "name": parts[1],
                "price": _num_or_none(parts[3]),
                "pe": _num_or_none(parts[39]),
                "pb": _num_or_none(parts[46]),
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(SPOT_COLUMNS))
    return pd.DataFrame(rows, columns=list(SPOT_COLUMNS))


def get_kline(
    code: str, days: int = 120, as_of: date | None = None
) -> pd.DataFrame | None:
    """单只日 K 线（前复权，东财主源 → 腾讯回退），归一为 ``date, close``。

    结果按 ``market_cache_hours``（默认 24 小时）缓存（显式 ``as_of`` 单独成键）。
    """
    key = f"kline:{code}:{days}:{as_of.isoformat() if as_of else 'live'}"
    return _cached(key, lambda: _fetch_kline(code, days, as_of))


def _fetch_kline(
    code: str, days: int = 120, as_of: date | None = None
) -> pd.DataFrame | None:
    """单只日 K 线（前复权），归一为 ``date, close``；失败 → None。

    请求区间为 ``as_of - 2×days`` 自然日至 ``as_of``（默认今天），
    取最后 days 行并按日期升序，剔除收盘价为 NaN 的行。
    """
    as_of = as_of or date.today()
    if _eastmoney_quotes_available():
        raw = _call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=(as_of - timedelta(days=2 * days)).strftime("%Y%m%d"),
            end_date=as_of.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if raw is None:
            _mark_eastmoney_quotes_down()  # 行情集群失败即熔断（30 分钟内不重复重试）
    else:
        logger.info("东财行情已熔断，get_kline(%s) 直接使用腾讯行情", code)
        raw = None
    if raw is None:
        logger.warning("get_kline(%s) 东财行情不可用，回退腾讯行情", code)
        return _tencent_kline(code, days)
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


def _tencent_kline(code: str, days: int) -> pd.DataFrame | None:
    """腾讯前复权日线 → canonical K 线帧；取末尾 days 根。

    腾讯接口按条数取数（不接日期区间），故回退路径忽略 ``as_of``。
    """
    symbol = _tencent_symbol(code)
    if symbol is None:
        return None
    text = _call(
        _http_text,
        TENCENT_KLINE_URL,
        params={"param": f"{symbol},day,,,{days},qfq"},
        encoding="utf-8",
        label="tencent fqkline",
    )
    if text is None:
        return None
    try:
        payload = json.loads(text)
        rows = payload["data"][symbol].get("qfqday") or []
    except (ValueError, KeyError, TypeError):
        logger.warning("腾讯 K 线解析失败：%s", code)
        return None
    rows = [row for row in rows if len(row) >= 3]
    if not rows:
        return pd.DataFrame(columns=list(KLINE_COLUMNS))
    frame = pd.DataFrame({"date": [r[0] for r in rows], "close": [r[2] for r in rows]})
    frame["date"] = pd.to_datetime(frame["date"])
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return (
        frame.sort_values("date")
        .tail(days)
        .dropna(subset=["close"])  # indicators 不允许看到 NaN 收盘价
        .reset_index(drop=True)
    )


def get_dividend_history(code: str) -> pd.DataFrame | None:
    """单只个股实施完毕的分红明细，归一为 ``date, dividend_per_share``。

    结果按 ``market_cache_hours``（默认 24 小时）缓存。
    """
    return _cached(f"dividend:{code}", lambda: _fetch_dividend_history(code))


def _fetch_dividend_history(code: str) -> pd.DataFrame | None:
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
        if not _eastmoney_quotes_available():
            logger.info("东财行情已熔断，get_industry_pe_pb(%s) 按数据不足处理", code)
            return None
        raw = _call(ak.stock_individual_info_em, symbol=code)
        if raw is None:
            _mark_eastmoney_quotes_down()  # 行情集群失败即熔断（30 分钟内不重复重试）
        industry = _industry_from_info(raw)
        if industry is None:
            return None
        cache["stocks"][code] = {"industry": industry, "updated_at": stamp}

    medians = _cached_medians(cache, industry, cache_days)
    if medians is None:
        if not _eastmoney_quotes_available():
            logger.info("东财行情已熔断，get_industry_pe_pb(%s) 按数据不足处理", code)
            return None
        cons = _call(ak.stock_board_industry_cons_em, symbol=industry)
        if cons is None:
            _mark_eastmoney_quotes_down()  # 行情集群失败即熔断（30 分钟内不重复重试）
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


def _load_cache(cache_path: Path, keys=("stocks", "industries")) -> dict:
    """读取缓存；文件缺失、JSON 损坏或形状不符一律视为空缓存（绝不抛错）。

    ``keys`` 为期望的顶层映射（PE 缓存为 stocks+industries，财报缓存只用 stocks）。
    """
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        data = {key: raw[key] for key in keys}
        if not all(isinstance(value, dict) for value in data.values()):
            raise TypeError("缓存形状不符")
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return {key: {} for key in keys}


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


# ---------------------------------------------------------------------------
# 基金：行情 / 净值 / 分红 / 概况
# ---------------------------------------------------------------------------

# 值存函数名而非函数对象，保证 monkeypatch akshare 后 `getattr(ak, ...)` 生效
_FUND_SPOT_FUNCTIONS = {"etf": "fund_etf_spot_em", "lof": "fund_lof_spot_em"}
_FUND_HIST_FUNCTIONS = {"etf": "fund_etf_hist_em", "lof": "fund_lof_hist_em"}


def get_fund_quotes(fund_type: str) -> pd.DataFrame | None:
    """ETF/LOF 实时行情，归一为 ``code, name, price, iopv``。

    结果按 ``market_cache_hours``（默认 24 小时）缓存。
    """
    return _cached(f"fund_quotes:{fund_type}", lambda: _fetch_fund_quotes(fund_type))


def _fetch_fund_quotes(fund_type: str) -> pd.DataFrame | None:
    """ETF/LOF 实时行情，归一为 ``code, name, price, iopv``；失败 → None。

    ``etf`` 源 ``fund_etf_spot_em``、``lof`` 源 ``fund_lof_spot_em``（上游无
    IOPV 列 → 该列全为 NaN）；``normal`` 开放式基金无行情表 → None。
    """
    ak_name = _FUND_SPOT_FUNCTIONS.get(fund_type)
    if ak_name is None:
        return None
    if not _eastmoney_quotes_available():
        logger.info("东财行情已熔断，get_fund_quotes(%s) 按数据不足处理", fund_type)
        return None
    raw = _call(getattr(ak, ak_name))
    if raw is None:
        _mark_eastmoney_quotes_down()  # 行情集群失败即熔断（30 分钟内不重复重试）
        return None
    if raw.empty:
        return pd.DataFrame(columns=list(FUND_QUOTE_COLUMNS))

    return pd.DataFrame(
        {
            "code": raw["代码"].astype(str),
            "name": raw["名称"].astype(str),
            "price": pd.to_numeric(raw["最新价"], errors="coerce"),
            "iopv": (
                pd.to_numeric(raw["IOPV实时估值"], errors="coerce")
                if "IOPV实时估值" in raw.columns
                else float("nan")
            ),
        }
    ).reset_index(drop=True)


def get_fund_nav_history(
    code: str, fund_type: str, days: int = 120
) -> pd.DataFrame | None:
    """单只基金日净值，归一为 ``date, close``。

    结果按 ``market_cache_hours``（默认 24 小时）缓存。
    """
    return _cached(
        f"fund_nav:{code}:{fund_type}:{days}",
        lambda: _fetch_fund_nav_history(code, fund_type, days),
    )


def _fetch_fund_nav_history(
    code: str, fund_type: str, days: int = 120
) -> pd.DataFrame | None:
    """单只基金日净值，归一为 ``date, close``；失败 → None。

    ``etf``/``lof`` 走 ``fund_etf_hist_em``/``fund_lof_hist_em``（``日期/收盘``，
    请求区间与 ``get_kline`` 同为今天前 2×days 自然日）；``normal`` 走
    ``fund_open_fund_info_em`` 的单位净值走势（``净值日期/单位净值``）。
    取末尾 days 行并按日期升序，剔除净值为 NaN 的行。
    """
    if fund_type == "normal":
        raw = _call(ak.fund_open_fund_info_em, symbol=code, indicator="单位净值走势")
        date_column, close_column = "净值日期", "单位净值"
    else:
        ak_name = _FUND_HIST_FUNCTIONS.get(fund_type)
        if ak_name is None:
            return None
        as_of = date.today()
        if _eastmoney_quotes_available():
            raw = _call(
                getattr(ak, ak_name),
                symbol=code,
                period="daily",
                start_date=(as_of - timedelta(days=2 * days)).strftime("%Y%m%d"),
                end_date=as_of.strftime("%Y%m%d"),
                adjust="",
            )
            if raw is None:
                _mark_eastmoney_quotes_down()  # 行情集群失败即熔断
        else:
            logger.info(
                "东财行情已熔断，get_fund_nav_history(%s) 直接使用单位净值走势", code
            )
            raw = None
        date_column, close_column = "日期", "收盘"
        if raw is None:
            # 东财历史行情不可用（海外常见）→ 回退单位净值走势（fund.eastmoney.com 可达）
            logger.warning(
                "get_fund_nav_history(%s, %s) 东财行情不可用，回退单位净值走势",
                code,
                fund_type,
            )
            raw = _call(
                ak.fund_open_fund_info_em, symbol=code, indicator="单位净值走势"
            )
            date_column, close_column = "净值日期", "单位净值"
    if raw is None:
        return None
    if raw.empty:
        return pd.DataFrame(columns=list(FUND_NAV_COLUMNS))

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[date_column]),
            "close": pd.to_numeric(raw[close_column], errors="coerce"),
        }
    )
    return (
        frame.sort_values("date")
        .tail(days)
        .dropna(subset=["close"])  # indicators 不应看到 NaN 净值
        .reset_index(drop=True)
    )


def get_fund_latest_nav(code: str) -> float | None:
    """最新单位净值（``单位净值走势`` 末行）；结果按 24 小时缓存。"""
    return _cached(f"fund_latest_nav:{code}", lambda: _fetch_fund_latest_nav(code))


def _fetch_fund_latest_nav(code: str) -> float | None:
    """最新单位净值（``单位净值走势`` 末行）；失败或无有效值 → None。"""
    raw = _call(ak.fund_open_fund_info_em, symbol=code, indicator="单位净值走势")
    if raw is None or raw.empty:
        return None
    value = pd.to_numeric(raw["单位净值"], errors="coerce").iloc[-1]
    return None if pd.isna(value) else float(value)


def get_fund_dividend_history(code: str) -> pd.DataFrame | None:
    """基金分红除息日序列，归一为 ``date`` 单列；结果按 24 小时缓存。"""
    return _cached(
        f"fund_dividends:{code}", lambda: _fetch_fund_dividend_history(code)
    )


def _fetch_fund_dividend_history(code: str) -> pd.DataFrame | None:
    """单只基金分红明细，归一为 ``date``（除息日，升序）；失败 → None。

    ETF/LOF/开放式基金同源于 ``fund_open_fund_info_em`` 的分红送配详情；
    除息日缺失（未实施）的记录剔除，``每10份分红`` 文本不解析。
    """
    raw = _call(ak.fund_open_fund_info_em, symbol=code, indicator="分红送配详情")
    if raw is None:
        return None
    if raw.empty:
        return pd.DataFrame(columns=list(FUND_DIVIDEND_COLUMNS))

    frame = pd.DataFrame({"date": pd.to_datetime(raw["除息日"], errors="coerce")})
    return frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def get_fund_overview(code: str) -> dict | None:
    """基金概况原始串（``scale``/``tracker``）；结果按 24 小时缓存。"""
    return _cached(f"fund_overview:{code}", lambda: _fetch_fund_overview(code))


def _fetch_fund_overview(code: str) -> dict | None:
    """基金概况 ``{"scale": str | None, "tracker": str | None}``；失败 → None。

    取 ``fund_overview_em`` 首行的 ``净资产规模`` 与 ``跟踪标的`` 原始串，
    不做解析或可用性判定（是否支持指数 PE 由 ``resolve_index_symbol`` 负责）。
    """
    raw = _call(ak.fund_overview_em, symbol=code)
    if raw is None or raw.empty:
        return None
    row = raw.iloc[0]
    return {
        "scale": _clean_str(row.get("净资产规模")),
        "tracker": _clean_str(row.get("跟踪标的")),
    }


# ---------------------------------------------------------------------------
# 指数 PE
# ---------------------------------------------------------------------------


def resolve_index_symbol(
    configured: str | None, tracker: str | None
) -> str | None:
    """解析用于指数 PE 分位的指数名；无法解析 → None 并记 warning。

    ``configured``（配置的 ``fund.index``）优先，须原样命中 ``SUPPORTED_INDEX_PE``；
    否则用 ``tracker``（基金概况的跟踪标的）去掉 ``指数``/``全收益`` 后缀后校验。
    配置了不支持项同样告警（不做 tracker 回退），便于定位错误配置。
    """
    configured = _clean_str(configured)
    if configured is not None:
        if configured in SUPPORTED_INDEX_PE:
            return configured
        logger.warning(
            "配置的指数 %s 不支持指数 PE 查询（stock_index_pe_lg），跳过指数估值指标",
            configured,
        )
        return None

    tracker = _clean_str(tracker)
    normalized = _strip_tracker_suffix(tracker)
    if normalized is not None and normalized in SUPPORTED_INDEX_PE:
        return normalized
    logger.warning(
        "跟踪标的 %s 无法映射到支持指数 PE 查询的指数，跳过指数估值指标", tracker
    )
    return None


def _clean_str(value) -> str | None:
    """去首尾空白后的字符串；缺失或空串 → None。"""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _strip_tracker_suffix(tracker: str | None) -> str | None:
    """去掉跟踪标的结尾的 ``指数``/``全收益``（上证红利全收益指数 → 上证红利）。"""
    name = _clean_str(tracker)
    if name is None:
        return None
    for suffix in ("指数", "全收益"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name or None


def get_index_pe_history(index_symbol: str) -> pd.DataFrame | None:
    """乐咕乐股指数 PE 历史，归一为 ``date, pe``（滚动市盈率）；按 24 小时缓存。"""
    return _cached(
        f"index_pe:{index_symbol}", lambda: _fetch_index_pe_history(index_symbol)
    )


def _fetch_index_pe_history(index_symbol: str) -> pd.DataFrame | None:
    """乐咕乐股指数 PE 历史，归一为 ``date, pe``（滚动市盈率）；失败 → None。

    按日期升序并剔除滚动市盈率为 NaN 的行（calc_index_pe_position 的夹取
    会把 NaN 当成满分档，NaN 不得进入打分）。
    """
    raw = _call(ak.stock_index_pe_lg, symbol=index_symbol)
    if raw is None:
        return None
    if raw.empty:
        return pd.DataFrame(columns=list(INDEX_PE_COLUMNS))

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["日期"]),
            "pe": pd.to_numeric(raw["滚动市盈率"], errors="coerce"),
        }
    )
    return frame.sort_values("date").dropna(subset=["pe"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 分红可持续性（个股财务分析指标，新浪财经）
# ---------------------------------------------------------------------------


def get_financial_health(
    code: str, cache_dir, cache_days: int = 30
) -> dict | None:
    """个股年报口径的可持续性指标（缓存 cache_days 天）；失败或无年报行 → None。

    来源 ``stock_financial_analysis_indicator``（新浪，按报告期升序），取最近一个
    12-31 年报行：``净利润增长率(%)``、``每股经营性现金流(元)``、``股息发放率(%)``。
    财报季度更新，缓存 ``cache_dir/financial_cache.json``；失败不写缓存。
    """
    cache_path = Path(cache_dir) / FINANCIAL_CACHE_FILENAME
    cache = _load_cache(cache_path, keys=("stocks",))
    entry = cache["stocks"].get(code)
    if isinstance(entry, dict) and _is_fresh(entry.get("updated_at"), cache_days):
        values = {
            key: entry.get(key)
            for key in ("eps_growth", "op_cash_per_share", "payout_stmt")
        }
        if all(v is None or isinstance(v, (int, float)) for v in values.values()):
            return values

    start_year = str(date.today().year - 3)
    raw = _call(ak.stock_financial_analysis_indicator, symbol=code, start_year=start_year)
    if raw is None or raw.empty or "日期" not in raw.columns:
        return None
    frame = raw.copy()
    frame["日期"] = frame["日期"].astype(str)
    annual = frame[frame["日期"].str.endswith("12-31")].sort_values("日期")
    if annual.empty:
        return None
    latest = annual.iloc[-1]

    def _value(column: str) -> float | None:
        if column not in annual.columns:
            return None
        value = pd.to_numeric(latest[column], errors="coerce")
        return None if pd.isna(value) else float(value)

    result = {
        "eps_growth": _value("净利润增长率(%)"),
        "op_cash_per_share": _value("每股经营性现金流(元)"),
        "payout_stmt": _value("股息发放率(%)"),
    }
    cache["stocks"][code] = {**result, "updated_at": datetime.now().isoformat()}
    _save_cache(cache_path, cache)
    return result


# ---------------------------------------------------------------------------
# 市场温度计（板块股息率 / 10Y 国债收益率）
# ---------------------------------------------------------------------------


def get_index_dividend_yield(index_code: str = "000922") -> float | None:
    """中证指数官网最新股息率（%，取 ``股息率1``）；结果按 24 小时缓存。"""
    return _cached(
        f"index_div_yield:{index_code}",
        lambda: _fetch_index_dividend_yield(index_code),
    )


def _fetch_index_dividend_yield(index_code: str = "000922") -> float | None:
    """中证指数官网最新股息率（%，取 ``股息率1``），默认中证红利；失败 → None。

    返回帧为倒序（新在前）且可能含 NaN，按日期升序取最后一个有效值。
    """
    raw = _call(ak.stock_zh_index_value_csindex, symbol=index_code)
    if raw is None or raw.empty or "股息率1" not in raw.columns:
        return None
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["日期"], errors="coerce"),
            "value": pd.to_numeric(raw["股息率1"], errors="coerce"),
        }
    ).dropna(subset=["date", "value"])
    if frame.empty:
        return None
    return float(frame.sort_values("date")["value"].iloc[-1])


def get_10y_bond_yield(days: int = 90) -> float | None:
    """中国 10 年期国债收益率最新值（%）；结果按 24 小时缓存。"""
    return _cached(f"bond_10y:{days}", lambda: _fetch_10y_bond_yield(days))


def _fetch_10y_bond_yield(days: int = 90) -> float | None:
    """中国 10 年期国债收益率最新值（%）；失败 → None。

    只请求近 ``days`` 天（bond_zh_us_rate 默认拉取 1990 年至今，全量要 19 次请求）。
    """
    start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    raw = _call(ak.bond_zh_us_rate, start_date=start)
    if raw is None or raw.empty or "中国国债收益率10年" not in raw.columns:
        return None
    values = pd.to_numeric(raw["中国国债收益率10年"], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.iloc[-1])


def get_index_constituents(index_code: str) -> pd.DataFrame | None:
    """中证指数官网成分股，归一为 ``code, name``（去重、按代码升序）；失败 → None。"""
    raw = _call(ak.index_stock_cons_csindex, symbol=index_code)
    if raw is None:
        return None
    if raw.empty:
        return pd.DataFrame(columns=["code", "name"])
    frame = pd.DataFrame(
        {
            "code": raw["成分券代码"].astype(str),
            "name": raw["成分券名称"].astype(str),
        }
    )
    return (
        frame.drop_duplicates(subset=["code"])
        .sort_values("code")
        .reset_index(drop=True)
    )
