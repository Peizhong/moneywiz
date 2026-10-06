"""编排入口：加载配置 → 拉取数据 → 计算指标 → 打分 → 渲染终端报告。

只做编排与容错，不含业务公式；所有 akshare 访问都经 ``src.data``：
每个数据源独立捕获异常并降级为「该指标不可评分（None）」，单只标的失败
绝不影响其余标的。
"""

from __future__ import annotations

import logging
import math
import sys
import time
from datetime import date
from pathlib import Path

from src import config, constituents, data, indicators, reporter, scorer

logger = logging.getLogger(__name__)


def _fetch(fetch, label: str):
    """调用一个数据获取函数：异常或返回 None 都记 warning 并降级为 None。"""
    try:
        result = fetch()
    except Exception as exc:  # 上游/解析等异常一律降级，不中断全局
        logger.warning("%s 获取失败：%s: %s", label, type(exc).__name__, exc)
        return None
    if result is None:
        logger.warning("%s 获取失败（返回空），相关指标按数据不足处理", label)
    return result


def _num(value):
    """把 NaN 统一转为 None；其余（含 None）原样返回，保证 NaN 不进打分器。"""
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except TypeError:
        pass
    return value


def _result(
    code,
    name,
    values,
    rules_section,
    tech,
    position=None,
    sustainability=None,
    volatility=None,
    turnover_wan=None,
):
    """按 reporter 契约组装单只标的的 result dict。

    总分经分红可持续性红标扣分（rules.yaml 的 stocks.sustainability 可配）。
    """
    score = scorer.score_instrument(values, rules_section)
    total, _penalty = scorer.apply_sustainability_penalty(
        score["total"], sustainability, rules_section.get("sustainability")
    )
    return {
        "code": code,
        "name": name,
        "total": total,
        "values": values,
        "scores": score["scores"],
        "missing": score["missing"],
        "tech": tech,
        "position": position,
        "sustainability": sustainability,
        "volatility": volatility,
        "turnover_wan": turnover_wan,
    }


def _spot_row(spot, code):
    """行情表中该代码的行；表缺失、为空或无该行 → None。"""
    if spot is None or spot.empty:
        return None
    matched = spot.loc[spot["code"] == code]
    return None if matched.empty else matched.iloc[0]


def _quote_row(quotes, code):
    """基金行情表中该代码的行；表缺失、为空或无该行 → None。"""
    if quotes is None or quotes.empty:
        return None
    matched = quotes.loc[quotes["code"] == code]
    return None if matched.empty else matched.iloc[0]


def _stock_result(
    stock,
    spot,
    cache_dir,
    as_of,
    kline_days,
    pe_cache_days,
    financial_cache_days,
    rules_section,
):
    """组装一只股票：每个数据源独立降级，任一失败只影响对应指标。"""
    row = _spot_row(spot, stock.code)
    if row is None and spot is not None and not spot.empty:
        # 只有行情表可用却缺该股时才逐股告警；整表不可用已有表级告警，避免误导性刷屏
        logger.warning("股票 %s 不在行情表中，价格/PE/PB 按缺失处理", stock.code)
    price = _num(row["price"]) if row is not None else None
    pe = _num(row["pe"]) if row is not None else None
    pb = _num(row["pb"]) if row is not None else None

    dividends = _fetch(
        lambda: data.get_dividend_history(stock.code),
        f"get_dividend_history({stock.code})",
    )
    kline = _fetch(
        lambda: data.get_kline(stock.code, days=kline_days, as_of=as_of),
        f"get_kline({stock.code})",
    )
    industry = _fetch(
        lambda: data.get_industry_pe_pb(stock.code, cache_dir, pe_cache_days),
        f"get_industry_pe_pb({stock.code})",
    )
    industry_pe = _num(industry["pe"]) if industry is not None else None
    industry_pb = _num(industry["pb"]) if industry is not None else None

    dividend_yield = _num(indicators.calc_dividend_yield(dividends, price, as_of))
    ma60 = _num(indicators.calc_ma(kline, window=60))
    macd = indicators.calc_macd(kline)

    values = {
        "dividend_yield": dividend_yield,
        "dividend_years": _num(indicators.calc_dividend_years(dividends, as_of)),
        "dividend_trend": _num(indicators.calc_dividend_trend(dividends, as_of)),
        "payout_ratio": _num(indicators.calc_payout_ratio(dividend_yield, pe)),
        "pe_vs_industry": _num(indicators.calc_pe_vs_industry(pe, industry_pe)),
        "pb_vs_industry": _num(indicators.calc_pb_vs_industry(pb, industry_pb)),
        "ma60_position": _num(indicators.calc_ma60_position(price, ma60)),
        "momentum_5d": _num(indicators.calc_momentum_5d(kline)),
    }
    tech = {
        "macd": macd["signal"] if macd else None,
        "rsi": _num(indicators.calc_rsi(kline)),
    }
    return _result(
        stock.code,
        stock.name,
        values,
        rules_section,
        tech,
        position=_num(indicators.calc_price_position(kline)),
        sustainability=_sustainability(
            stock.code, dividends, as_of, cache_dir, financial_cache_days
        ),
        volatility=_num(indicators.calc_volatility(kline)),
        turnover_wan=_num(indicators.calc_turnover_amount(kline)),
    )


