# 移除基金逻辑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 funds 基金相关的全部逻辑（配置/取数/指标/评分/渲染/编排/快照/测试/文档），工具只处理股票。

**Architecture:** 按依赖顺序自顶向下删除：编排+渲染+快照 → 配置 → 数据层 → 指标层 → 评分层 → 文档。每个任务连同其测试一起改，每个任务结束时全量测试必须绿（删除任务的「测试」= 更新受影响的用例 + 删除基金专属用例）。

**Tech Stack:** Python 3 / pandas / pytest / tabulate。测试命令一律 `.venv/bin/pytest`（系统环境无依赖）。

**Spec:** `docs/superpowers/specs/2026-10-10-remove-funds-design.md`（本计划按它执行；口径决策以它为准）

## Global Constraints

- **彻底删除，不留停用代码**——不搞「保留但禁用」。
- **`calc_index_pe_position`（src/indicators.py）与 `get_index_pe_history`（src/data.py）必须保留**——板块温度计（`main._market_context`）在用，不是基金专属。
- **快照 JSON `SNAPSHOT_SCHEMA`：1 → 2**；历史快照文件（snapshots/ 下已提交内容）**一个字都不改**。
- **`cache/` 不清理**——基金旧缓存键按 TTL 自然过期（最长 7 天）。
- **`docs/superpowers/` 下历史 spec/plan 不改**（当时记录），只改 README.md / CLAUDE.md / `.claude/skills/moneywiz/SKILL.md`。
- **股票位置口径词固定「全收益」**；摘要行新格式：`扫描 {N} 只股票，数据不足 {X}，指标不全 {Y}，耗时 {Z}s`。
- `run_scan()` 返回值**去掉** `"fund_results"` 键。
- 提交信息中文，末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 每个任务的最后一步都是提交；提交前该任务测试必须全绿。

---

### Task 1: 编排/渲染/快照层移除基金

**Files:**
- Modify: `src/reporter.py`（基金表、基金口径词分支、摘要行、标签表）
- Modify: `main.py`（`_fetch_fund_quotes`、`_fund_result`、`_quote_row`、run_scan 基金段）
- Modify: `src/snapshot.py`（`funds` 段、schema 版本）
- Test: `tests/test_reporter.py`、`tests/test_main.py`、`tests/test_snapshot.py`

**Interfaces:**
- Consumes: 现状（本任务从零开始）
- Produces（后续任务依赖）:
  - `reporter.render_report(stock_results: list[dict], output_cfg: dict, elapsed_s: float, market: dict | None = None, risk_hints: dict | None = None, position_window: int = 120) -> str`——**第二个参数从 `fund_results` 变成 `output_cfg`**，全部调用点必须改。
  - `main.run_scan(...)` 返回 dict 键：`report, stock_results, market, elapsed, as_of, rules, output`（无 `fund_results`）。
  - `write_snapshot(scan, snapshot_dir)` 产出 JSON 含 `schema: 2`、`stocks` 段，**无 `funds` 段**。
  - 摘要行固定 `扫描 {N} 只股票，数据不足 {X}，指标不全 {Y}，耗时 {Z:.1f}s`。

- [ ] **Step 1: 改 `src/reporter.py`**

改动清单（按文件内顺序）：

1. 模块 docstring 第 1 行 `把打分结果渲染成股票表、基金表和摘要行` → `把打分结果渲染成股票表和摘要行`。
2. `INDICATOR_LABELS`（第 19-36 行）删除 5 个基金条目：`"discount_rate": "折溢价"`、`"nav_trend_20d": "净值趋势"`、`"index_pe_vs_history": "指数估值"`、`"dividend_frequency": "分红频率"`、`"fund_size": "基金规模"`。
3. `INDICATOR_VALUE_FORMATS`（第 39-56 行）删除同样 5 个键。
4. 删除 `FUND_HEADER = "【基金】"`（第 66 行）。
5. `_signals`（第 106 行注释）：`（如仅 1 只基金、avoid=5）` → `（如表内仅 1 只标的、avoid=5）`。
6. `render_report` 签名删除 `fund_results` 参数，改为：

