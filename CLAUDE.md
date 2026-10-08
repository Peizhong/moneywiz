# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

红利投资筛选工具：读取自选股票/基金与多个红利指数成分股，经 akshare/腾讯/新浪等数据源拉取行情、分红、年报数据，按 14 个指标（股票 9 + 基金 5）加权打分（总分另受分红可持续性红标扣分），终端输出排序信号表。代码与文档以中文为主（docstrings、日志、README、提交信息）。

设计与实现的原始文档在 `docs/superpowers/specs/` 与 `docs/superpowers/plans/`（含“关键口径决策”一节——改动打分/口径前先读它）。

## Commands

```bash
# 首次环境
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt

.venv/bin/python main.py       # 运行筛选（读 config/，写 cache/）；交互式终端里报表后可选中个股看详情
.venv/bin/python main.py --no-interactive   # 只打报表（管道/重定向/Docker 下本就不会进交互）
.venv/bin/pytest               # 全量测试（pytest.ini: pythonpath=., testpaths=tests）
.venv/bin/pytest tests/test_scorer.py -v                                    # 单文件
.venv/bin/pytest "tests/test_main.py::test_run_includes_market_header" -v   # 单用例
```

一律使用 `.venv/bin/pytest`（系统环境没有安装依赖）。

## Architecture

`config/*.yaml` → `src/config.py` 校验 → `main.py` 编排 → 每只标的：`src/data.py` 取数 → `src/indicators.py` 纯计算 → `src/scorer.py` 加权 → `src/reporter.py` 渲染。

- **src/data.py** — 唯一允许 `import akshare` 的模块。所有取数经 `_call()`（异常 → warning 含数据源名 + 重试 1 次 → None）。输出规范 schema（canonical 英文列）：行情 `code,name,price,pe,pb,market_cap`（market_cap 为总市值、单位**亿元**：东财源是元需 `/1e8`，腾讯源本就是亿元；spot 缓存键带 `v2` 前缀，改 schema 时须连带升版本，否则旧帧缺列会被当成「市值全缺失」）；K线/净值 `date,close`；分红 `date,dividend_per_share`；指数PE `date,pe`。**失败返回 None；成功但无数据返回带列的空 DataFrame**——下游 indicators 依赖这个区分（None = 不可评分，空 = 值为 0）。
- **src/scorer.py** — 指标名 → 档位函数的注册表（higher-better / two-sided / sweet-band / range / min 五类，档位 1.0/0.5/0.0）。缺失指标剔除后按剩余权重归一化 `total = Σ(score×w)/Σw×100`，全缺 → None。未知指标名告警并按不可评分处理。
- **src/reporter.py** — 消费 result dict 契约 `{code,name,total,values,scores,missing,tech,position,sustainability,sustainability_flags,turnover_wan,new_constituent}`。同分竞赛排名（显示 `1(并列6)`）、覆盖列（已评分/应有）、亮点/风险带数值、120 日位置短语、可持续性警示（**只渲染 `sustainability_flags` 的中文文案，不重判阈值**）、板块温度计首行、`(新增)` 成分股标记。
- **src/constituents.py** — 多指数成分股定期刷新：`config/dividend_index.yaml` 是 `indices` 列表，每个指数独立 `updated_at`；过期（`index_refresh_days`，默认 14 天）→ 从中证指数官网拉取 → 新成员写 `added` 字段并可标注、调出移除、**单指数失败沿用其旧名单**（不写时间戳，下轮重试），文件按当前内容整体重写。
- **src/interactive.py** — 报表后的交互式详情（唯一 `import questionary` 的模块）：`enabled()` 只在 stdout 是 TTY 且未传 `--no-interactive` 时放行（管道/Docker/CI 一律跳过，绝不阻塞），`session()` 循环「选择 → 打印详情 → **等一次按键** → 回列表」直到 Ctrl-C/Ctrl-Q（questionary 的 select **不绑 Esc**，别在提示里写）。详情后必须等按键：列表几十行，立刻重绘会把详情顶出屏幕；暂停走 `unsafe_ask`（`ask()` 会吞掉 KeyboardInterrupt 并同样返回 None，无法区分「按了键」与「用户中断」）。详情文本由 `reporter.render_detail`（纯函数）渲染；选择列表与表格的名次/信号同源于 `reporter.ranked_rows`，别在别处重写。
- **main.py 的候选池裁剪** — `_select_constituents()` 在逐股取数**之前**把成分股裁到 `data.candidate_top_n` 名（rules.yaml，默认 0 = 不筛）：各指数条目按代码去重 → 按 `market_cap`（总市值，亿元）降序取前 N → 合并自选（自选不参与筛选、不占名额）。裁剪只决定入选、不重排输出。**个股取不到市值按严格口径剔除并在日志列出代码；但 `limit<=0`、行情整表不可用（None/空帧/缺 `market_cap` 列）、或一只候选都排不出市值时不筛**（否则名单会被丢光、报告近乎空白）——改动这里的失败分支前先想清楚这条。

### 数据源回退与熔断（重要）

