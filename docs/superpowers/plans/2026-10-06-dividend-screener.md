# 红利投资筛选工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按设计文档实现一个 Python 命令行工具：读取自选股票/基金配置，通过 akshare 拉取行情与分红数据，多因子加权打分（0-100），终端输出按总分排序的买卖信号表。

**Architecture:** 分层：`src/data.py`（唯一接触 akshare 的层，把上游中文列名归一化为稳定英文列）→ `src/indicators.py`（纯计算，输入 pandas 帧、输出指标值）→ `src/scorer.py`（阈值分档归一化 + 加权汇总）→ `src/reporter.py`（tabulate 表格输出）；`main.py` 负责编排、逐标错误隔离、计时。`src/config.py` 负责 YAML 加载与校验。

**Tech Stack:** Python 3.12（venv）、akshare 1.19.x（已验证）、pandas、pyyaml、tabulate、numpy、pytest（开发依赖）。

**Spec:** `docs/superpowers/specs/2026-10-06-dividend-screener-design.md`

## Global Constraints

- 依赖版本下限（照抄 spec）：`akshare>=1.14.0`、`pandas>=2.0.0`、`pyyaml>=6.0`、`tabulate>=0.9.0`、`numpy>=1.24.0`；开发依赖 `pytest>=8.0`。
- 数据源 akshare（免费、无需注册）；单次运行标的 ≤50；每天 <5 次；不做并发。
- 权重总和必须为 100（stocks、funds 各自）；总分 0-100。
- 不在范围内（不得实现）：持仓盈亏、通知推送、Web/GUI、数据持久化/数据库、回测。
- **所有 akshare 调用只允许出现在 `src/data.py`**；indicators/scorer/reporter/config 不得 import akshare。
- 单只标的获取失败：记日志、该标的显示 N/A，不中断全局（spec 错误处理）。
- 测试不得真实联网：所有 akshare 调用在测试中必须被 monkeypatch。
- 运行环境：项目根目录下 `.venv`；所有测试命令用 `.venv/bin/pytest`。

**数据层契约（fetch 失败 vs 空结果）**：失败返回 `None`（记 warning 日志）；成功但无数据返回空 DataFrame/空列表。indicators 据此区分“不可评分（None）”与“值为 0”。

**关键口径决策（对 spec 字面的细化，超出部分以实现为准）：**
1. 股息率 = 过去 12 个月**已实施**（进度=="实施"）每股分红合计 / 现价 × 100（TTM 口径，避免半年分红被低估；spec 原文“最近一次”）。
2. 派息率 = 股息率(%) × PE（由 D/E = D/P × P/E 推出；spec 表格输入写作“分红 + PE”，此处用已算出的股息率）。
3. 基金折溢价 canonical 值 = `(价格 - IOPV/净值) / IOPV/净值 × 100`（负值 = 折价 = 利好），阈值 `{discount:-1, premium:1}` 与之自洽；ETF 用行情表 `IOPV实时估值`，LOF 用最新单位净值，场外 normal 型无市价 → 该指标不可评分。
4. 打分档位统一为三档：1.0 / 0.5 / 0.0（与 spec 示例“区间内=1.0、中间档=0.5、区间外=0.0”一致），逐指标档位定义见 Task 5。

## Review Focus

以下 5 类输入/故障最可能伤人，各有一行；每行都已在对应 Task 中绑定测试：

1. **停牌/退市/新股不在行情表中**（spot 表缺该代码）：该标的仍按可得数据打分，缺失指标剔除后按权重归一化，绝不崩溃 → Task 10。
2. **无分红历史 / 分红数据获取失败**：分别对应 0.0 分 与 不可评分(None)，两者不得混淆 → Task 2、Task 6。
3. **数值字段返回 `"-"` 或空串**（PE/PB/IOPV）：data 层强制转 None → 指标不可评分，不得抛异常 → Task 6、Task 8。
4. **`cache/pe_cache.json` 损坏或旧格式**：视为空缓存直接重建，不得崩溃 → Task 7。
5. **基金跟踪指数不在 akshare 支持列表**（如“中证红利”不被 `stock_index_pe_lg` 支持）：指数估值指标不可评分并告警，整体继续 → Task 8。

---