```python
def render_report(
    stock_results: list[dict],
    output_cfg: dict,
    elapsed_s: float,
    market: dict | None = None,
    risk_hints: dict | None = None,
    position_window: int = 120,
) -> str:
    """渲染完整报告：板块温度计（可选）、股票表、摘要行，以换行连接。

    ``position_window`` 只用于位置短语的窗口文案（来自 ``data.kline_days``），
    带默认值以保证既有调用方无需改动。
    """
    total = len(stock_results)
    no_score = sum(1 for r in stock_results if r["total"] is None)
    partial = sum(
        1
        for r in stock_results
        if r["total"] is not None and (r.get("missing") or [])
    )
    summary = (
        f"扫描 {total} 只股票，数据不足 {no_score}，指标不全 {partial}，"
        f"耗时 {elapsed_s:.1f}s"
    )
    blocks = []
    market_text = _market_text(market) if market else ""
    if market_text:
        blocks.append(market_text)
    blocks.extend(
        [
            STOCK_HEADER,
            # 股票位置由前复权 K 线算出 → 全收益口径
            _table(
                stock_results,
                output_cfg,
                risk_hints,
                position_window,
                position_basis="全收益",
            ),
            summary,
        ]
    )
    return "\n".join(blocks)
```

7. `_table` docstring（第 198 行）`口径词随表而异——股票「全收益」、基金空串，见 ``_position_phrases``` → `口径词固定「全收益」，见 ``_position_phrases```。
8. `_position_phrases` docstring（第 535-544 行）删掉基金条目，保留股票解释：

```python
    ``basis`` 是口径词，固定「全收益」：``position`` 由**前复权** K 线算出，除息
    不是损失（钱以分红形式拿到），且与最大回撤的含分红总回报口径一致。这与波动率、
    最大回撤、股息率历史分位所用的**不复权**帧不同——四处取数分工是有意的，不是
    待统一的疏漏，理由见 plan 的「关键口径决策」第 5 条。
```

- [ ] **Step 2: 改 `main.py`**

1. 删除 `_quote_row`（第 183-189 行）——它只有 `_fund_result` 一个调用方（股票路径用 `_spot_row`），先 `grep -n "_quote_row" main.py` 确认调用点只有第 361 行一处。
2. 删除 `_fetch_fund_quotes`（第 304-315 行）整函数。
3. 删除 `_fund_result`（第 359-423 行）整函数。
4. `run_scan`：
   - docstring 改为 `返回 ``{"report", "stock_results", "market", "elapsed", "as_of", "rules", "output"}``。`
   - 删除第 479 行 `fund_quotes = _fetch_fund_quotes(cfg.funds)`。
   - 删除第 496-505 行的 `fund_results = [...]` 组装块。
   - `render_report(...)` 调用去掉第二个参数 `fund_results`。
   - 返回 dict 删除 `"fund_results": fund_results,` 行。

- [ ] **Step 3: 改 `src/snapshot.py`**

1. 第 21 行：`SNAPSHOT_SCHEMA = 1` → `SNAPSHOT_SCHEMA = 2`（注释保留）。
2. `write_snapshot` docstring（第 50 行）：`含 market/fund_results/elapsed/as_of 四个快照专用键` → `含 market/elapsed/as_of 三个快照专用键`。
3. payload 删除 `funds` 段（第 65-67 行）：

```python
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "as_of": day,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": _native(scan["elapsed"]),
        "market": _native(scan["market"]),
        "stocks": [
            _native(row) for row in ranked_rows(scan["stock_results"], scan["output"])
        ],
    }
```

- [ ] **Step 4: 改 `tests/test_reporter.py`**

