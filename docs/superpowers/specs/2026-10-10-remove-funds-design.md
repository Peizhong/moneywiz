# 移除基金逻辑 设计文档

> 日期：2026-10-10 | 状态：待审批

## 概述

工具只关注股票：删除 funds 基金相关的全部逻辑（配置、取数、指标、评分、渲染、编排、快照、测试、文档），保留股票流水线与板块温度计。

一个已经与用户确认的决策：

1. **历史快照保留原样。** `snapshots/` 下已提交的 JSON（schema 1）含有 `funds` 段，是当时的完整输出记录，不改写；新快照（schema 2）起不再含 `funds` 段。skill 对比分析时旧档的基金条目自然消失，SKILL.md 注明这一不连续点。

---

## 关键口径决策

1. **彻底删除，不做「停用保留」。** 用户要求「去掉基金相关逻辑」，保留死代码与「只关注股票」的意图相悖；git 历史随时可回退，无需在代码里留后路。

2. **`calc_index_pe_position` 与 `get_index_pe_history` 保留——它们是股票侧板块温度计的在用依赖，不是基金专属。** 板块温度计（`main._market_snapshot`）用 `get_index_pe_history("上证红利")` + `calc_index_pe_position` 计算上证红利 PE 分位；基金指标 `index_pe_vs_history` 只是复用者。删基金时这两者不动，`calc_index_pe_position` 从 indicators 的「基金指标」段移出，改为通用/市场指标注释。

3. **`resolve_index_symbol` 是基金专属，删除。** 它只在 `main._fund_result` 里解析基金配置的 `index` 字段（支持 PE 分位的指数名），股票侧无调用方。若 `_clean_str`/`_strip_tracker_suffix` 仅被它使用则一并删（实现时确认）。

4. **快照 JSON 升 schema 版本：1 → 2。** 删除 `funds` 段是字段变更，按 CLAUDE.md 约定必须升版本。历史文件保留 schema 1 原样；读者（skill/Claude）以「schema ≥ 2 无 funds 段」理解。

5. **`cache/` 不清理。** 基金取数缓存键（fund_quotes/fund_nav_history 等）按各自 TTL 自然过期（最长 7 天），cache 本身可再生，不值得为一次性删除写迁移逻辑。

6. **`docs/superpowers/` 下历史 spec/plan 不改。** 它们是当时设计过程的记录（含基金设计），不是现状文档；现状文档（README/CLAUDE.md/SKILL.md）才需要同步。

7. **股票侧口径词恢复固定。** reporter 的 `_position_phrases` 目前按股票/基金传不同口径词（股票「全收益」、基金空串）；删基金后统一固定为「全收益」，删除基金空串分支。股票的位置口径不变。

---

## 模块详细设计

### 配置层

- **删除** `config/funds.yaml`。
- **`config/rules.yaml`**：删除 `funds:` 段（`discount_rate` 25 / `nav_trend_20d` 25 / `index_pe_vs_history` 25 / `dividend_frequency` 15 / `fund_size` 10）。`stocks:`、`risk_hints:`、`data:`、`output:` 不动。
- **`src/config.py`**：
  - 删 `FundCfg`（及 `FUND_TYPES` 枚举）、`_parse_funds`、`FILENAMES["funds"]`；
  - `Config` 去掉 `funds` 字段；
  - `_parse_rules` 的 `for section in ("stocks", "funds")` 改为只校验 `stocks`。

### 取数层 `src/data.py`

- 删 11 个基金函数：`get_fund_quotes` / `_fetch_fund_quotes` / `_tencent_fund_quotes`、`get_fund_nav_history` / `_fetch_fund_nav_history`、`get_fund_latest_nav` / `_fetch_fund_latest_nav`、`get_fund_dividend_history` / `_fetch_fund_dividend_history`、`get_fund_overview` / `_fetch_fund_overview`、`resolve_index_symbol`（决策 3）。
- 模块 docstring 去掉基金相关条目；缓存档位表注释若提及基金示例则同步改。
- 熔断/缓存/腾讯行情等股票在用的基础设施一律不动。