### Task 1: 脚手架与配置加载

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `.gitignore`
- Create: `config/stocks.yaml`, `config/funds.yaml`, `config/rules.yaml`
- Create: `src/__init__.py`, `src/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `load_config(config_dir: Path = Path("config")) -> Config`；`Config(stocks: list[StockCfg], funds: list[FundCfg], rules: dict, output: dict)`；`StockCfg(code: str, name: str)`；`FundCfg(code: str, name: str, type: str, index: str | None = None)`；`ConfigError(Exception)`。`rules` 为 rules.yaml 原始 dict；`output` 默认 `{"buy_top_n": 5, "avoid_bottom_n": 5}`。

- [ ] **Step 1: 环境与文件**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` 照抄 spec 依赖（版本下限见 Global Constraints）；`requirements-dev.txt` 为 `pytest>=8.0`；`pytest.ini` 内容 `[pytest]` / `testpaths = tests` / `pythonpath = .`；`.gitignore` 含 `.venv/`、`__pycache__/`、`*.pyc`、`.pytest_cache/`、`cache/`。

`config/stocks.yaml`、`config/funds.yaml` 照抄 spec 示例，`funds.yaml` 每项增加可选字段 `index: "上证红利"`（用于指数估值分位；缺省时运行期自动识别跟踪标的，见 Task 8）。`config/rules.yaml` 照抄 spec 全文，另加：

```yaml
output:
  buy_top_n: 5
  avoid_bottom_n: 5
```

- [ ] **Step 2: 写失败测试** `tests/test_config.py`

用 `tmp_path` 写 YAML 文件后断言：正常加载（2 只股票、1 只基金、weights 合法）；缺 `code` → `ConfigError`；`type: "abc"` → `ConfigError`；stocks 权重和 = 95 → `ConfigError`；文件不存在 → `ConfigError`；无 `output` 段 → 默认 `buy_top_n=5`；`index` 字段缺省为 `None`。用项目真实 `config/` 目录加载一次，断言 `rules["data"]["pe_cache_days"] == 7`。

- [ ] **Step 3: 运行测试确认失败** — `.venv/bin/pytest tests/test_config.py -v`，期望 `ModuleNotFoundError: src.config`。

- [ ] **Step 4: 实现 `src/config.py`**

校验规则：三个文件存在且可解析；stocks/funds 每项必填字段齐全、`type ∈ {etf, lof, normal}`；`rules["stocks"]["indicators"]` 与 `rules["funds"]["indicators"]` 每项含 `weight:int` 与 `thresholds:dict` 且权重和 == 100；`rules["data"]` 缺省为 `{"kline_days": 120, "pe_cache_days": 7}`。错误信息必须包含文件路径与字段名。

- [ ] **Step 5: 运行测试确认通过，提交**

```bash
.venv/bin/pytest tests/test_config.py -v
git add -A && git commit -m "feat: project scaffold and config loading"
```

---

### Task 2: 股票分红与估值指标

**Files:**
- Create: `src/indicators.py`
- Test: `tests/test_indicators_dividend.py`

**Interfaces:**
- Consumes: data 层归一化后的分红帧（列：`date`（datetime64，升序）、`dividend_per_share`（税前每股，float））。
- Produces:
  - `calc_dividend_yield(dividend_data: DataFrame | None, price: float | None, as_of: date, window_days: int = 365) -> float | None`
  - `calc_dividend_years(dividend_data: DataFrame | None, as_of: date) -> int | None`（`None`=获取失败不可评分；空帧=0）
  - `calc_payout_ratio(dividend_yield: float | None, pe: float | None) -> float | None`
  - `calc_pe_vs_industry(stock_pe: float | None, industry_pe: float | None) -> float | None`
  - `calc_pb_vs_industry(stock_pb: float | None, industry_pb: float | None) -> float | None`

- [ ] **Step 1: 写失败测试** `tests/test_indicators_dividend.py`