1. 删除 `FUND_70`（第 66-73 行）与 `FUND_UNSCORED`（第 123-135 行）两个 fixture。
2. `_table_rows`（第 141 行）：`if stripped in (STOCK_HEADER, FUND_HEADER)` → `if stripped == STOCK_HEADER`。
3. 删除 `test_render_report_fund_table_and_summary`（第 219-229 行）整函数。
4. `test_render_report_unscored_coverage_is_zero_of_all`：调用改为 `render_report([STOCK_UNSCORED], OUTPUT_CFG, 0.0)`；删除 `fund_row` 两行断言；`数据不足 2` → `数据不足 1`。
5. `test_high_position_row_with_oversized_avoid_zone_stays_watch`：注释 `（如仅 1 只基金、avoid=5）` → `（如表内仅 1 只标的、avoid=5）`；`_result("510880", "红利ETF", ...)` 改 `_result("600036", "招商银行", ...)`。
6. `test_detail_falls_back_to_label_without_value`（第 728 行）：删除 `assert _detail_text({"fund_size": 1.0}, {"fund_size": "n/a"}, 1.0) == "基金规模"` 一行。
7. 删除 `test_fund_position_phrase_does_not_claim_total_return`（第 917-924 行）与 `test_stock_and_fund_tables_use_their_own_position_basis`（第 927 行起）两个整函数。
8. 其余所有 `render_report(X, Y, OUTPUT_CFG, t)` 双列表调用改为 `render_report(X, OUTPUT_CFG, t)`（`Y` 为 `[]` 或 `[FUND_70]` 等）。逐处搜索：`grep -n "render_report(" tests/test_reporter.py`，涉及的函数至少有 `test_render_report_partial_coverage_disclosed`、`test_render_report_full_coverage_has_no_partial_count`、`test_render_report_sorts_none_last_and_ties_by_code`、`test_ties_share_rank_and_boundary_signal`、`test_high_position_stock_not_marked_buy_and_slot_passes_down`、`test_high_position_extension_keeps_tie_group_whole`、`test_high_position_in_bottom_zone_still_marked_avoid`、`test_render_report_uses_output_cfg_thresholds`、`test_render_report_none_scores_render_dashes`、`test_render_report_empty_lists_render_placeholders`、`test_render_report_takes_position_window`。

- [ ] **Step 5: 改 `tests/test_main.py`**

注意：本任务**保留** `FUND_CODE`、`FUNDS_CONFIG`、`RULES_CONFIG` 里的 `"funds"` 段与 `config_dir` fixture 里的 `("funds.yaml", FUNDS_CONFIG)` 写入——config 层还没改（Task 2），删了它们本任务就红。基金取数替身则可以删（main 已不再调用）。

1. `DATA_FUNCTIONS`（第 86-90 行）删除 6 个键：`"get_fund_quotes"`、`"get_fund_nav_history"`、`"get_fund_latest_nav"`、`"get_fund_dividend_history"`、`"get_fund_overview"`、`"resolve_index_symbol"`。
2. 删除 fixture `FUND_QUOTES`（第 180-182 行）、`FUND_NAV`（第 183-188 行）、`FUND_DIVIDENDS`（第 189-191 行）。
3. `_happy_overrides`（第 231-238 行）删除 6 个基金键（`get_fund_quotes` 到 `resolve_index_symbol`）。
4. `test_run_happy_path_reports_all_instruments_sorted_by_total`：删除 `fund_rows = _section_rows(report, "【基金】")` 断言块（第 283-286 行）；摘要断言（第 288 行）改为 `assert "扫描 2 只股票，数据不足 0" in report`。
5. `test_run_kline_failure_keeps_other_indicators_scoring`（第 328 行）：`assert "平安银行" in report and "红利ETF" in report` → `assert "平安银行" in report`。
6. `test_run_surfaces_sustainability_warnings`：删除第 794-795 两行（`other = _row_for(report, "红利ETF")` 及其断言）。
7. `test_run_all_sources_failing_reports_insufficient_data`：`for name in ("平安银行", "招商银行", "红利ETF")` → `("平安银行", "招商银行")`；`"数据不足 3"` → `"数据不足 2"`。
8. `test_run_passes_data_rules_and_as_of_through`：删除 `calls` 里的 `"nav"/"quotes"/"symbol"` 键（第 915 行）、三个基金局部函数（第 925-935 行）、overrides 里的三个基金参数（第 942-944 行）、三行基金断言（第 955-957 行）。
9. 删除 `test_run_list_valued_index_degrades_instead_of_aborting`（第 960-996 行）整函数。
10. 删除 `test_run_etf_discount_falls_back_to_latest_nav`（第 1093-1108 行）整函数。
11. `test_run_scan_exposes_snapshot_fields`：docstring 改 `快照入口依赖 run_scan 返回的三个键：market/elapsed/as_of`；删除第 1122 行；加一行 `assert "fund_results" not in scan`。

