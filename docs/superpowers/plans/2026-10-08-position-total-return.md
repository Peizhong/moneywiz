# 120 日位置指标：复权口径确认与文案修订 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「120 日位置」缺失的复权口径记录补上，并把写死的「120日」文案改成带全收益口径、窗口取自配置的形式。

**Architecture:** 只动展示层的文案与它取窗口的方式，不碰任何取数与判定逻辑。`_position_phrases` 增加一个带默认值的 `window` 参数（默认值让两个调用点中不渲染文案的那个无需改动），`render_report` 同样增加带默认值的 `position_window`，由 `main.py` 从 `cfg.rules["data"]["kline_days"]` 传入。文档三处同步记录口径。

**Tech Stack:** Python 3.12 / pytest；无新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-08-position-total-return-design.md`

## Global Constraints

- **测试绝不联网**；reporter 测试全部用内联 result dict 构造，不依赖 akshare 与配置加载。
- **一律用 `.venv/bin/pytest`**（系统环境没装依赖）。
- **零分数变化、零买入名单变化**：本次不触碰 `values` 中的任何数值、不触碰 `src/scorer.py`、不触碰 `_signals` 的判定逻辑、不改 `POSITION_LOW`/`POSITION_HIGH`（30/70）。
- **不改复权取数**：位置继续用前复权（`get_kline`），波动率/最大回撤/股息率分位继续用不复权（`get_kline_raw`）。这是 spec 决策 2 裁定保留的**有意**分工。
- 不改 `config/rules.yaml` 的任何取值、不改 `src/config.py`、不改 `cache/`。
- docstring / 提交信息一律中文。提交信息末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

**本任务的确切值**：
- 文案格式 `f"{window}日全收益低位 {value:.0f}%"`、`f"{window}日全收益高位 {value:.0f}%"`、`f"{window}日全收益中位 {value:.0f}%"`。
- `window` 默认 `120`；非法值（非 int、`bool`、≤0）兜底为 `120`，不抛异常。
- `POSITION_LOW = 30.0`、`POSITION_HIGH = 70.0` 不变。

## Review Focus

以下 5 类输入/故障最可能伤到使用者，每行都已在下方 Task 1 绑定测试。

1. **`_signals` 的高位判定被文案改动破坏**：`reporter.py:98` 靠 `_position_phrases(item.get("position"))[1] is not None` 实现「高位剥夺买入资格」。若返回值结构被改动（例如改成返回单个字符串），这个规则会**静默失效**——报告照常生成，只是高位股重新拿到买入标签。
2. **`render_report` 的其他调用方未传新参数**：若把 `position_window` 做成必填参数，所有既有调用点（含测试）都会 `TypeError`。必须带默认值。
3. **`kline_days` 配成非法值**：`0`、负数、字符串 `"60"`、`None`、布尔 `True` 都可能来自手改的 `rules.yaml`。不得印出「0日」「1日」或崩溃。
4. **`position` 无法转成数值**（`None`、字符串、列表）→ 必须继续返回 `(None, None)`：不出文案、不崩溃。这是既有行为，改动不得破坏。（`NaN` 能通过 `float()` 却不满足任何比较分支，会落到中间档渲染成「中位 nan%」——这是**既有瑕疵**，且数据层不可达；本次有意不处理，见 spec 的「不纳入范围」，别在实现时"顺手修"它，那会引入本 spec 明确排除的行为变化。）
5. **文档与实现脱节**：三份文档若仍写旧文案或漏记复权口径，读者会以为位置量的是价格图。→ Task 2。

---

### Task 1: 位置文案带全收益口径，窗口取自配置

**Files:**
- Modify: `src/reporter.py`（`render_report` 约 116-122 行、表格循环调用点约 189 行、`_position_phrases` 约 493 行）
- Modify: `main.py:503-510`（`render_report` 调用处）
- Test: `tests/test_reporter.py`

**Interfaces:**
- Consumes: `cfg.rules["data"]["kline_days"]`（`main.py:451` 已有变量 `kline_days`）
- Produces:
  - `_position_phrases(position, window: int = 120) -> tuple[str | None, str | None]`
  - `render_report(stock_results, fund_results, output_cfg, elapsed_s, market=None, risk_hints=None, position_window: int = 120) -> str`

- [ ] **Step 1: 写失败测试**

在 `tests/test_reporter.py` 追加（沿用文件里既有的 `_result(...)` helper 与 `render_report` / `OUTPUT_CFG` / `STOCK_HEADER` / `_table_rows`）：

```python
def test_position_phrases_declare_total_return_and_take_window():
    from src.reporter import _position_phrases

    assert _position_phrases(23.0) == ("120日全收益低位 23%", None)
    assert _position_phrases(82.0) == (None, "120日全收益高位 82%")
    assert _position_phrases(55.0) == ("120日全收益中位 55%", None)
    # 窗口来自参数，不再写死
    assert _position_phrases(23.0, 60) == ("60日全收益低位 23%", None)
    assert _position_phrases(82.0, 250) == (None, "250日全收益高位 82%")