fixture 分红帧（模拟招行：2026-07-10 派息 10.03 元/10 股、2026-01-16 派息 10.13 元/10 股、2024-05-01 派息 9.00 元/10 股）：
- `yield(price=40.0, as_of=date(2026,10,6)) == approx(5.04)`（(10.03+10.13)/10/40×100，TTM 口径）；
- 只有窗口外那条 → `0.0`；`dividend_data=None`（获取失败）→ `None`；`price=None` → `None`；
- `calc_dividend_years`：年份 {2026,2025,2024,2023,2020} → 4；最新为 2023 且 as_of=2026 → 0（断档）；空帧 → 0；`None`（获取失败）→ `None`；
- `calc_payout_ratio(5.04, 10.0) == approx(50.4)`；`pe=None`、`pe=0` → `None`；
- `calc_pe_vs_industry(10.0, 12.5) == approx(-20.0)`；`(15.0, 12.5) == approx(20.0)`；`industry_pe=None/0` → `None`；PB 同理。

- [ ] **Step 2: 运行测试确认失败** — `.venv/bin/pytest tests/test_indicators_dividend.py -v`。

- [ ] **Step 3: 实现**（`src/indicators.py`）

TTM 窗口：`date > pd.Timestamp(as_of) - pd.Timedelta(days=window_days)`。连续年数：从 `max(years)` 起逐年回数；若 `max(years) < as_of.year - 1` 直接返回 0。所有函数遇 None/空帧/0 分母按测试返回 None 或 0（与测试一致）。

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git add src/indicators.py tests/test_indicators_dividend.py && git commit -m "feat: stock dividend and valuation indicators"`

---

### Task 3: 技术指标（MA60 / 动量 / MACD / RSI）

**Files:**
- Modify: `src/indicators.py`
- Test: `tests/test_indicators_technical.py`

**Interfaces:**
- Consumes: K 线帧（列：`date`、`close`，升序）。
- Produces:
  - `calc_ma(kline: DataFrame | None, window: int = 60) -> float | None`（最后 window 根收盘均值；行数不足 → None）
  - `calc_ma60_position(close: float | None, ma60: float | None) -> float | None` — `(close-ma60)/ma60*100`
  - `calc_momentum_5d(kline: DataFrame | None) -> float | None` — `(close[-1]-close[-6])/close[-6]*100`，不足 6 行 → None
  - `calc_macd(kline: DataFrame | None) -> dict | None` — `{"dif","dea","hist","signal"}`，signal ∈ `{"金叉","死叉",""}`；不足 26 行 → None
  - `calc_rsi(kline: DataFrame | None, period: int = 14) -> float | None`；不足 period+1 行 → None

- [ ] **Step 1: 写失败测试** `tests/test_indicators_technical.py`

- MA60：59 个 10.0 + 1 个 11.0 → 在测试内计算 `ma=(59*10+11)/60`，断言 `calc_ma60_position(11.0, ma) == approx((11-ma)/ma*100)`；
- 动量：收盘序列 6 天前的 10.0、最新 11.0 → `approx(10.0)`；
- MACD：`[10.0]*30 + [20.0]`（长期走平后最后一根急涨）→ `signal=="金叉"` 且 `dif>dea>0`；`[10.0]*30 + [5.0]` → `"死叉"`；
- RSI：单调上行序列 → `approx(100.0)`；单调下行 → `approx(0.0)`；先涨后跌混合序列 → `0 < rsi < 100`；
- 各函数 `kline=None` 或行数不足 → `None`。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

MACD：`EMA(12)-EMA(26)=DIF`，`DEA=EMA(DIF,9)`，`hist=2*(DIF-DEA)`，全部用 `pandas.ewm(span=n, adjust=False, min_periods=n).mean()`；金叉/死叉 = 最后一根的 DIF-DEA 与上一根变号。RSI：Wilder 平滑，`ewm(alpha=1/period, adjust=False)`。

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git commit -m "feat: technical indicators (MA60, momentum, MACD, RSI)"`

---

### Task 4: 基金指标

**Files:**
- Modify: `src/indicators.py`
- Test: `tests/test_indicators_fund.py`

**Interfaces:**
- Produces:
  - `calc_fund_discount(price: float | None, nav: float | None) -> float | None` — `(price-nav)/nav*100`，负值=折价
  - `calc_nav_trend(history: DataFrame | None, days: int = 20) -> float | None` — 列 `date, close`；不足 days+1 行 → None
  - `calc_index_pe_position(pe_history: DataFrame | None, column: str = "pe") -> float | None` — `(last-min)/(max-min)*100`，截断 0-100；不足 2 行或 max==min → None
  - `calc_dividend_frequency(dividend_data: DataFrame | None, as_of: date, window_days: int = 365) -> int | None`（`None`=获取失败不可评分；空帧=0）
  - `calc_fund_size(scale: str | None) -> float | None` — 解析 `"222.76亿元（截止至：2026年06月30日）"` → `222.76`；`"5000万元（…）"` → `0.5`；`"---"`/None → `None`