- [ ] **Step 6: 改 `tests/test_snapshot.py`**

1. `_scan` fixture（第 20-45 行）：删除 `"fund_results": [],`；`"report"` 改为 `"扫描 1 只股票，数据不足 0，耗时 1.0s"`；docstring 里 `含快照专用四键` → `含快照专用三键`。
2. `test_write_snapshot_creates_json_and_txt`：`assert data["schema"] == 1` → `== 2`；`assert data["funds"] == []` → `assert "funds" not in data`。

- [ ] **Step 7: 运行测试确认全绿**

Run: `.venv/bin/pytest -q`
Expected: 全绿。若还有 `render_report` 旧签名调用点或 `_section_rows(report, "【基金】")` 残留，按失败信息逐处修正（本任务内所有调用点都应已覆盖）。

- [ ] **Step 8: 提交**

```bash
git add src/reporter.py main.py src/snapshot.py tests/test_reporter.py tests/test_main.py tests/test_snapshot.py
git commit -m "$(cat <<'EOF'
refactor: 移除基金编排/渲染/快照——报告只出股票表，摘要行改「扫描 N 只股票」，快照 schema 1→2

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 配置层移除基金

**Files:**
- Delete: `config/funds.yaml`
- Modify: `config/rules.yaml`（删除 `funds:` 段）
- Modify: `src/config.py`（`FundCfg`、`_parse_funds`、`FUND_TYPES`、`FILENAMES["funds"]`、`Config.funds`）
- Test: `tests/test_config.py`、`tests/test_main.py`（fixture 清理）

**Interfaces:**
- Consumes: Task 1 的新 `render_report`/`run_scan` 签名（测试不受影响）。
- Produces: `load_config()` 返回 `Config`，字段只有 `stocks, rules, output, indices`；`config/` 下只剩 `stocks.yaml`、`rules.yaml`、`dividend_index.yaml` 三个文件；`rules.yaml` 无 `funds` 段（校验只要求 `stocks.indicators` 权重和 100）。

- [ ] **Step 1: 删除 `config/funds.yaml`**

Run: `git rm config/funds.yaml`
Expected: 文件从工作区与暂存区移除（用户自选基金数据本身被移除——spec 已确认「去掉 funds 相关逻辑」）。

- [ ] **Step 2: 删 `config/rules.yaml` 的 `funds:` 段**

删除 `# 基金评分指标权重与阈值（权重总和 100）` 注释及其下 `funds:` 整段（`discount_rate`/`nav_trend_20d`/`index_pe_vs_history`/`dividend_frequency`/`fund_size` 五项，位于 `stocks.risk_hints` 之后、`# 数据拉取参数` 之前）。

- [ ] **Step 3: 改 `src/config.py`**

1. 删除第 15 行 `FUND_TYPES = {"etf", "lof", "normal"}`。
2. `DEFAULT_DATA` 注释（第 23-24 行）：`日频档：K线/净值/指数PE/指数股息率/国债` → `日频档：K线/指数PE/指数股息率/国债`；`慢变档：分红明细/基金分红/基金概况` → `慢变档：分红明细`。
3. `FILENAMES` 删除 `"funds": "funds.yaml",`。
4. 删除 `FundCfg` dataclass（第 47-52 行）。
5. `Config` dataclass 删除 `funds: list[FundCfg]` 字段。
6. `load_config`：docstring `四个 YAML（自选/基金/规则/指数成分股）` → `三个 YAML（自选/规则/指数成分股）`；删除 `funds = _parse_funds(...)` 行与 `Config(...)` 里的 `funds=funds,`。
7. 删除 `_parse_funds`（第 199-218 行）整函数。
8. `_parse_rules`（第 225-226 行）：

```python
    _validate_indicators(path, "stocks", data.get("stocks"))
```

- [ ] **Step 4: 改 `tests/test_config.py`**