def _sustainability(code, dividends, as_of, cache_dir, financial_cache_days):
    """分红可持续性红标：盈利下滑 / 经营现金流为负 / 分红超现金流。

    数据来自 ``get_financial_health``（带 cache_days 缓存）：盈利下滑按最新报告期
    （含中报/季报）净利润同比，现金流与分红覆盖率按最近年报口径；任一数据缺失
    只影响对应警示。
    """
    health = _fetch(
        lambda: data.get_financial_health(
            code, cache_dir, cache_days=financial_cache_days
        ),
        f"get_financial_health({code})",
    )
    if health is None:
        return None
    op_cash = health.get("op_cash_per_share")
    ttm_per_share = _num(indicators.calc_ttm_dividend_per_share(dividends, as_of))
    cash_cover = None
    if ttm_per_share is not None and op_cash is not None and op_cash > 0:
        cash_cover = ttm_per_share / op_cash * 100.0
    return {
        "eps_growth": health.get("eps_growth"),
        "eps_period": health.get("eps_period"),
        "op_cash_per_share": op_cash,
        "cash_cover": cash_cover,
    }


def _fetch_fund_quotes(cfg_funds):
    """按类型拉取一次基金行情表（normal 无行情表，不请求）。"""
    quotes = {}
    for fund_type in sorted({fund.type for fund in cfg_funds}):
        if fund_type == "normal":
            continue
        codes = [fund.code for fund in cfg_funds if fund.type == fund_type]
        quotes[fund_type] = _fetch(
            lambda t=fund_type, c=codes: data.get_fund_quotes(t, c),
            f"get_fund_quotes({fund_type})",
        )
    return quotes


def _scan_stocks(watchlist, constituent_entries):
    """扫描清单 = 自选 ∪ 成分股（按代码去重；自选优先，保留其名称）。"""
    stocks = list(watchlist)
    known = {stock.code for stock in stocks}
    for entry in constituent_entries:
        code = str(entry.get("code") or "")
        if code and code not in known:
            stocks.append(
                config.StockCfg(code=code, name=str(entry.get("name") or code))
            )
            known.add(code)
    return stocks


def _market_context():
    """板块温度计：中证红利股息率、10Y 国债收益率、利差、上证红利 PE 分位。

    任一数据源失败只影响对应字段；全部缺失时返回 None（报告不显示该行）。
    """
    dividend = _fetch(
        lambda: data.get_index_dividend_yield(), "get_index_dividend_yield"
    )
    bond = _fetch(lambda: data.get_10y_bond_yield(), "get_10y_bond_yield")
    pe_history = _fetch(
        lambda: data.get_index_pe_history("上证红利"),
        "get_index_pe_history(上证红利)",
    )
    position = _num(indicators.calc_index_pe_position(pe_history))
    spread = None
    if dividend is not None and bond is not None:
        spread = dividend - bond
    if dividend is None and bond is None and position is None:
        return None
    return {
        "dividend_yield": dividend,
        "bond_10y": bond,
        "spread": spread,
        "index_pe_position": position,
    }