- [ ] **Step 1: 写失败测试** `tests/test_indicators_fund.py`

断言：`calc_fund_discount(0.99, 1.0) == approx(-1.0)`、`calc_fund_discount(1.02, 1.0) == approx(2.0)`、nav 为 None/0 → None；净值序列 20 天前 1.0、最新 1.10 → `approx(10.0)`；PE 序列 [10,20,30,40] 最新 30 → `approx(66.67)`（三分位近似，`rel=1e-3`）；一年内 3 次分红 + 一年前 1 次 → 3；`None`（获取失败）→ `None`；规模解析三例 + None。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git commit -m "feat: fund indicators"`

---

### Task 5: 打分引擎（含每指标档位定义）

**Files:**
- Create: `src/scorer.py`
- Test: `tests/test_scorer.py`

**Interfaces:**
- Consumes: indicators 产出的 `values: dict[str, float | None]`；`rules_section = cfg.rules["stocks"]`（或 `["funds"]`），结构 `{"indicators": {name: {"weight": int, "thresholds": dict}}}`。
- Produces:
  - `normalize(indicator: str, value: float | None, thresholds: dict) -> float | None`
  - `score_instrument(values: dict[str, float | None], rules_section: dict) -> dict` → `{"scores": dict, "total": float | None, "missing": list[str]}`

**档位表（逐字实现，三档 1.0/0.5/0.0）：**

| 指标 | 键 | 规则 |
|---|---|---|
| dividend_yield / dividend_years / dividend_frequency | high, mid | `v>=high→1.0; v>=mid→0.5; else 0.0` |
| payout_ratio | min, max | `min<=v<=max→1.0; else 0.0` |
| pe_vs_industry / pb_vs_industry | discount, premium | `v<=discount→1.0; v<premium→0.5; else 0.0` |
| discount_rate | discount, premium | 同上 |
| nav_trend_20d | pullback, rally | 同上（越跌越优） |
| momentum_5d | oversold, overbought | 同上（超卖优） |
| ma60_position | sweet_low, sweet_high, max_deviation | `sweet_low<=v<=sweet_high→1.0; |v|<=max_deviation→0.5; else 0.0` |
| index_pe_vs_history | low, high | `v<=low→1.0; v<high→0.5; else 0.0` |
| fund_size | min | `v>=min→1.0; else 0.0` |

未知指标名（不在表内）：`logging.warning` 后按不可评分处理（不得抛异常）。

- [ ] **Step 1: 写失败测试** `tests/test_scorer.py`

用 spec 阈值逐档断言：`dividend_yield` {high:4.0, mid:2.0}：4.0→1.0、3.0→0.5、1.99→0.0；`payout_ratio` {20,70}：50→1.0、19→0.0、71→0.0；`pe_vs_industry` {-30,30}：-35→1.0、0→0.5、35→0.0；`ma60_position` {-5,5,20}：0→1.0、12→0.5、25→0.0；`index_pe_vs_history` {30,70}：20→1.0、50→0.5、80→0.0；`fund_size` {1}：0.5→0.0、2→1.0；`value=None` → `None`。

`score_instrument`：两指标权重 60/40、档位 1.0/0.5 → `total == 80.0`；一个指标缺失 → 分母改为实际参与权重（如 60/60 → 100.0）且 `missing` 含该指标；全部缺失 → `total is None`；未知指标名 → 出现 warning 且计入 missing（用 `caplog`）。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

`total = round(sum(s*w for scored)/sum(w for scored)*100, 1)`；无任何可评分指标 → None。

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git commit -m "feat: weighted scoring engine"`

---

### Task 6: 数据层 — 股票行情 / K 线 / 分红

**Files:**
- Create: `src/data.py`
- Test: `tests/test_data_stock.py`