1. 删除 `FundCfg` 导入（第 12 行）。
2. `RULES` fixture 删除 `"funds"` 块（第 26-33 行）。
3. 删除 `FUNDS` fixture（第 45-49 行）。
4. `_write_config`：签名去掉 `funds=FUNDS` 参数，删除 `("funds.yaml", funds),` 元组。
5. `test_load_valid_config`：删除 `cfg.funds == [...]` 断言（第 97-99 行）。
6. 删除 `test_invalid_fund_type_raises_config_error`（第 116-126 行）、`test_funds_weights_must_sum_to_100`（第 141-151 行）、`test_fund_index_defaults_to_none`（第 210-216 行）三个整函数。
7. `test_load_real_project_config`：删除第 230 行 `assert all(f.code and ... for f in cfg.funds)`。

- [ ] **Step 5: 改 `tests/test_main.py` 的配置 fixture**

1. 删除 `FUND_CODE = "510880"  # 红利ETF`（第 24 行）与 `FUNDS_CONFIG`（第 34-38 行）。
2. `RULES_CONFIG` 删除 `"funds"` 块（第 59-67 行）。
3. `config_dir` fixture（第 118 行）删除 `("funds.yaml", FUNDS_CONFIG),`。
4. 内联写配置的三个测试（`test_run_scores_dividend_trend` 第 659 行、`test_run_scores_dividend_yield_percentile` 第 700 行、`test_run_scores_stability_and_reports_liquidity_hint` 第 739 行）：删除各自的 `("funds.yaml", FUNDS_CONFIG),` 元组，并删除各自 rules dict 里的 `"funds"` 块（约第 645-650、686-692、725-731 行）。

- [ ] **Step 6: 运行测试确认全绿**

Run: `.venv/bin/pytest -q`
Expected: 全绿。若有用例仍引用 `funds.yaml`/`cfg.funds`/`FundCfg`，按失败信息删除。

- [ ] **Step 7: 提交**

```bash
git add config/funds.yaml config/rules.yaml src/config.py tests/test_config.py tests/test_main.py
git commit -m "$(cat <<'EOF'
refactor: 配置层移除基金——删 config/funds.yaml 与 rules.yaml funds 段，只校验股票权重

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 数据层删除基金取数函数

**Files:**
- Modify: `src/data.py`
- Delete: `tests/test_data_fund.py`

**Interfaces:**
- Consumes: Task 2（config 已无基金）。
- Produces: `src.data` 保留且只保留股票/市场取数：`get_stock_spot`、`get_kline`、`get_kline_raw`、`get_dividend_history`、`get_industry_pe_pb`、`get_index_pe_history`（**保留**）、`get_index_dividend_yield`、`get_10y_bond_yield`、`get_financial_health`、`get_index_constituents` 及全部私有 helper（`_tencent_symbol` 股票侧在用，**保留**）。

- [ ] **Step 1: 确认私有 helper 归属（防误删共享代码）**

Run: `grep -n "_clean_str\|_strip_tracker_suffix\|SUPPORTED_INDEX_PE\|_FUND_SPOT_FUNCTIONS\|_FUND_HIST_FUNCTIONS\|_quote_row" src/data.py main.py`
Expected: 全部命中仅落在基金函数内（`_clean_str`/`_strip_tracker_suffix` 只在 `get_fund_overview`/`resolve_index_symbol`；`SUPPORTED_INDEX_PE` 只在 `resolve_index_symbol` 与其常量定义；`_FUND_*` 只在基金行情/历史取数）。若出现其他调用点，停下确认后再删。

- [ ] **Step 2: 删除 `src/data.py` 的基金函数**

按块删除（行号为当前值，先 `grep -n "^def get_fund\|^def _fetch_fund\|^def _tencent_fund\|^def resolve_index_symbol\|^def _clean_str\|^def _strip_tracker_suffix\|^_FUND_\|^SUPPORTED_INDEX_PE" src/data.py` 复核）：

1. `_FUND_SPOT_FUNCTIONS` / `_FUND_HIST_FUNCTIONS`（约第 851-852 行）及其上方注释。
2. `get_fund_quotes` + `_fetch_fund_quotes` + `_tencent_fund_quotes`（约第 855-942 行）。
3. `get_fund_nav_history` + `_fetch_fund_nav_history`（约第 943-1021 行）。
4. `get_fund_latest_nav` + `_fetch_fund_latest_nav`（约第 1022-1037 行）。
5. `get_fund_dividend_history` + `_fetch_fund_dividend_history`（约第 1038-1062 行）。
6. `get_fund_overview` + `_fetch_fund_overview`（约第 1063-1090 行）。
7. `resolve_index_symbol`（约第 1091-1118 行）。
8. `_clean_str`（约第 1120-1126 行）、`_strip_tracker_suffix`（约第 1128-1137 行）。
9. `SUPPORTED_INDEX_PE` 常量（约第 137-150 行）及其上方注释行 `# stock_index_pe_lg 接受的指数名（"中证红利" 等不在其列，见 resolve_index_symbol）`。
10. 模块 docstring（约第 15-26 行）删除 6 条基金接口条目（`get_fund_quotes`/`get_fund_nav_history`/`get_fund_latest_nav`/`get_fund_dividend_history`/`get_fund_overview`/`resolve_index_symbol`），保留 `get_index_pe_history` 条目。

