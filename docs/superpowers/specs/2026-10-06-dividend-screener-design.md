# 红利投资筛选工具 设计文档

> 日期：2026-10-06 | 状态：待审批

## 概述

一款基于 Python 的命令行投资辅助工具。用户维护自选股票和基金列表，运行脚本后自动拉取行情、分红、估值数据，按红利策略多因子加权打分，终端输出按总分排序的买卖信号。

- **数据源**：akshare（免费，无需注册）
- **使用频率**：每天 < 5 次
- **标的规模**：不超过 50 只股票 + 基金

---

## 模块架构

```
money/
├── config/
│   ├── stocks.yaml         # 自选股票列表
│   ├── funds.yaml          # 自选基金列表
│   └── rules.yaml          # 指标规则、权重、参数阈值
├── src/
│   ├── __init__.py
│   ├── data.py             # akshare 数据获取
│   ├── indicators.py       # 技术指标与红利指标计算
│   ├── scorer.py           # 加权打分引擎
│   └── reporter.py         # 终端表格输出
├── cache/                  # PE 行业均值缓存（7天有效）
├── main.py                 # 入口脚本
└── requirements.txt
```

**数据流**：`config/*.yaml` → `main.py` 遍历自选列表 → `data.py` 从 akshare 拉数据 → `indicators.py` 计算各维度指标 → `scorer.py` 加权汇总（满分 100） → `reporter.py` 终端表格输出，按总分降序排列。

---

## 配置文件设计

### stocks.yaml

```yaml
stocks:
  - code: "000001"
    name: "平安银行"
  - code: "600036"
    name: "招商银行"
```

### funds.yaml

```yaml
funds:
  - code: "510880"
    name: "红利ETF"
    type: etf      # etf | lof | normal
```

### rules.yaml

```yaml
# 股票评分指标权重与阈值（权重总和 100）
stocks:
  indicators:
    dividend_yield:
      weight: 30
      thresholds: { high: 4.0, mid: 2.0 }
    dividend_years:
      weight: 20
      thresholds: { high: 5, mid: 3 }
    payout_ratio:
      weight: 10
      thresholds: { min: 20, max: 70 }
    pe_vs_industry:
      weight: 15
      thresholds: { discount: -30, premium: 30 }
    pb_vs_industry:
      weight: 10
      thresholds: { discount: -30, premium: 30 }
    ma60_position:
      weight: 10
      thresholds: { sweet_low: -5, sweet_high: 5, max_deviation: 20 }
    momentum_5d:
      weight: 5
      thresholds: { oversold: -10, overbought: 10 }

# 基金评分指标权重与阈值（权重总和 100）
funds:
  indicators:
    discount_rate:
      weight: 25
      thresholds: { discount: -1, premium: 1 }
    nav_trend_20d:
      weight: 25
      thresholds: { pullback: -10, rally: 5 }
    index_pe_vs_history:
      weight: 25
      thresholds: { low: 30, high: 70 }
    dividend_frequency:
      weight: 15
      thresholds: { high: 2, mid: 1 }
    fund_size:
      weight: 10
      thresholds: { min: 1 }

# 数据拉取参数
data:
  kline_days: 120
  pe_cache_days: 7
```

---

## 模块详细设计

### data.py — 数据获取层

封装 akshare 接口，所有函数返回 pandas DataFrame。单只标的获取失败时记录日志并返回空 DataFrame，不中断全局流程。

| 函数 | akshare 接口 | 说明 |
|------|-------------|------|
| `get_realtime_quotes()` | `stock_zh_a_spot_em()` | 全市场行情一次性拉取，按自选筛选 |
| `get_kline(code, days=120)` | `stock_zh_a_hist()` | 单只日K线，含 OHLCV + 换手率 |
| `get_dividend_history(code)` | `stock_dividents_detail()` | 分红明细 |
| `get_industry_pe_pb(code)` | `stock_individual_info_em()` + 缓存 | PE/PB 与行业均值对比，缓存 7 天 |
| `get_fund_quotes(market)` | `fund_etf_spot_em()` / `fund_lof_spot_em()` | ETF/LOF 行情 |
| `get_fund_nav_history(code)` | `fund_etf_hist_em()` | 基金净值历史 |

**缓存策略**：PE/PB 行业均值数据缓存 7 天（`cache/pe_cache.json`），避免每次重复拉取。其他行情/K线/分红数据每次实时获取。

