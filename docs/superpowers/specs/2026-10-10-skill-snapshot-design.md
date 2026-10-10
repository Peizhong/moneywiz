# 快照与历史分析 skill 化 设计文档

> 日期：2026-10-10 | 状态：待审批

## 概述

把 moneywiz 从「一次性的终端打分工具」扩展为「可按天留存结果、由 Claude 对比历史做分析的 skill」：

- 每运行一次 skill，生成一份**快照**（当日报告文本 + 结构化结果 JSON）；
- 快照按日期命名，**当天重复执行只保留最后一次**（同名覆盖）；
- skill 运行后由 **Claude 结合历史快照与当前数据生成分析**（分析由 Claude 生成，脚本只负责取数与写快照）。

四个已经与用户确认的决策：

1. **分析由 Claude 生成**：脚本只跑筛选、存快照、把快照材料递给 Claude；Claude 按 SKILL.md 的指引对比变化并生成自然语言中文分析。
2. **快照存 JSON + 文本双份**：`report.txt`（人读、Claude 快速抓全局）与 `results.json`（结构化数据、数值核对用）。
3. **历史全部保留**：每天最多一份、单份几 KB，无磁盘压力；分析时 Claude 自由选昨天/约 7 天前/约 30 天前作参照。
4. **skill 放项目内**：`.claude/skills/moneywiz/SKILL.md`，随 git 版本控制；脚本继续用项目自己的 `.venv`。

---

## 关键口径决策

1. **快照是消费 `run_scan()` 返回值的独立组件，不改 main.py 的编排。** 新增 `src/snapshot.py`（写快照的纯函数）与根目录薄 CLI `snapshot.py`（`run_scan()` → `write_snapshot()`）。快照不得复制任何渲染/排序/信号逻辑。

2. **快照里的股票/基金条目存 `reporter.ranked_rows()` 的输出，而非裸 `stock_results`。** 榜单进出与信号变化是历史分析的重点，而排名/并列/信号只允许 `ranked_rows` 这一处实现（CLAUDE.md 硬约束：「选择列表与表格的名次/信号同源于 `reporter.ranked_rows`，别在别处重写」）。快照写盘时调用 `ranked_rows` 包装结果，即 `[{"rank", "tied_count", "signal", "item"}, ...]`，item 为完整 result dict（含 `values`/`scores`/`missing`/`tech`/`position`/`sustainability_flags`/`new_constituent` 等归因所需全部字段）。

3. **`run_scan()` 返回值增加 `"market"`、`"elapsed"`、`"as_of"` 三个键。** 板块温度计（中证红利股息率/10Y 国债/利差/上证红利 PE 分位）是历史分析维度之一，但 `run_scan` 目前只把 `market` 传给 `render_report` 而不放进返回 dict；`elapsed` 与 `as_of` 同理（快照元信息需要）。给返回 dict 增加这三个键（新增键，向后兼容，现有消费方与测试不受影响），使 `write_snapshot` 的输入完全自包含。这是本设计中 main.py 的**唯一**改动；不做 `market` 这个则快照 CLI 要么重跑编排、要么从报告文本解析温度计数值——前者复制逻辑、后者违背决策 5。

4. **当天去重 = 文件名按天 + 原子覆盖。** 快照文件名为 `YYYY-MM-DD.json` / `YYYY-MM-DD.txt`，同日重复运行直接覆盖同名文件，无需清理逻辑。写盘用「先写临时文件再 `os.replace`」——写一半崩溃不留半截文件，坏快照会污染后续所有历史分析。日期取 `run_scan` 的 `as_of`（即本地当天），与报告内容一致。

5. **数值对比只从 JSON 取，不从 txt 表格解析。** txt 是给人看的渲染产物（数值已按各自单位格式化、亮点/风险列有截断），从表格解析数值容易错。这条同时写进 SKILL.md 的注意事项与快照测试的注释。

6. **快照序列化前把 numpy 标量归一为 Python 原生类型，NaN → None。** result dict 里的数值来自 pandas（`indicators` 各函数的返回值虽经 `main._num` 挡过 NaN，但 `sustainability` 里的 `eps_history`/`cash_yoy_history` 等透传字段可能仍是 numpy 类型），`json.dump` 遇 numpy 标量会崩。归一化只做类型转换，不改数值。