def _fund_result(fund, quotes, as_of, kline_days, rules_section):
    """组装一只基金：每个数据源独立降级，任一失败只影响对应指标。"""
    row = _quote_row(quotes, fund.code)
    if quotes is not None and row is None:
        logger.warning("基金 %s 不在 %s 行情表中，价格按缺失处理", fund.code, fund.type)
    price = _num(row["price"]) if row is not None else None

    if fund.type == "etf":
        nav = _num(row["iopv"]) if row is not None else None
    else:
        nav = None  # LOF 稍后用最新净值；场外基金不可评分

    if fund.type in ("etf", "lof") and nav is None:
        # LOF，或 ETF 无 IOPV（腾讯回退路径）→ 用最新单位净值做日频折溢价代理
        nav = _num(
            _fetch(
                lambda: data.get_fund_latest_nav(fund.code),
                f"get_fund_latest_nav({fund.code})",
            )
        )

    nav_history = _fetch(
        lambda: data.get_fund_nav_history(fund.code, fund.type, days=kline_days),
        f"get_fund_nav_history({fund.code})",
    )
    dividend_history = _fetch(
        lambda: data.get_fund_dividend_history(fund.code),
        f"get_fund_dividend_history({fund.code})",
    )
    overview = _fetch(
        lambda: data.get_fund_overview(fund.code),
        f"get_fund_overview({fund.code})",
    )
    tracker = overview["tracker"] if overview is not None else None
    scale = overview["scale"] if overview is not None else None
    index_symbol = _fetch(
        lambda: data.resolve_index_symbol(fund.index, tracker),
        f"resolve_index_symbol({fund.code})",
    )
    pe_history = (
        _fetch(
            lambda: data.get_index_pe_history(index_symbol),
            f"get_index_pe_history({index_symbol})",
        )
        if index_symbol is not None
        else None
    )

    values = {
        "discount_rate": _num(indicators.calc_fund_discount(price, nav)),
        "nav_trend_20d": _num(indicators.calc_nav_trend(nav_history, days=20)),
        "index_pe_vs_history": _num(indicators.calc_index_pe_position(pe_history)),
        "dividend_frequency": _num(
            indicators.calc_dividend_frequency(dividend_history, as_of)
        ),
        "fund_size": _num(indicators.calc_fund_size(scale)),
    }
    return _result(
        fund.code,
        fund.name,
        values,
        rules_section,
        tech=None,
        position=_num(indicators.calc_price_position(nav_history)),
    )


def run(
    config_dir: Path = Path("config"),
    cache_dir: Path = Path("cache"),
    as_of: date | None = None,
) -> str:
    """跑一遍筛选并返回完整报告文本（含摘要行）。"""
    started = time.monotonic()
    cfg = config.load_config(config_dir)
    data.configure_cache(
        cache_dir, ttl_hours=cfg.rules["data"]["market_cache_hours"]
    )
    data.reset_quote_source_state()  # 读取（可能持久化的）东财熔断判定
    as_of = as_of or date.today()
    kline_days = cfg.rules["data"]["kline_days"]
    pe_cache_days = cfg.rules["data"]["pe_cache_days"]

    refresh_days = cfg.rules["data"]["index_refresh_days"]
    refresh = constituents.refresh_indices(
        config_dir / config.FILENAMES["constituents"],
        cfg.indices,
        refresh_days,
    )
    if refresh["error"]:
        logger.warning("成分股刷新失败（%s），沿用现有名单", refresh["error"])
    new_codes = constituents.new_constituent_codes(refresh["constituents"], refresh_days)
    scan_stocks = _scan_stocks(cfg.stocks, refresh["constituents"])

    spot = _fetch(
        lambda: data.get_stock_spot([stock.code for stock in scan_stocks]),
        "get_stock_spot",
    )
    fund_quotes = _fetch_fund_quotes(cfg.funds)
    market = _market_context()

    stock_results = []
    for stock in scan_stocks:
        result = _stock_result(
            stock,
            spot,
            cache_dir,
            as_of,
            kline_days,
            pe_cache_days,
            cfg.rules["data"]["financial_cache_days"],
            cfg.rules["stocks"],
        )
        result["new_constituent"] = stock.code in new_codes
        stock_results.append(result)
    fund_results = [
        _fund_result(
            fund,
            fund_quotes.get(fund.type),
            as_of,
            kline_days,
            cfg.rules["funds"],
        )
        for fund in cfg.funds
    ]
    return reporter.render_report(
        stock_results,
        fund_results,
        cfg.output,
        time.monotonic() - started,
        market=market,
        risk_hints=cfg.rules["stocks"].get("risk_hints"),
    )


def main() -> int:
    """CLI 入口：打印报告；配置错误时提示并返回 1。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        report = run()
    except config.ConfigError as exc:
        logger.error("配置加载失败：%s", exc)
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