### indicators.py — 指标计算层

接收 data 层数据，输出各维度指标值。

**股票指标：**

| 函数 | 输入 | 输出 | 计算逻辑 |
|------|------|------|---------|
| `calc_dividend_yield(dividend_data, price)` | 分红明细 + 当前价 | 股息率 % | 最近一次每股分红 / 当前股价 × 100 |
| `calc_dividend_years(dividend_data)` | 分红明细 | 连续分红年数 | 统计连续分红年份数 |
| `calc_payout_ratio(dividend_data, pe)` | 分红 + PE | 派息率 % | 每股分红 / 每股收益 × 100 |
| `calc_pe_vs_industry(stock_pe, industry_pe)` | 个股PE + 行业均值PE | 偏离 % | (个股PE - 行业PE) / 行业PE × 100 |
| `calc_pb_vs_industry(stock_pb, industry_pb)` | 个股PB + 行业均值PB | 偏离 % | (个股PB - 行业PB) / 行业PB × 100 |
| `calc_ma60_position(close, ma60)` | 收盘价 + 60日均线 | 偏离 % | (收盘价 - MA60) / MA60 × 100 |
| `calc_momentum_5d(kline)` | 5日K线 | 涨跌幅 % | (今日收 - 5日前收) / 5日前收 × 100 |
| `calc_macd(kline)` | K线 | MACD信号 | DIF/DEA/柱状线，金叉/死叉信号 |
| `calc_rsi(kline, period=14)` | K线 | RSI值 | 标准 RSI 计算 |

**基金指标：**

| 函数 | 输入 | 输出 | 计算逻辑 |
|------|------|------|---------|
| `calc_fund_discount(fund_quote)` | 基金行情 | 折溢价率 % | (现价 - 净值) / 净值 × 100 |
| `calc_nav_trend(nav_history, days=20)` | 净值历史 | 变化 % | (今日净值 - 20日前净值) / 20日前净值 × 100 |
| `calc_index_pe_position(index_code)` | 对应指数代码 | PE 近似分位 | 基于指数当前 PE 与历史区间对比 |
| `calc_dividend_frequency(fund_dividend_data)` | 基金分红记录 | 年分红次数 | 统计年度分红次数 |
| `calc_fund_size(fund_quote)` | 基金行情 | 规模(亿) | 基金资产净值 |

### scorer.py — 加权打分引擎

输入 indicators 层的指标值 + rules.yaml 的权重/阈值，输出 0-100 分。

**打分逻辑**：每个指标根据其阈值区间映射为 0-1 的归一化得分，乘以权重（百分比），累加得总分。

```
score = Σ ( normalize(indicator_value, thresholds) × weight )  for all indicators
```

- `normalize` 函数将指标值映射到 0-1 区间
  - 区间内（如股息率 > 4%）= 1.0
  - 中间档（如股息率 2-4%）= 0.5
  - 区间外（如股息率 < 2%）= 0.0
- 反向指标（如 PE 偏离 > 30%）= 越远离越低分
- 单只标的返回 `{total_score, dimension_scores: {...}}`

### reporter.py — 输出层

使用 `tabulate` 库生成对齐表格。输出分三块：

1. **股票信号表格**（按总分降序，前 N 为买入建议，末尾列回避）
2. **基金信号表格**（同上）
3. **摘要行**：扫描数量、数据缺失数量、耗时

每行包含：排名、代码、名称、总分、各维度得分简要、信号文字（自动生成：高分维度 = 亮点，低分维度 = 风险提示）。

---

## 错误处理

- **数据缺失**：单只标的获取失败 → 日志记录，总分显示 N/A，不中断全局
- **网络超时**：akshare 默认超时 30s，失败重试 1 次
- **配置校验**：启动时检查 YAML 格式与必填字段
- **缓存过期**：PE 缓存按 7 天自动判定，过期则重新拉取
- **akshare 上游变更**：接口不可用时打印明确错误信息，指向具体函数

---

## 依赖

```
akshare>=1.14.0
pandas>=2.0.0
pyyaml>=6.0
tabulate>=0.9.0
numpy>=1.24.0
```

---

## 不纳入范围

- 持仓盈亏计算（本期不做）
- 通知/告警推送（本期不做）
- Web UI / GUI 界面（本期不做）
- 数据持久化 / 数据库（本期不做）
- 回测功能（本期不做）