def test_position_phrases_invalid_window_falls_back_to_120():
    from src.reporter import _position_phrases

    for bad in (None, 0, -1, "60", 60.5, True):
        assert _position_phrases(23.0, bad) == ("120日全收益低位 23%", None), bad


def test_position_phrases_keeps_tuple_shape_for_signals():
    """_signals 依赖 [1] is not None 判高位——返回结构是硬约束。"""
    from src.reporter import _position_phrases

    for value in (0.0, 30.0, 55.0, 70.0, 100.0):
        low, high = _position_phrases(value)
        # 低位与中位把文字放第 1 位；只有高位放第 2 位——这条区别就是 _signals 的判据
        assert isinstance(low, str) or isinstance(high, str)
        assert (high is not None) == (value >= 70.0), value
    # 无法转成数值的位置仍须返回二元组且都为 None（不崩溃、不出文案）
    for bad in (None, "x", [1]):
        assert _position_phrases(bad) == (None, None)


def test_render_report_takes_position_window():
    result = _result("600036", "招商银行", 80.0, scores={"dividend_yield": 1.0})
    result["position"] = 82.0

    row = _table_rows(
        render_report([result], [], OUTPUT_CFG, 1.0, position_window=60), STOCK_HEADER
    )[0]
    assert "60日全收益高位 82%" in row

    # 不传则走默认值，既有调用方无需改动
    row = _table_rows(render_report([result], [], OUTPUT_CFG, 1.0), STOCK_HEADER)[0]
    assert "120日全收益高位 82%" in row
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/test_reporter.py -k "position" -v`
Expected: FAIL —— `_position_phrases() takes 1 positional argument but 2 were given` 与 `render_report() got an unexpected keyword argument 'position_window'`

- [ ] **Step 3: 实现 `src/reporter.py` 与 `main.py`**

`_position_phrases` 签名改为 `(position, window: int = 120)`。窗口归一化照此写（顺序即判定顺序）：

```python
if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
    window = 120