### 指标层 `src/indicators.py`

- 删基金段：`calc_fund_discount`、`calc_nav_trend`、`calc_dividend_frequency`、`calc_fund_size`（含段首「基金指标」注释）。
- `calc_index_pe_position` 保留，移出基金段（决策 2）。

### 评分层 `src/scorer.py`

- 删 `"fund_size": _at_least_min` 注册项与基金档位常量（`NAV_TREND_TH`、`FREQUENCY_TH`、`INDEX_PE_TH`、`DISCOUNT_TH` 等，实现时按注册表引用逐一确认）。

### 渲染层 `src/reporter.py`

- 删 `FUND_HEADER`、基金表渲染分支；`render_report` 签名去掉 `fund_results` 参数。
- 摘要行「扫描 N 只标的（股票 X / 基金 Y）」→「扫描 N 只股票」。
- `_position_phrases` 删基金空串分支，口径词固定「全收益」（决策 7）；`_signals` 里「仅 1 只基金」的注释改为股票场景描述或删除。
- `ranked_rows` 的基金包装调用删除（快照与交互列表只包装 `stock_results`）。

### 编排 `main.py`

- 删 `_fetch_fund_quotes`、`_fund_result` 及 `run()` 中基金取数/组装/渲染段、基金相关 import。
- `_market_snapshot`（板块温度计）与 `_select_constituents`（候选池裁剪）原样保留。
- `run_scan()` 返回值去掉 `"fund_results"` 键（快照与测试随动）。

### 快照 `src/snapshot.py`

- 删 JSON 的 `"funds": [...]` 段；`SNAPSHOT_SCHEMA` 1 → 2（决策 4）。
- 历史快照文件不改（概述决策 1）。

### 测试

- **删除整文件**：`tests/test_data_fund.py`、`tests/test_indicators_fund.py`。
- **清理基金用例/fixture**：`test_main.py`（基金行断言、`_patch_data` 的 `get_fund_*` 键、funds.yaml fixture 写入、`_row_for(report, "红利ETF")` 等）、`test_config.py`（funds 解析/校验用例）、`test_reporter.py`（基金表/摘要行用例）、`test_scorer.py`（基金档位用例）、`test_snapshot.py`（funds 段断言）。
- 板块温度计相关测试不动（依赖的 `get_index_pe_history`/`calc_index_pe_position` 保留）。

### 文档与 skill

- **README.md**：删 `config/funds.yaml` 章节、`funds.indicators` 权重说明、基金位置口径段落、基金数据源回退条目（`fund_etf_spot_em`/`fund_lof_spot_em`/`fund_open_fund_info_em`/`fund_overview_em`）。
- **CLAUDE.md**：删「ETF/LOF 行情 → 腾讯」「基金历史 → 单位净值走势」等回退链条目与基金口径描述（「该口径词只适用于股票」句可随基金表删除而简化）。
- **`.claude/skills/moneywiz/SKILL.md`**：description 的「红利股票/基金筛选结果」→「红利股票筛选结果」；注意事项补一条：历史快照（schema 1）含 `funds` 段、schema 2 起只有 `stocks`，对比时旧档的基金条目自然消失，不算数据缺失。

---

## 验证

1. `.venv/bin/pytest` 全绿（无基金用例残留、无孤儿 import）。
2. `.venv/bin/python main.py --no-interactive` 跑通，只出股票表 + 板块温度计，摘要行为「扫描 N 只股票」。
3. `.venv/bin/python snapshot.py` 生成当日快照：JSON `schema: 2`、无 `funds` 段、`stocks` 段完整。
4. `grep -rin "fund" --include="*.py" src/ main.py` 无基金残留（`get_index_pe_history` 的 docstring 等股票侧注释除外）。