- [ ] **Step 3: 删除 `tests/test_data_fund.py`**

Run: `git rm tests/test_data_fund.py`
Expected: 文件移除。`SUPPORTED_INDEX_PE` 的断言（该文件第 395 行）随文件一并消失。

- [ ] **Step 4: 运行测试确认全绿**

Run: `.venv/bin/pytest -q`
Expected: 全绿（`tests/test_data_stock.py`、`test_data_market.py`、`test_data_industry.py` 不依赖被删函数）。

- [ ] **Step 5: 提交**

```bash
git add src/data.py tests/test_data_fund.py
git commit -m "$(cat <<'EOF'
refactor: 数据层删除基金取数——行情/净值/分红/概况/指数符号解析，保留 get_index_pe_history（温度计在用）

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 指标层删除基金指标函数

**Files:**
- Modify: `src/indicators.py`
- Delete: `tests/test_indicators_fund.py`

**Interfaces:**
- Consumes: Task 3（data 层已无基金）。
- Produces: `src.indicators` 保留 `calc_index_pe_position`（注释归入市场指标，供 `main._market_context` 用）；删除 `calc_fund_discount`、`calc_nav_trend`、`calc_dividend_frequency`、`calc_fund_size`。

- [ ] **Step 1: 删除基金指标段（约第 353-427 行）**

删除内容：
1. 段首注释 `# 基金指标（基金历史帧规范列：…）`（第 353-356 行）。
2. `calc_fund_discount`（第 358-365 行）。
3. `calc_nav_trend`（第 368-379 行）。
4. `calc_dividend_frequency`（第 400-413 行）。
5. `calc_fund_size`（第 415-427 行）。

**保留** `calc_index_pe_position`（第 382-398 行），并在其 docstring 上方补一行段注释：

```python
# 指数 PE 历史分位（板块温度计「上证红利 PE 分位」用；基金指标已移除）
```

- [ ] **Step 2: 删除 `tests/test_indicators_fund.py`**

Run: `git rm tests/test_indicators_fund.py`
Expected: 文件移除。

- [ ] **Step 3: 运行测试确认全绿**

Run: `.venv/bin/pytest -q`
Expected: 全绿。`calc_index_pe_position` 的间接覆盖仍在（`test_main.py::test_run_includes_market_header` 经 `INDEX_PE` fixture 断言「上证红利PE分位」）。

- [ ] **Step 4: 提交**

```bash
git add src/indicators.py tests/test_indicators_fund.py
git commit -m "$(cat <<'EOF'
refactor: 指标层删除基金指标函数，calc_index_pe_position 归入市场指标

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 评分层删除基金档位注册

**Files:**
- Modify: `src/scorer.py`
- Test: `tests/test_scorer.py`

**Interfaces:**
- Consumes: Task 4。
- Produces: `_TIERS` 只含股票指标：`dividend_yield`、`dividend_yield_percentile`、`dividend_years`、`volatility`、`max_drawdown`、`dividend_trend`、`payout_ratio`、`pe_vs_industry`、`pb_vs_industry`、`momentum_5d`、`ma60_position`。`_at_least_min` 函数删除（唯一使用者 `fund_size` 已删）。

- [ ] **Step 1: 改 `src/scorer.py`**

1. `_two_sided` docstring（第 32-34 行）删基金示例，改为：

```python
    用于 PE/PB 相对行业的折价（discount/premium）与动量超卖（oversold/overbought）。
