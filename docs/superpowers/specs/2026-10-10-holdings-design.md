# 持仓列表与成本价风险标注 设计文档

> 日期：2026-10-10 | 状态：待审批

## 概述

新增持仓列表：用户在 `config/holdings.yaml` 维护股票代码与每股成本价；工具运行时把持仓股并入扫描清单（与其他股票按代码去重）统一评估，并根据成本价在报告里标注浮亏风险。**持仓盈亏只做风险标注，不参与打分、不改变信号与排序**。

两个已经与用户确认的决策：

1. **风险作用层 = 只标注**：成本价是个人持仓状态而非公司质地，混入红利评分会污染排序；浮亏提示与流动性提示同一处理层级（`risk_hints`）。
2. **阈值 = 单阈值、默认 -10%、可配**：`rules.yaml` 的 `stocks.risk_hints.holding_loss`（负数表示亏损幅度），浮亏达到或超过该幅度才在风险列提示。

---

## 关键口径决策

1. **持仓并入股票表，不单开「持仓」区块或列。** 用户要求「与其他股票去重后评估」——一张表复用现有评分/排名/信号体系（`reporter.ranked_rows` 只此一处实现），持仓身份用名称标记 `(持仓)` 呈现，浮亏用风险列呈现。终端表格已 9 列，另加「持仓」列宽度代价高。

2. **持仓不参与候选池裁剪、不占名额——与自选同待遇。** `candidate_top_n` 裁剪只作用于成分股（自选不参与筛选、不占名额是既有语义）；持仓是「我手里的票」，即使不在任何红利指数也必须被评估，不得被裁剪掉。

3. **去重规则：自选名优先、成分股名其次、纯持仓股名称从行情表回填。** 扫描清单 = 自选 ∪ 成分股（裁剪后）∪ 持仓，按代码去重（自选优先、成分股其次、持仓补充）。纯持仓代码在 `_scan_stocks` 阶段没有名称来源，用行情表（spot）的 `name` 回填；行情缺失时名称显示代码。同一代码同时是自选/成分股时，该行照常携带 `holding`（成本与浮亏）。

4. **浮亏口径：`pnl_pct = (现价 - 成本) / 成本 × 100`，现价取 spot 的 `price`。** 行情缺失（该股不在行情表）→ `holding` 只带 `cost`、`pnl_pct` 为 None（有成本但无法估算浮亏，不提示）。**成本价原样使用**：分红摊薄/加减仓后的成本由用户自行维护，工具绝不自动调整。

5. **提示阈值语义：`pnl_pct <= holding_loss` 才提示**（`holding_loss` 为负数，默认 -10 即浮亏 10% 或更深）。提示文案「持仓浮亏 X%」（X 取 `pnl_pct` 绝对值，如「持仓浮亏 20%」）。浮盈或浅亏不提示——阈值以内不占用有限的风险列空间（最多 6 项）。

6. **风险列中的位置**：持仓浮亏提示属于 `_risk_hint_phrases` 组（与流动性提示同组），排在高位短语之前、零分档指标之后——即 红标 → 流动性/持仓提示 → 高位 → 零分档 的既有顺序不变，持仓提示追加在流动性提示之后。

7. **快照 JSON 升 schema：2 → 3。** result dict 新增 `holding` 字段后，快照的 `stocks[].item` 自动携带（`ranked_rows` 包装完整 result），但按 CLAUDE.md 约定改字段必须升版本。历史快照（schema 1/2）不动；skill 对比时旧档无 `holding` 字段，按「字段缺失」如实说明。

8. **成本价不参与交互式选择列表**（`row_label` 保持 名次/代码/名称/总分/信号），但详情视图（`render_detail`）头部追加持仓一行。

---

## 模块详细设计

### 配置层

- **新建 `config/holdings.yaml`**（带示例与注释，用户自行维护）：

```yaml
holdings:
  - code: "600036"   # 股票代码
    cost: 32.50      # 每股成本（元）
```

- **`src/config.py`**：
  - `FILENAMES` 增 `"holdings": "holdings.yaml"`；`load_config` 读四个 YAML（docstring「三个 YAML」→「四个 YAML」）。
  - 新增 dataclass `HoldingCfg(code: str, cost: float)`；`Config` 增 `holdings: list[HoldingCfg]`。
  - `_parse_holdings(path, data)`：`holdings` 段必须是列表；每项 code 非空字符串、cost 必须是**正数**（int/float 且 >0，拒绝 bool——bool 是 int 子类）；错误信息含文件路径与字段名（`holdings[i].code`/`holdings[i].cost`）。
- **`config/rules.yaml`**：`stocks.risk_hints` 增 `holding_loss: -10`（注释：持仓浮亏提示阈值 %，负数；与 `turnover_low` 并列）。`src/config.py` 的 `_parse_rules` 对 risk_hints 无逐键校验（现状即透传），不需改。

### 编排 `main.py`

- `_scan_stocks(watchlist, constituent_entries)` 扩展为 `_scan_stocks(watchlist, constituent_entries, holdings)`：持仓条目（`HoldingCfg` → 临时 `StockCfg(code=code, name=code)`）在自选、成分股之后并入（按现有 seen 去重），返回 `(stocks, holding_costs: dict[code, float])`；纯持仓股名称用 spot 的 `name` 回填（在 `run_scan` 已有 spot 之后做；spot 缺失则保持 code）。
- `_stock_result(stock, spot, ..., holding_cost=None)`：`holding_cost` 非 None 时计算 `pnl = (price - holding_cost) / holding_cost * 100`（price 为 None → pnl None），result dict 增：