```

排除 `bool` 的理由与 `src/config.py` 校验权重时一致：Python 里 `True` 是 `int` 的实例，不排除会让 `window=True` 静默渲染成「1日」。三分支的文案按 Global Constraints 里的格式串改写，**`elif value >= POSITION_HIGH` 的返回仍是 `(None, f"...")`、中间档仍返回 `(f"...", None)`——结构一个字符都不能动**。

`render_report` 签名末尾加 `position_window: int = 120`，在表格循环里把 `_position_phrases(item.get("position"))`（约 189 行）改为传入 `position_window`。**`_signals`（约 98 行）的调用不动**——它只需判高位、不渲染文案，走默认值即可。

`main.py:503-510` 的 `render_report(...)` 调用加一行 `position_window=kline_days`（`kline_days` 是同函数内 451 行已有的局部变量）。

- [ ] **Step 4: 更新既有断言并运行全量**

`tests/test_reporter.py` 里 **7 处**正断言需改成新文案（约 298、335、361、438、445、450、551 行），以及 **1 处负断言**（约 455 行 `assert "120日" not in row`，改为断言新文案前缀不出现）。**只改文案字符串，不改断言的语义、不改被测的阈值与被测的分数。**

Run: `.venv/bin/pytest -v`
Expected: 全绿。既有 `test_buy_eligibility_uses_same_threshold_as_high_position_phrase`（**定义在约 336 行**，其中约 352 行是 `at_threshold["position"] = 70.0` 那条）必须继续通过——它钉的是「`_signals` 的 70 阈值与文案的高位分支同源」，是本任务最关键的回归守卫。

- [ ] **Step 5: 目视确认报告**

Run: `.venv/bin/python main.py --no-interactive`
Expected: 风险列出现形如 `120日全收益高位 92%` 的文案；买入/观察/末位信号与改动前一致（零买入名单变化）。

- [ ] **Step 6: 提交**

```bash
git add src/reporter.py main.py tests/test_reporter.py
git commit -m "feat(reporter): 位置文案声明全收益口径，窗口取自 kline_days"
```

---

### Task 2: 文档三处记录复权口径

**Files:**
- Modify: `docs/superpowers/plans/2026-10-06-dividend-screener.md`（「关键口径决策」节）
- Modify: `CLAUDE.md`（「打分模型的方向性约定」段）
- Modify: `README.md`（「输出说明」节）

**Interfaces:**
- Consumes: Task 1 落地后的实际文案格式 `{window}日全收益低位/高位/中位 X%`

- [ ] **Step 1: 追加 plan 的关键口径决策第 5 条**

`docs/superpowers/plans/2026-10-06-dividend-screener.md` 的「关键口径决策」节现有 4 条，在其后追加第 5 条，记录位置的复权口径并列出四个价格类指标的分工：

- 位置 = **前复权**（全收益），理由：位置回答「现在买入是否追高」，而除息不是损失（分红以现金形式拿到）；与最大回撤的「含分红总回报口径」一致。实测位置（前复权）− 位置（不复权）在 132 只候选上平均 +6.7pp、最大 +39.1pp，19% 的标的高/中/低分类会因口径翻转。
- 波动率、最大回撤 = **不复权**，理由：除息噪声实测 +0.31pp，可忽略。
- 股息率历史分位 = **不复权**（必须），理由：前复权价已扣除后来的分红，会把历史股息率系统性算低。此理由 `src/indicators.py` 的 `calc_dividend_yield_percentile` 已文档化。
- 明确一句：**四处取数不同是有意的，不是待统一的疏漏。**

- [ ] **Step 2: 更新 `CLAUDE.md`**

在「打分模型的方向性约定」段现有的「波动率与最大回撤越低越好（权重 15/5，取近 250 根不复权 K 线…）」之后补一句：位置取**前复权**（全收益）K 线、与前者不同是**有意的**（除息不是损失，且与回撤的总回报口径一致），以及「120日」文案中的窗口数取自 `data.kline_days`。

- [ ] **Step 3: 更新 `README.md`**

「输出说明」里位置短语的示例改为新格式，并说明全收益口径。同时修正该节里任何把窗口写死为 120 的表述，改为一律引用 `kline_days`。

- [ ] **Step 4: 核对三处与实现一致**

Run: `grep -rn "全收益" README.md CLAUDE.md docs/superpowers/plans/2026-10-06-dividend-screener.md`
Expected: 三份文件都出现，且对口径与窗口来源的描述彼此一致、与 `src/reporter.py` 的实际格式串一致。

- [ ] **Step 5: 提交**

```bash
git add CLAUDE.md README.md docs/superpowers/plans/2026-10-06-dividend-screener.md
git commit -m "docs: 记录位置的复权口径（前复权/全收益）与四个价格类指标的取数分工"
```