**Interfaces:**
- Produces（失败一律 `None` + warning 日志；akshare 调用重试 1 次）：
  - `get_stock_spot() -> DataFrame | None` — 列 `code, name, price, pe, pb`；源 `stock_zh_a_spot_em()`（`代码/名称/最新价/市盈率-动态/市净率`）
  - `get_kline(code: str, days: int = 120, as_of: date | None = None) -> DataFrame | None` — 列 `date, close`；源 `stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")`，start_date = as_of − 2×days 自然日，取末 days 行
  - `get_dividend_history(code: str) -> DataFrame | None` — 列 `date, dividend_per_share`；源 `stock_history_dividend_detail(symbol=code, indicator="分红")`（`派息/进度/除权除息日`，派息单位为元/10 股）；仅保留 `进度=="实施"` 且除权除息日非空；`dividend_per_share = 派息/10`；按日期升序

- [ ] **Step 1: 写失败测试** `tests/test_data_stock.py`

monkeypatch `ak.stock_zh_a_spot_em` 等返回**用真实 akshare 列名**的原始帧：
- spot 中 `市盈率-动态` 为 `"-"`、`市净率` 为 None → 归一为 `None`；`最新价` 为浮点；
- kline：原始列 `日期/股票代码/开盘/收盘/…` → 输出仅 `date, close`；断言调用参数含 `adjust="qfq"` 与按 as_of 计算的 start_date；
- 分红：含一条 `进度=="预案"` 和一条除权除息日 NaT 的记录被剔除；`派息=10.03` → `dividend_per_share≈1.003`；
- 重试：伪造函数第一次 raise、第二次成功 → 被调用 2 次且结果正确；
- 伪造函数两次都 raise → 返回 `None` 且 `caplog` 含该 akshare 函数名。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

统一用模块内 `_call(ak_fn, *args, **kwargs)`：捕获异常 → warning（含 akshare 函数名）→ 重试 1 次 → 仍失败返回 None。数值列用 `pd.to_numeric(errors="coerce")`（`"-"` → NaN）。

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git commit -m "feat: stock data layer (spot, kline, dividends)"`

---

### Task 7: 数据层 — 行业 PE/PB 与 7 天缓存

**Files:**
- Modify: `src/data.py`
- Test: `tests/test_data_industry.py`

**Interfaces:**
- Produces: `get_industry_pe_pb(code: str, cache_dir: Path, cache_days: int = 7) -> dict | None` → `{"industry": str, "pe": float | None, "pb": float | None}`。
- 缓存文件 `cache_dir/"pe_cache.json"` 结构：`{"stocks": {code: {"industry": str, "updated_at": iso}}, "industries": {name: {"pe": float|None, "pb": float|None, "updated_at": iso}}}`，两者 TTL 均为 cache_days。
- 实现路径：`stock_individual_info_em(code)` 取 `行业`（item/value 两列）→ 缓存命中则复用；否则 `stock_board_industry_cons_em(symbol=行业)` 的成分股 `市盈率-动态`/`市净率` 剔除 NaN 与非正值后取中位数。

- [ ] **Step 1: 写失败测试** `tests/test_data_industry.py`

- 伪造成分股 `市盈率-动态=[5,7,None,-3]`、`市净率=[0.6,0.8,None,-1]` → `pe==6.0, pb==0.7`（中位数，剔除坏值）；
- 缓存：首次调用后文件生成；第二次调用 akshare 计数仍为 1（命中缓存）；
- 过期：手写 `updated_at` 为 8 天前 → 重新拉取；
- **损坏缓存**：写入非法 JSON 或 `{"stocks": "bad"}` → 不崩溃、视为空缓存重建；
- akshare 两次均失败 → `None`；行业名缺失 → `None`。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

`cache_dir.mkdir(parents=True, exist_ok=True)`；读缓存包 try/except JSONDecodeError 与 KeyError → 视为空。

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git commit -m "feat: industry PE/PB lookup with 7-day cache"`

---

### Task 8: 数据层 — 基金

**Files:**
- Modify: `src/data.py`
- Test: `tests/test_data_fund.py`