```

2. `_TIERS` 删除 5 个条目：`"dividend_frequency"`（第 75 行）、`"discount_rate"`（第 80 行）、`"nav_trend_20d"`（第 81 行）、`"index_pe_vs_history"`（第 84 行）、`"fund_size"`（第 85 行）。
3. 删除 `_at_least_min` 函数（第 62-66 行）——先 `grep -n "_at_least_min" src/` 确认只剩定义与 `_TIERS` 注册两处。

- [ ] **Step 2: 改 `tests/test_scorer.py`**

1. 删除常量（第 34-38 行）：`DISCOUNT_TH`、`NAV_TREND_TH`、`INDEX_PE_TH`、`FREQUENCY_TH`、`FUND_SIZE_TH`。
2. `test_normalize_tier_boundaries` parametrize 删除基金行：`index_pe_vs_history` 3 行（第 57-59 行）、`fund_size` 2 行（第 61-62 行）、`dividend_frequency` 3 行（第 67-69 行）、`discount_rate` 3 行（第 73-75 行）、`nav_trend_20d` 3 行（第 76-78 行）。
3. `test_normalize_none_value_returns_none` parametrize 删除 `("fund_size", FUND_SIZE_TH),`（第 108 行）。

- [ ] **Step 3: 运行测试确认全绿**

Run: `.venv/bin/pytest -q`
Expected: 全绿。

- [ ] **Step 4: 提交**

```bash
git add src/scorer.py tests/test_scorer.py
git commit -m "$(cat <<'EOF'
refactor: 评分层删除基金指标档位注册与 _at_least_min

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 文档与 skill 同步

**Files:**
- Modify: `README.md`、`CLAUDE.md`、`.claude/skills/moneywiz/SKILL.md`
- 不动：`docs/superpowers/` 下所有历史 spec/plan（Global Constraints）

**Interfaces:**
- Consumes: Task 5 之后的最终行为（摘要行新格式、无基金表、schema 2 快照）。

- [ ] **Step 1: 改 `README.md`**

1. 第 3 行：`维护自选股票与基金列表` → `维护自选股票列表`。
2. 第 5 行：`在终端输出按总分降序排列的股票表、基金表和摘要` → `在终端输出按总分降序排列的股票表和摘要`。
3. 删除 `### config/funds.yaml — 自选基金` 整个小节（约第 59-73 行，从标题到下一节 `### config/rules.yaml` 之前），并在配置文件列表里同步去掉 funds.yaml 的提及。
4. 第 75 行：`` `stocks.indicators` / `funds.indicators` `` → `` `stocks.indicators` ``。
5. 缓存表两行（第 88-89 行）：`K 线、基金净值、指数 PE、指数股息率、10Y 国债` → `K 线、指数 PE、指数股息率、10Y 国债`；`分红明细、基金分红、基金概况` → `分红明细`。
6. 示例输出（约第 178-182 行）：删除 `【基金】` 表头与红利ETF 那一行；摘要行改成与新股式一致（如 `扫描 3 只股票，数据不足 0，指标不全 1，耗时 12.3s`——数字按示例股票表实际行数对齐）。
7. 第 196 行：`时如 `0/9`，基金为 `0/5`` → `时如 `0/9``。
8. 位置口径词段落（约第 246-253 行）：删除「**基金**「不加口径词」…」整条，保留股票「全收益」一条（该段开头若写「口径词股票与基金不同」改为「口径词固定」）。
9. 第 256 行：`摘要行：扫描标的数（股票/基金）、数据不足数…` → `摘要行：扫描股票数、数据不足数…`。
10. 数据源回退段（约第 281 行）删除 `- 基金历史 → fund_open_fund_info_em 的单位净值走势` 条目；数据源清单（约第 285-287 行）删除 `fund_etf_spot_em`/`fund_lof_spot_em`、`fund_open_fund_info_em`、`fund_overview_em` 提及。