```python
"holding": {"cost": holding_cost, "pnl_pct": pnl} if holding_cost is not None else None
```

- `run_scan`：`scan_stocks, holding_costs = _scan_stocks(cfg.stocks, selection["kept"], cfg.holdings)`；`_stock_result` 调用传 `holding_costs.get(stock.code)`。
- 裁剪不变：`_select_constituents` 只处理成分股（决策 2）。

### 渲染 `src/reporter.py`

- result 契约文档增 `holding` 字段：`{"cost": float, "pnl_pct": float | None} | None`。
- `_table` 名称标记：`name_text = f"{name}(持仓)"`（`item.get("holding")` 非 None 时）；与 `(新增)` 并列时顺序为 `(持仓)(新增)`。
- `_risk_hint_phrases(item, risk_hints)`：`holding_loss = float(hints.get("holding_loss", HOLDING_LOSS_DEFAULT))`（模块常量 `HOLDING_LOSS_DEFAULT = -10.0`，与 `TURNOVER_LOW_DEFAULT` 同模式）；`holding = item.get("holding")`，`holding and holding["pnl_pct"] is not None and holding["pnl_pct"] <= holding_loss` → `phrases.append(f"持仓浮亏 {abs(pnl):.0f}%")`（紧随流动性提示之后）。
- `render_detail` 头部：持仓时追加 `  持仓 成本 {cost:.2f} 浮亏 {pnl:+.1f}%`（pnl 为 None 时只显示成本；无持仓不追加）。
- 排序/信号/覆盖/总分路径零改动（决策 1）。

### 快照与 skill

- **`src/snapshot.py`**：`SNAPSHOT_SCHEMA = 3`；`write_snapshot` 无需其他改动（`holding` 随 item 自动进 JSON）。
- **`.claude/skills/moneywiz/SKILL.md`**：
  - 分析维度增：持仓浮亏变化（`item.holding.pnl_pct` 加深/缓解）、持仓名单进出（有/无 `item.holding`）；
  - 注意事项增：schema 3 起 `item` 含 `holding`；对比旧快照（schema ≤ 2）时 `holding` 缺失，如实说明为「历史快照无持仓数据」，不当成真实变化。

### 测试

- `tests/test_config.py`：holdings 解析（合法输入 → `HoldingCfg`）；cost 非正（0/负数）、code 缺失、cost 为 bool/字符串 → ConfigError 含路径与字段名；`test_load_real_project_config` 增 holdings 断言（不断言内容，只断言字段形状）；`_write_config` 增 holdings 参数与文件写入。
- `tests/test_main.py`：纯持仓股被评估（不在自选/成分股也出现且打分）；自选+持仓同码只出一行且带 `holding`；成分股+持仓同码带 `holding`；行情缺失时 `pnl_pct` 为 None；持仓不参与 candidate_top_n 裁剪（裁剪后持仓股仍在）；`holding` 值正确（成本 32.5、现价 26 → pnl -20.0）。
- `tests/test_reporter.py`：`(持仓)` 标记（含与 `(新增)` 并列）；浮亏 -10% 界内不提示/界外提示「持仓浮亏 20%」；浮盈不提示；`holding_loss` 覆盖（hints 传入不同阈值）；`render_detail` 持仓行与无持仓行为。
- `tests/test_snapshot.py`：schema == 3；`stocks[0]["item"]["holding"]` 存在且值正确；`_scan` fixture 增 `holding`。
- `tests/test_interactive.py` 不动（row_label 未变）。

### 文档

- **README.md**：新增 `### config/holdings.yaml — 持仓` 章节（字段、去重与裁剪豁免语义、成本价原样使用）；`risk_hints` 说明增 `holding_loss`；输出说明增 `(持仓)` 标记与「持仓浮亏」提示。
- **CLAUDE.md**：Architecture 段「三个 YAML」→「四个 YAML」并列出 holdings；reporter 契约增 `holding`；方向性约定段补一句持仓浮亏只标注不参与打分。
- **SKILL.md**：见上。

---

## 验证

1. `.venv/bin/pytest` 全绿。
2. `.venv/bin/python main.py --no-interactive`：持仓股出现在股票表、名称带 `(持仓)`、浮亏超阈值时风险列提示；非持仓行无标记。
3. `snapshot.py`（手动验证时注意：同日覆盖会重写当日快照——若当日快照需保留则跳过，schema 3 由 test_snapshot 覆盖）：JSON `schema: 3`、持仓 item 含 `holding`。
4. `grep -rn "holding" src/ main.py` 无残留（`holding` 为新增字段，全链一致）。

---

## 实施修订（2026-10-10）

1. **详情头标签「浮亏」→「盈亏」**：原设计对盈利持仓会渲染「浮亏 +5.0%」，自相矛盾。
   实现改为 `持仓 成本 {cost:.2f} 盈亏 {pnl:+.1f}%`（浮亏为负时仍如实显示亏损）。
2. **成本校验补 `math.isfinite`**：YAML 的 `.nan`/`.inf` 能通过「正数」校验
   （`cost <= 0` 对 NaN 为假），`_parse_holdings` 增加有限数判定。