7. **取数失败照常写快照。** `run_scan` 内部已逐源降级；快照记录的是「当天的真实运行结果」，即使行情整表不可用、报告近乎空白也照写——Claude 分析时据 `missing`/空表如实说明「数据不足导致的变化」而非当成真实变化（这条同样写进 SKILL.md）。只有**配置错误**不写快照：CLI 退出码 1，与 `main()` 一致。

---

## 模块详细设计

### `src/snapshot.py`（新文件）

```python
def write_snapshot(scan: dict, snapshot_dir: Path) -> Path:
    """把 run_scan() 的结果写成 YYYY-MM-DD.{json,txt} 两份快照，同日覆盖。
    返回 JSON 文件路径。"""
```

- 依赖 `reporter.ranked_rows` 包装股票/基金条目（决策 2）；`market`、`elapsed`、`as_of` 从 `scan` 取；`created_at` 为写盘时刻的本地时间（`datetime.now().astimezone()`）。文件名用 `scan["as_of"]`（即 `run_scan` 的日期参数，缺省即 `date.today()`），与报告内容同源。
- JSON 结构（`schema: 1`，将来改字段必须升版本）：

```json
{
  "schema": 1,
  "as_of": "2026-10-10",
  "created_at": "2026-10-10T09:15:32+08:00",
  "elapsed_s": 123.4,
  "market": {"dividend_yield": 5.2, "bond_10y": 1.7, "spread": 3.5, "index_pe_position": 42.0},
  "stocks": [{"rank": 1, "tied_count": 1, "signal": "买入", "item": {...}}],
  "funds":  [{"rank": 1, "tied_count": 1, "signal": "买入", "item": {...}}]
}
```

- txt 文件 = `scan["report"]` 原文（与 `main.py` 打印的完全一致，含温度计、两张表、摘要行）。
- 写盘：`snapshot_dir` 不存在时创建；两个文件各自「临时文件 + `os.replace`」原子落盘。
- 归一化函数（模块私有）递归处理 dict/list，`np.float64/float32` → `float`、`np.int64` 等 → `int`、`np.nan` → `None`；`str`/`bool`/`None` 原样。

### `main.py`（唯一改动）

- `run_scan()` 返回 dict 增加 `"elapsed": time.monotonic() - started` 与 `"market": market` 两个键（`render_report` 现在消费的 elapsed 值抽成局部变量后同时进返回 dict）。新增键向后兼容，现有消费方（`run`、`main`、交互式测试）不受影响。

### `snapshot.py`（新文件，根目录，与 main.py 平级）

约 20 行的薄 CLI：

- `run_scan()` → `write_snapshot(scan, Path("snapshots"))` → 打印 JSON 快照路径与报告摘要行（`report` 的最后一行，即「扫描 N 只标的…耗时 Xs」）。
- 捕获 `config.ConfigError`：日志错误、退出码 1、不写快照（与 `main()` 一致）。
- 不进入交互式详情（skill 场景本就不是 TTY；详情查看由 Claude 按需读 JSON 数据用 `reporter.render_detail` 同源数据自行组织）。

### `.claude/skills/moneywiz/SKILL.md`（新文件）

frontmatter：

```yaml
---
name: moneywiz
description: 红利投资筛选快照与分析。运行筛选脚本生成当日快照（当天重复运行自动覆盖），并对比历史快照分析榜单、信号、分数与板块温度计的变化。当用户想查看红利股票/基金筛选结果、跟踪标的排名变化或做历史对比时使用。
---
```

正文指引（Claude 执行时阅读）：