**Interfaces:**
- Produces（失败 `None`）：
  - `get_fund_quotes(fund_type: str) -> DataFrame | None` — `etf` 源 `fund_etf_spot_em()`、`lof` 源 `fund_lof_spot_em()`；列 `code, name, price, iopv`（ETF 的 IOPV 取 `IOPV实时估值`，LOF 无此列 → None）；`normal` 无行情表 → None
  - `get_fund_nav_history(code: str, fund_type: str, days: int = 120) -> DataFrame | None` — 列 `date, close`；etf→`fund_etf_hist_em`、lof→`fund_lof_hist_em`（`日期/收盘`），normal→`fund_open_fund_info_em(code, "单位净值走势")`（`净值日期/单位净值`）
  - `get_fund_latest_nav(code: str) -> float | None` — `fund_open_fund_info_em(code, "单位净值走势")` 末行单位净值
  - `get_fund_dividend_history(code: str) -> DataFrame | None` — 列 `date`；源 `fund_open_fund_info_em(code, "分红送配详情")`（`除息日`），升序；ETF 已验证同一接口可用
  - `get_fund_overview(code: str) -> dict | None` — `{"scale": str | None, "tracker": str | None}`（原始串，取自 `fund_overview_em` 的 `净资产规模`/`跟踪标的`）
  - `resolve_index_symbol(configured: str | None, tracker: str | None) -> str | None` — 优先 configured；否则 tracker 去掉 `指数/全收益` 后缀后校验是否 ∈ 支持集，不支持（如“中证红利”）或 `"该基金无跟踪标的"` → None + warning
  - 模块常量 `SUPPORTED_INDEX_PE = {"上证50","沪深300","上证380","创业板50","中证500","上证180","深证红利","深证100","中证1000","上证红利","中证100","中证800"}`
  - `get_index_pe_history(index_symbol: str) -> DataFrame | None` — 列 `date, pe`；源 `stock_index_pe_lg(symbol)` 的 `滚动市盈率`

- [ ] **Step 1: 写失败测试** `tests/test_data_fund.py`

