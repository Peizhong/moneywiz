"""编排入口：加载配置 → 拉取数据 → 计算指标 → 打分 → 渲染终端报告。

只做编排与容错，不含业务公式；所有 akshare 访问都经 ``src.data``：
每个数据源独立捕获异常并降级为「该指标不可评分（None）」，单只标的失败
绝不影响其余标的。
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from datetime import date
from pathlib import Path

from src import config, constituents, data, indicators, interactive, reporter, scorer

logger = logging.getLogger(__name__)

# 波动率取近一年（250 根）不复权 K 线：与股息率历史分位共用同一次取数，
# 窗口贴合"长期持有"视角（120 根偏短线，且除息噪声在 250 根上可忽略）。
VOLATILITY_WINDOW = 250


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
    turnover_wan=None,
):
    """按 reporter 契约组装单只标的的 result dict。

    总分经分红可持续性红标扣分（rules.yaml 的 stocks.sustainability 可配）；
    ``sustainability_flags`` 为触发明细（由 :func:`scorer.evaluate_sustainability`
    判定，展示层据此渲染，不再自行判阈值），无红标或 ``sustainability`` 为 None → ``[]``。
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
        "sustainability_flags": scorer.evaluate_sustainability(
            sustainability, rules_section.get("sustainability")
        ),
        "turnover_wan": turnover_wan,
    }


def _spot_row(spot, code):
    """行情表中该代码的行；表缺失、为空或无该行 → None。"""
    if spot is None or spot.empty:
        return None
    matched = spot.loc[spot["code"] == code]
    return None if matched.empty else matched.iloc[0]


def _market_cap(spot, code):
    """行情表中该代码的总市值（亿元）；表缺失、无该行或值为空 → None。"""
    row = _spot_row(spot, code)
    return _num(row["market_cap"]) if row is not None else None


def _dedupe_by_code(entries: list[dict]) -> list[dict]:
    """按代码去重（保留首次出现的条目）：同一只股票可同时属于多个红利指数。"""
    seen: set[str] = set()
    unique: list[dict] = []
    for entry in entries:
        code = str(entry.get("code") or "")
        if code not in seen:
            seen.add(code)
            unique.append(entry)
    return unique


def _unfiltered(entries: list[dict], unranked=()) -> dict:
    """不筛的结果：原样保留，``threshold`` 为 None（调用方据此走告警分支）。"""
    return {
        "candidates": len(entries),
        "kept": list(entries),
        "unranked": list(unranked),
        "threshold": None,
        "filtered": False,
    }


def _select_constituents(entries: list[dict], spot, limit: int) -> dict:
    """候选池裁剪：各指数成分股合并去重后，按总市值取前 ``limit`` 名（不影响自选）。

    ``limit <= 0``、行情整表不可用（None/空帧/缺 market_cap 列）、或一只候选都排不出
    市值时**不筛**：这几种情况下严格口径会把名单丢光、报告近乎空白。个股取不到市值
    则按严格口径剔除，代码列入 ``unranked`` 由调用方告警。

    返回 ``{"candidates", "kept", "unranked", "threshold", "filtered"}``：
    ``candidates`` 为去重后的候选数，``kept`` 是入选条目并保持名单原顺序（市值只决定
    入选，不重排输出），``threshold`` 为入选末位的总市值（亿元），``unranked`` 为
    市值缺失、被剔除的代码。
    """
    entries = _dedupe_by_code(entries)
    usable = spot is not None and not spot.empty and "market_cap" in spot.columns
    if limit <= 0 or not entries or not usable:
        return _unfiltered(entries)

    ranked, missing = [], []
    for entry in entries:
        cap = _market_cap(spot, entry["code"])
        (ranked if cap is not None else missing).append((cap, entry))
    if not ranked:
        return _unfiltered(entries, [entry["code"] for _cap, entry in missing])
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    admitted = {entry["code"] for _cap, entry in ranked[:limit]}
    return {
        "candidates": len(entries),
        "kept": [entry for entry in entries if entry["code"] in admitted],
        "unranked": [entry["code"] for _cap, entry in missing],
        "threshold": min(cap for cap, _entry in ranked[:limit]),
        "filtered": True,
    }