1. **运行段**：执行 `.venv/bin/python snapshot.py`；快照落盘 `snapshots/YYYY-MM-DD.{json,txt}`（当天重复运行覆盖）；脚本打印快照路径与摘要行。
2. **分析段**（核心）：
   a. 列出 `snapshots/*.json`，选参照点：**昨日**（最近一份更早日期的快照）、**约 7 天前**最近一份、**约 30 天前**最近一份——存在才用，不存在跳过；
   b. 先读当前 txt + 参照 txt 抓全局变化，再回 JSON 核对具体数值；
   c. 分析维度：买入区新进/跌出（`buy_top_n` 名额内）、名次显著升降、信号变化（买入↔观察↔末位）及其归因、总分漂移显著的标的（对照 `values` 逐指标归因，总分变化阈值由 Claude 视样本自行把握，不硬编码）、板块温度计趋势（利差/指数 PE 分位）、红标（`sustainability_flags`）新增/消失、`new_constituent` 新成分股；
   d. 输出中文分析：变化摘要 + 值得注意的标的 + 带具体数字与快照日期的归因。
3. **注意事项段**：数值对比只从 JSON 取，不从 txt 表格解析；`missing` 数据不足导致的变化要如实说明、不当成真实变化；快照是当日最后一次运行；不修改 `config/` 与 `snapshots/` 下的历史文件。

### `.gitignore`

增加一行 `snapshots/`（历史快照是本地数据，不进版本控制）。

### 文档

- **README.md**：新增「快照与历史分析」一节——`snapshot.py` 用法、`snapshots/` 目录布局（`YYYY-MM-DD.{json,txt}`、当天覆盖）、skill 触发方式。
- **CLAUDE.md**：架构节补 `snapshot.py`（快照 CLI）入口与 `src/snapshot.py`（写快照）；缓存分层表下补一行快照目录说明（非缓存、不清理、当天覆盖、schema 版本号）；测试节提一句 `tests/test_snapshot.py`。

---

## 测试策略

遵循项目约定：**测试绝不联网**（conftest 已兜底）、一律 `.venv/bin/pytest`。新测试全部在 `tests/test_snapshot.py`：

- `write_snapshot` 用内联构造的 scan dict（含最小合法的 `report`/`stock_results`/`output`/`market`）：
  - 两个文件都生成、JSON 可解析、schema 字段齐全；
  - 连续写两次 → 目录内文件数不变、内容为第二次（**当天去重的核心断言**）；
  - `snapshot_dir` 不存在时自动创建；
  - numpy 标量归一化：`np.float64` → `float`、`np.nan` → `None`、嵌套 dict/list 递归生效、`None` 原样保留；
  - 条目经 `ranked_rows` 包装：断言 `stocks[0]` 含 `rank`/`tied_count`/`signal` 且 `item` 为原 result dict（钉住决策 2，防止将来退回裸 `stock_results`）。
- CLI（monkeypatch `run_scan` + `tmp_path` 快照目录）：
  - 正常路径 → 退出码 0、stdout 含快照路径；
  - `run_scan` 抛 `ConfigError` → 退出码 1、不产生任何快照文件。
- 现有测试不动（`run_scan` 返回值新增键向后兼容，若 `tests/test_main.py` 有返回键的严格断言则相应补充）。

---

## 影响面

- **分数、信号、报告内容零变化**：本设计不触碰 `indicators`/`scorer`/`reporter` 的渲染逻辑与任何取数；`main.py` 只往返回值加两个键。
- 新增文件：`src/snapshot.py`、`snapshot.py`、`.claude/skills/moneywiz/SKILL.md`、`tests/test_snapshot.py`、`docs/superpowers/specs/2026-10-10-skill-snapshot-design.md`。
- 运行时新增 `snapshots/` 目录（gitignore 排除）：每天最多新增一份 JSON + txt。
- 不改 `config/`、不改 `cache/` 的任何取值。

---

## 不纳入范围

- **定时调度**（cron/Claude Code 定时任务）：本设计只做手动触发（`/moneywiz`）；当天去重语义已为「一天内跑多次（手动 + 将来可能的定时）只留最后一次」做好准备，定时调度本身另案处理。
- **脚本化 diff 分析**：Q1 已裁定分析由 Claude 生成，Python 不做确定性 diff 模块。
- **快照清理/聚合策略**：Q3 已裁定全部保留，不做滚动删除或周期锚点。
- **用户级全局安装**（`~/.claude/skills/`）：Q4 已裁定项目内。
- **分析的输出格式模板化**：Claude 按 SKILL.md 维度指引自由生成中文分析，不做固定模板与阈值硬编码。