- [ ] **Step 2: 改 `CLAUDE.md`**

1. 第 7 行：`读取自选股票/基金与多个红利指数成分股` → `读取自选股票与多个红利指数成分股`；`按 14 个指标（股票 9 + 基金 5）加权打分` → `按 9 个指标加权打分`。
2. 第 41 行：删除 `；ETF/LOF 行情 → 腾讯（价格；无 IOPV，折溢价退化为「最新净值」日频代理）；基金历史 → 单位净值走势（`fund_open_fund_info_em`）` 整段（保留前后股票/行业部分）。
3. 缓存分层表第 51 行：`（K线、基金净值、指数PE、指数股息率、10Y国债）` → `（K线、指数PE、指数股息率、10Y国债）`；第 52 行：`（分红明细、基金分红、基金概况）` → `（分红明细）`。
4. 第 61 行：`把行情（`spot`/`fund_quotes`/`fund_latest_nav`）挪出实时档则会让股息率、折溢价用上旧价格` → `把行情（`spot`）挪出实时档则会让股息率用上旧价格`。
5. 第 75 行：`（自选、基金、成分股）` → `（自选、成分股）`。
6. 第 76 行：删除 `**该口径词只适用于股票**——基金的位置来自 `get_fund_nav_history` 的**不复权**价格/净值序列（除息日同样下跌，非总回报），故基金短语**不加口径词**（`120日高位 78%`），照抄股票的「全收益」会是一句与序列不符的声明；` 整句（保留前面「位置取前复权（全收益）K 线…是有意的」的表述）。

- [ ] **Step 3: 改 `.claude/skills/moneywiz/SKILL.md`**

1. frontmatter `description`：`红利股票/基金筛选结果` → `红利股票筛选结果`。
2. 「注意事项」列表末尾加一条：

```markdown
- 历史快照（schema 1）含 `funds` 段、schema 2 起只有 `stocks`：对比新旧快照时
  旧档的基金条目会自然消失，不是数据缺失或脚本错误，如实说明即可；
```

- [ ] **Step 4: 残留检查**

Run: `grep -rin "fund\|基金" README.md CLAUDE.md .claude/skills/moneywiz/SKILL.md`
Expected: 无输出（「分红」不含这些词；如命中 `fund_open_fund_info_em` 等即漏改）。

- [ ] **Step 5: 提交**

```bash
git add README.md CLAUDE.md .claude/skills/moneywiz/SKILL.md
git commit -m "$(cat <<'EOF'
docs: README/CLAUDE.md/moneywiz skill 移除基金描述

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 全量验证

**Files:** 无改动（验证任务；发现问题则回到对应任务修复后再提交）。

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/pytest -q`
Expected: 全绿，无 skipped/failed。

- [ ] **Step 2: 源码残留检查**

Run: `grep -rin "fund" --include="*.py" src/ main.py; grep -rn "红利ETF\|510880" src/ main.py tests/`
Expected: 均无输出。

- [ ] **Step 3: 真实数据跑一遍报告**

Run: `.venv/bin/python main.py --no-interactive`
Expected: 输出含 `【板块温度计】` 与 `【股票】` 表，**无** `【基金】`，摘要行为 `扫描 N 只股票，数据不足 X，指标不全 Y，耗时 Zs`。注意：这是唯一一次真实取数运行（行业 PE 冷缓存可能耗时数分钟；API 偶发失败时报告仍会以降级数据渲染，属预期）。**不运行 `snapshot.py`**——`snapshots/2026-10-10.json`（schema 1、含基金）是当前唯一历史快照，同日覆盖会销毁这份基金记录，与「历史快照保留原样」的用户决策冲突；schema 2 快照写盘已由 `tests/test_snapshot.py` 覆盖。

- [ ] **Step 4: 收尾确认**

Run: `git status`
Expected: 工作区干净（7 个任务共 7 个提交全部落库，无未提交改动）。