def _log_candidate_selection(selection: dict, limit: int) -> None:
    """裁剪结果落日志；整表不可用时打显眼告警（本轮不筛）。"""
    if selection["filtered"]:
        logger.info(
            "候选池按总市值取前 %d：成分股 %d → %d（门槛 %.0f 亿）",
            limit,
            selection["candidates"],
            len(selection["kept"]),
            selection["threshold"],
        )
    elif limit > 0 and selection["candidates"]:
        logger.warning(
            "行情不可用，本轮不做市值筛选（成分股 %d 只照常扫描）",
            selection["candidates"],
        )
    if selection["unranked"]:
        logger.warning(
            "候选池剔除市值缺失的 %d 只：%s",
            len(selection["unranked"]),
            "、".join(selection["unranked"]),
        )


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
    raw_kline = _fetch(
        lambda: data.get_kline_raw(stock.code),
        f"get_kline_raw({stock.code})",
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
    # 稳定性指标（波动率/最大回撤）共用近一年窗口的不复权 K 线
    raw_window = raw_kline.tail(VOLATILITY_WINDOW) if raw_kline is not None else None

    values = {
        "dividend_yield": dividend_yield,
        "dividend_yield_percentile": _num(
            indicators.calc_dividend_yield_percentile(dividends, raw_kline)
        ),
        "dividend_years": _num(indicators.calc_dividend_years(dividends, as_of)),
        "dividend_trend": _num(indicators.calc_dividend_trend(dividends, as_of)),
        "payout_ratio": _num(indicators.calc_payout_ratio(dividend_yield, pe)),
        "pe_vs_industry": _num(indicators.calc_pe_vs_industry(pe, industry_pe)),
        "pb_vs_industry": _num(indicators.calc_pb_vs_industry(pb, industry_pb)),
        "ma60_position": _num(indicators.calc_ma60_position(price, ma60)),
        "momentum_5d": _num(indicators.calc_momentum_5d(kline)),
        "volatility": _num(indicators.calc_volatility(raw_window)),
        "max_drawdown": _num(
            indicators.calc_max_drawdown(raw_window, dividends)
        ),
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
        turnover_wan=_num(indicators.calc_turnover_amount(kline)),
    )


def _sustainability(code, dividends, as_of, cache_dir, financial_cache_days):
    """分红可持续性红标：盈利下滑 / 经营现金流为负 / 分红超现金流 / 负债恶化。

    数据来自 ``get_financial_health``（带 cache_days 缓存）：盈利下滑按最新报告期
    （含中报/季报）净利润同比，现金流与分红覆盖率按最近年报口径；任一数据缺失
    只影响对应警示。多期序列、负债字段与最新报告期锚点**原样透传**给判定层
    （``scorer.evaluate_sustainability`` 消费），此处不判断任何阈值。
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
        "eps_history": health.get("eps_history"),
        "cash_yoy_history": health.get("cash_yoy_history"),
        "debt_ratio_yoy": health.get("debt_ratio_yoy"),
        "interest_cover": health.get("interest_cover"),
        "latest_period": health.get("latest_period"),
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
    return run_scan(config_dir, cache_dir, as_of)["report"]


def run_scan(
    config_dir: Path = Path("config"),
    cache_dir: Path = Path("cache"),
    as_of: date | None = None,
) -> dict:
    """跑一遍筛选，返回 ``{"report", "stock_results", "rules", "output"}``。

    比 :func:`run` 多带回交互式详情所需的原始结果（report 之外的部分不被消费）。
    """
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
    # 先按全量候选取行情（一次批量请求），据总市值把成分股裁到前 N，
    # 再跑逐股取数——裁剪省下的是最贵的部分。
    all_stocks = _scan_stocks(cfg.stocks, refresh["constituents"])
    spot = _fetch(
        lambda: data.get_stock_spot([stock.code for stock in all_stocks]),
        "get_stock_spot",
    )
    selection = _select_constituents(
        refresh["constituents"], spot, cfg.rules["data"]["candidate_top_n"]
    )
    _log_candidate_selection(selection, cfg.rules["data"]["candidate_top_n"])
    scan_stocks = _scan_stocks(cfg.stocks, selection["kept"])
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
    return {
        "report": reporter.render_report(
            stock_results,
            fund_results,
            cfg.output,
            time.monotonic() - started,
            market=market,
            risk_hints=cfg.rules["stocks"].get("risk_hints"),
        ),
        "stock_results": stock_results,
        "rules": cfg.rules,
        "output": cfg.output,
    }


def main(argv=None) -> int:
    """CLI 入口：打印报告；是交互式终端时接着进入详情会话；配置错误返回 1。"""
    parser = argparse.ArgumentParser(
        description="红利投资筛选：打印信号表；在交互式终端里可选中个股查看指标明细。"
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="报表打印后不进入交互式详情（管道/重定向时本就不会进入）",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        scan = run_scan()
    except config.ConfigError as exc:
        logger.error("配置加载失败：%s", exc)
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    print(scan["report"])
    if interactive.enabled(args.no_interactive):
        interactive.session(scan["stock_results"], scan["rules"]["stocks"], scan["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