- 东财 `push2*.eastmoney.com` 会按出口 IP 拒绝请求（海外实测：TLS 握手成功后连接被断）。东财失败时的回退链：股票行情/K线 → 腾讯（`qt.gtimg.cn`、`web.ifzq.gtimg.cn` 前复权）→ **K 线再回退新浪**（`ak.stock_zh_a_daily`，前复权/不复权都走它；腾讯一次失败即跳过本轮 `_tencent_kline_down`，如 501 限流时避免逐只重试）；ETF/LOF 行情 → 腾讯（价格；无 IOPV，折溢价退化为「最新净值」日频代理）；基金历史 → 单位净值走势（`fund_open_fund_info_em`）；**行业 PE/PB → 新浪行业板块**（成分股自带 `per`/`pb`，首次全量扫描约 1-2 分钟，见缓存分层）。东财已不是任何指标的必需源。
- 东财任一行情调用失败 → 熔断 30 分钟（持久化），期间直接走回退源/按数据不足处理，不再重复重试。运行开始时 `reset_quote_source_state()` 读取持久化判定。
- 熔断在源码里的名字：`src/data.py` 的 `_eastmoney_quotes_down`；跳过点分散在各取数函数（搜索「已熔断」）。
- 回退提示一律经 `_log_fallback_once(key, level, msg, ...)`（每种消息一个 key）：熔断后逐只标的重复输出会刷屏，同类只保留首条（含首只代码），`reset_quote_source_state()` 时清空。新增回退日志不要用裸 `logger.*`。

### 缓存分层（改取数前必读）

| 缓存 | 存放 | 有效期 |
|---|---|---|
| 通用取数（行情/K线/分红/基金/指数PE/国债） | `cache/market_cache.db`（SQLite） | `market_cache_hours`（默认 24h） |
| 东财熔断判定 | 同上（键 `state:eastmoney_quotes_down`） | 30 分钟 |
| 行业 PE/PB | `cache/pe_cache.json` | `pe_cache_days`（7d） |
| 年报可持续性 | `cache/financial_cache.json` | `financial_cache_days`（30d） |
| 新浪行业反查表（代码→行业 + 行业中位数）| `cache/sina_industry.json` | `pe_cache_days`（7d，与行业 PE 共用）|
| 成分股名单（多指数，各自独立）| `config/dividend_index.yaml` 的 `updated_at` | `index_refresh_days`（14d） |

新增取数函数用 `_cached(key, fetch)` 包装：只缓存非 None 结果；键对 live 参数必须稳定（如 kline 用 `live` 而非日期）。`src/cache.py` 的 `Cache` 负责 SQLite 读写，并把 `read_json` 会把整数值浮点列推断成 int64 的问题还原为 float64。

## Testing

- **测试绝不联网**：conftest 的 `_no_real_network` 会默认把 `src.data.requests.get` 打桩为失败——任何未打桩的回退路径都会直接报错而不是真实联网；`ak.*`、`src.constituents.refresh_indices`（main 测试经 `_patch_data`）仍需各自 monkeypatch。fixture 使用真实上游的列名与字段位置（参见 `QUOTES_TEXT`/`KLINE_JSON` 的腾讯报文）。
- `tests/conftest.py` 的 autouse fixture 每个测试前后重置 data 层运行时状态（熔断标记 + 取数缓存）——新增模块级状态时要同步加入。
- 交互式测试（`tests/test_interactive.py`）全部打桩 `questionary.select`（脚本化的问答序列），不碰真实终端；CLI 用例显式传 `main.main([])`——`argv=None` 会去解析 pytest 自己的命令行。
- main 级测试把自选/规则/成分股写进 `tmp_path` 的临时 config 目录（fixture `config_dir`）；**不得依赖仓库 `config/` 的内容**（那是用户随时会改的数据，断言内容会让测试在正常使用中变红）。
- 性能基准（当前 3 个指数去重 130 只，rules.yaml `candidate_top_n: 80` 裁到 80 只后扫描；未裁剪时约 6 分钟）：冷启动数分钟（行业 PE 走新浪回退时全量扫描最慢）；暖缓存 ~2 秒（24h 缓存 + 持久化熔断生效）。

## Conventions

- 新增 akshare 调用一律写在 `src/data.py`；indicators/scorer/reporter/config/constituents 不得 import akshare。
- 配置校验集中在 `src/config.py`：错误信息必须含文件路径与字段名；指标权重总和必须为 100。
- `config/` 下是用户自己维护的数据（自选、基金、成分股），改动前先确认；`cache/` 是可再生的，可随意清理。
- 打分模型的方向性约定：股息率/估值越低分越高、超卖/折价越好；**波动率与最大回撤越低越好（权重 15/5，取近 250 根不复权 K 线，与股息率分位共用取数；回撤按含分红总回报口径）**；位置与流动性不参与打分——位置只并入亮点（低位）/风险（高位）文字，但**高位（120 日分位 ≥70%）会取消「买入」资格、名额按名次顺延**（`reporter._signals`，唯一影响信号分配的呈现规则）；流动性只标注（`risk_hints.turnover_low`）。分红可持续性红标**参与打分**：在加权总分上按项扣分（**四维度**、维度内只取最重一条，见 rules.yaml `stocks.sustainability`：利润维度最新报告期净利润同比下滑 -5% / 近 4 期 ≥3 期为负 -15%；现金流维度经营现金流为负 -15% / 同季同比单期下滑 -5% / 连续恶化 -15%；覆盖维度分红超现金流 -10%；负债维度资产负债率同季同比跳升 ≥10pp / 利息保障 <2 倍 -10%；合计**封顶 -45%**。盈利下滑判据 = **最新报告期**净利润同比，含中报/季报；现金流绝对水平与分红覆盖率 = 最近年报口径）。红标判定的唯一来源是 `scorer.evaluate_sustainability`——扣分与展示都消费它（result 的 `sustainability_flags`），**reporter 不重判阈值**；分红趋势是正式打分指标（权重 10）。口径细节见 plan 的「关键口径决策」与 README「输出说明」。
- 提交信息末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