用真实 akshare 列名的原始帧 monkeypatch：ETF 行情 `IOPV实时估值="-"` → None；ETF/LOF 历史列名归一 `date, close`；normal 走净值走势；分红 `每10份分红="每10份派现金0.0050元"` 只取 `除息日`；overview 解析出 `scale/tracker`；`resolve_index_symbol` 五例：`(None,"上证红利指数")→"上证红利"`、`(None,"该基金无跟踪标的")→None`、`(None,"中证红利指数")→None`（不支持+warning）、`("沪深300",None)→"沪深300"`、`("中证红利",None)→None`（配置了不支持项也告警）；各函数失败 → None。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git commit -m "feat: fund data layer"`

---

### Task 9: 输出层（reporter）

**Files:**
- Create: `src/reporter.py`
- Test: `tests/test_reporter.py`

**Interfaces:**
- 输入 result dict（main 与 reporter 的契约）：
  `{"code": str, "name": str, "total": float | None, "values": dict, "scores": dict | None, "missing": list[str], "tech": {"macd": str, "rsi": float | None} | None}`（tech 仅股票有）
- Produces:
  - `signal_for(rank: int, total_count: int, buy_top_n: int, avoid_bottom_n: int, has_score: bool) -> str` — 无分 → `"数据不足"`；`rank<=buy_top_n` → `"买入"`；`rank>total_count-avoid_bottom_n` → `"回避"`；否则 `"观察"`
  - `render_report(stock_results: list[dict], fund_results: list[dict], output_cfg: dict, elapsed_s: float) -> str`
- 中文标签映射（reporter 内常量）：dividend_yield=股息率、dividend_years=连续分红、payout_ratio=派息率、pe_vs_industry=PE估值、pb_vs_industry=PB估值、ma60_position=均线位置、momentum_5d=5日动量、discount_rate=折溢价、nav_trend_20d=净值趋势、index_pe_vs_history=指数估值、dividend_frequency=分红频率、fund_size=基金规模。
- 表格列（tabulate，`tablefmt="simple"`）：`排名 | 代码 | 名称 | 总分 | 信号 | 亮点 | 风险 | 技术`；亮点 = 档位 1.0 的指标（最多 2 个，`、` 连接，无则 `-`）；风险 = 档位 0.0 的指标（最多 2 个）；技术 = `MACD 金叉 RSI 56`（无则 `-`）。

- [ ] **Step 1: 写失败测试** `tests/test_reporter.py`

三只股票（总分 80、40、None）+ 一只基金：断言第一行是总分 80 者且含 `"买入"`；`total=None` 的标的显示 `"数据不足"` 且排最后；基金表标题出现；摘要行含 `"扫描 4 只标的（股票 3 / 基金 1）"`、`"数据不足 1"`、`"耗时"`。`signal_for` 边界用例：n=6, buy_top_n=5, avoid_bottom_n=5 → rank6="回避"、rank1="买入"；n=3, buy_top_n=5 → 全部 "买入"。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

排序：`total` 降序，None 最后（按 code 稳定排序）。

- [ ] **Step 4: 运行测试确认通过**

- [ ] **Step 5: 提交** — `git commit -m "feat: terminal reporter"`

---

### Task 10: 编排入口 main.py 与 README

**Files:**
- Create: `main.py`, `README.md`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `load_config`（Task 1）、`data.*`（Task 6–8）、`indicators.*`（Task 2–4）、`score_instrument`（Task 5）、`render_report`（Task 9）。
- Produces:
  - `run(config_dir: Path = Path("config"), cache_dir: Path = Path("cache"), as_of: date | None = None) -> str` — 返回完整报告文本（含摘要行）
  - `main() -> int` — 配置 `logging.basicConfig`（INFO），打印 `run()` 结果，返回 0
- 参数透传：`get_kline(code, days=rules["data"]["kline_days"])`、`get_industry_pe_pb(code, cache_dir, rules["data"]["pe_cache_days"])`。
- 股票 values 组装：`dividend_yield`（分红+spot 价）、`dividend_years`、`payout_ratio`、`pe_vs_industry`/`pb_vs_industry`（spot PE/PB + `get_industry_pe_pb`）、`ma60_position`（`calc_ma`+`calc_ma60_position`）、`momentum_5d`；`tech` = MACD/RSI。每个数据源独立 try/except：任一失败 → 对应指标置 None 并记 warning，其余照常。
- 基金 values 组装：`discount_rate`（etf：价格+IOPV；lof：价格+`get_fund_latest_nav`；normal：None）、`nav_trend_20d`、`index_pe_vs_history`（`resolve_index_symbol(fund.index, overview["tracker"])` → `get_index_pe_history` → `calc_index_pe_position`）、`dividend_frequency`、`fund_size`。

- [ ] **Step 1: 写失败测试** `tests/test_main.py`

monkeypatch 全部 `src.data.*`（持有 canonical 形状 fixture）：
- 正常路径：2 股票 1 基金 → 报告含两只股票名与基金名、基金表标题、摘要；股票按总分排序；
- **spot 表缺某只股票**（fixture 中无该 code）→ 该股票仍出现在表中（其余指标如 dividend_years 有效），不抛异常；
- 某只股票 `get_kline` raise → 该股票其余指标仍参与打分（总分非 None）；
- 全部数据源返回 None → 该标的 `数据不足`，`run()` 正常返回。

- [ ] **Step 2: 运行测试确认失败**

- [ ] **Step 3: 实现**

`main.py` 顶部 `from src import config, data, indicators, scorer, reporter`；`run()` 内部只做编排与容错，不写业务公式。`README.md`：环境安装（venv + requirements）、配置文件说明（三个 yaml）、`python main.py` 运行方式、输出含义（信号/亮点/风险）、已知限制（akshare 上游接口变更时错误信息指向具体函数；本沙箱 `push2.eastmoney.com` 被限制，行情类接口需在有网环境验证）。

- [ ] **Step 4: 运行测试确认通过** — `.venv/bin/pytest -v`（全套 10 个测试文件）。

- [ ] **Step 5: 提交** — `git commit -m "feat: main orchestration and README"`

---

## 执行顺序与验证

- 任务按 1→10 顺序执行，每个任务独立提交；Task 1 的提交是仓库首个提交。
- 最终验证：`.venv/bin/pytest -v` 全绿；`python main.py` 在可联网环境（akshare 全部接口可达）跑通。
- 注意：当前开发沙箱对 `push2.eastmoney.com`/`push2delay.eastmoney.com` 不可达（`stock_zh_a_spot_em`、`stock_zh_a_hist`、`fund_etf_spot_em`、行业板块接口会失败），`stock_history_dividend_detail`、`fund_open_fund_info_em`、`fund_overview_em`、`stock_index_pe_lg` 已验证可用。真实运行须在放行上述域名后验证；测试全部 mock，不受影响。
