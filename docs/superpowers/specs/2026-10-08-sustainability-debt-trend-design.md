# 分红可持续性增强：负债红标与连续恶化判定 设计文档

> 日期：2026-10-08 | 状态：待审批

## 概述

现有 `apply_sustainability_penalty` 只对**当期快照**做三个判断：最新报告期盈利下滑、最近年报经营现金流为负、分红超现金流。这漏掉了两类在红利策略里权重很高的情况：

1. **负债恶化** —— 完全没有覆盖。借钱分红（而非经营现金流分红）是分红可持续性的直接威胁。
2. **连续多期恶化** —— 只有单期快照，抓不到「过去 1～2 年持续走弱」这一真正的危险模式。且现有口径对单期下滑即扣 10%，与「一次季度利润下降不用太在意」的判断标准相悖。

本次增强把这两块补上，同时把红标判定从「散在 scorer 与 reporter 两处各判一次」收敛为单一口径来源。

**约束**：不新增数据源、不新增网络请求——所需字段全部来自现有 `ak.stock_financial_analysis_indicator` 调用的同一个返回帧。

---

## 关键口径决策

以下为设计评审中确定的决策，实现时不得偏离：

1. **负债走红标扣分，不进指标池。** 不新增打分指标，`config.py` 强制股票指标权重和 = 100 的约束不受影响，现有标的的加权总分（扣分红标前）不变。
2. **负债判据用「变化型 + 利息保障」，不用绝对水平。** 绝对阈值会被银行永久误报（实测四大行资产负债率 90.2～92.4%），且行业间不可比。变化型（资产负债率同季同比）行业无关——银行长年稳定在 91% 附近，不会触发。
3. **银行的豁免来自数据本身，不引入行业分类。** 实测银行的「利息支付倍数」恒为缺失（该指标对银行不适用），故「利息支付倍数」判据天然跳过银行。不依赖 `cache/sina_industry.json`。
4. **利息支付倍数判据限定为「正数且低于阈值」。** 实测格力电器该值为 −860（利息净收入为正，属财务健康），若写成「低于阈值即触发」会误报。
5. **现金流连续恶化按同季同比口径，与利润同规则。** 每股经营性现金流是累计 YTD 值（长江电力 2026：Q1 0.48 → 中报 0.99 → 三季 1.75 → 年报 2.48），相邻期不可直接比较；同季同比（2026中报 vs 2025中报）可比，且比年报口径早 2～3 个季度发现问题。**注意**：这与现有「现金流的红标口径固定为年报」并存——现金流**绝对水平**的判定（为负）仍用年报，只有**趋势**判定改用同季同比。
6. **「连续恶化」判据取「近 4 期中 ≥3 期为负」，不取严格连续递减、也不取均值下移。** 实测长江电力近 8 期累计同比为 `29.6 → 17.8 → 29.8 → 14.7 → 0.6 → 6.1 → 30.1 → 13.2`（无一期为负）。严格连续递减对单季偶然波动过敏；均值下移会因「增速从 23% 台阶降到 12% 台阶」而点名长江电力这类优质公用事业，噪音过大。取「多数为负」兼顾召回与精确。
7. **四个维度各自只取最重一条，不叠加。** 防止「连续恶化」触发时把「单期下滑」重复计入（二者标的是同一件事）。
8. **封顶从 30 提到 45。** 四个维度满配为 15+15+10+10 = 50，封顶仅在极端情况生效。提高封顶是为修正「波动率一个档位差 = 15 分，而分红红标只扣约 7 分」的优先级倒挂。
9. **单期判据降权：盈利单期下滑从 −10 降到 −5。** 与决策 6 配套，把「一次下降不用太在意」编码进力度结构。
10. **红标判定收敛为单一函数。** `scorer.evaluate_sustainability()` 为纯函数，返回触发明细；`apply_sustainability_penalty` 与 `reporter` 都消费它的输出，不在两处各写一套规则。触发明细经 result dict 的 `sustainability_flags` 键传给展示层——`render_report` 目前收不到 config，展示层无法自行判定，把 flags 放进 result 契约同时消除了这个结构性障碍。
11. **「近 N 期」不足 N 期时不触发。** 序列长度 < `repeated_periods` 的标的不做连续判定（数据不足而非「未恶化」），避免次新股被误判为健康。
    **（终审 I1 追加）现金流趋势判据另设锚点门禁**：`cash_yoy_history` 的前段期可能因「去年同季基期缺失或 ≤ 0」被整条剔除，使窗口整体前移到很旧的报告期（实测 601166 兴业银行停在 2024Q1–Q4，全部 2025/2026 期被剔除），此时「近 4 期」名不副实、且文案读起来像当期。故 `cash_repeated` 与 `cash_decline` 仅在 `cash_yoy_history[0]["period"] == latest_period` 时触发；不等或 `latest_period` 缺失（旧缓存、手改数据）即视为数据不足，降级为不触发。**利润侧不加此门禁**：`eps_history` 无基期依赖，且「最新期留空时向前回退到最近一个有效值」是既有文档化语义。其余判据（`negative_cash`、`cash_cover`、利润侧全部、负债侧全部）一概不受影响。

---

## 模块详细设计

### data.py — `get_financial_health()` 扩展

签名与返回值类型不变（`dict | None`），新增以下键。全部来自同一次 `ak.stock_financial_analysis_indicator` 调用，`start_year` 维持 `today.year - 3`（4 年 ≈ 16 个报告期，足够算 4 个同季同比值）。

| 键 | 类型 | 口径 |
|---|---|---|
| `eps_history` | `list[{"period": str, "growth": float}]` | `净利润增长率(%)`（累计同比，跨期可比），**只收录数值有效的报告期**，新→旧，最多 8 条 |
| `cash_yoy_history` | `list[{"period": str, "yoy": float}]` | `每股经营性现金流(元)` 与**去年同季**配对比值：`(本期 / 去年同季 − 1) × 100`，新→旧，最多 8 条 |
| `debt_ratio` | `float \| None` | `资产负债率(%)` 最新报告期 |
| `debt_ratio_yoy` | `float \| None` | `debt_ratio` 相对去年同季的变化，单位 pp |
| `interest_cover` | `float \| None` | `利息支付倍数` 最新报告期 |
| `latest_period` | `str \| None` | 帧内最新的报告期（`frame["日期"]` 排序后的最大值），**不看该期各字段是否有值**（终审 I1 追加；无法解析的日期不计）——判定层靠它判断现金流趋势窗口是否陈旧，见决策 11 |

两个 history 的上限均为 8（本次判定窗口为 4，留一倍余量以免调窗口即需升缓存版本）。`debt_ratio` 本身不参与判定，供 `debt_ratio_yoy` 计算与后续展示用。

现有四个键（`eps_growth`、`eps_period`、`op_cash_per_share`、`payout_stmt`）**取值不变**。`eps_growth`/`eps_period` 的实现改为取 `eps_history[0]`，与原先「从新到旧找第一个有效值」的循环结果同值，但实现更简单；**「二者同有同无」的约束保留**。

**剔除规则（重要）**：
- `cash_yoy_history` 中，去年同季值 **≤ 0 或缺失**的期**整条剔除**——从负值算同比变化率没有意义（从负转正实为改善），不得按 0 或按绝对值处理，也不得让该期参与「近 4 期中 ≥3 期为负」的计数。**该剔除会让窗口整体前移**（最近几期都被剔除时 `[:4]` 停在很旧的期），判定层据此按决策 11 的锚点门禁降级。
- `debt_ratio_yoy` 在任一同季值缺失时为 `None`。
- 无法解析的报告期（无 `-` 分隔符）整期跳过（`_period_key` → `None`），不影响其他期与其他字段。
- 任一新增字段取不到 → 对应键为 `None` 或空列表，**不影响其他键**。

**缓存**：`cache/financial_cache.json` 每条记录新增 `"schema": 3`。读取时无 `schema` 字段（或 `schema` < 3）的条目**视为过期重取**，沿用现有「旧格式条目没有 `eps_period` → 视为过期」的处理方式。首次运行会多一轮年报请求，之后恢复正常。（2 → 3 因返回形状新增 `latest_period`。）

### scorer.py — 红标判定

新增纯函数，作为红标的唯一口径来源：

```python
def evaluate_sustainability(
    sustainability: dict | None, config: dict | None = None
) -> list[dict]
```

返回按维度顺序排列的触发明细，每条为：

```python
{"key": str, "dimension": str, "value": float | int, "penalty": float}
```

`dimension ∈ {"profit", "cashflow", "coverage", "debt"}`。`sustainability` 为 `None` 或全部数据缺失 → 返回 `[]`。

**四个维度各自取最重一条**，求和后按 `max_penalty` 封顶：

| 维度 | key | 触发条件 | 默认扣分 |
|---|---|---|---|
| profit | `eps_repeated` | `eps_history` 前 `repeated_periods`(4) 条中 `growth < 0` 的条数 ≥ `repeated_min_negative`(3) | 15 |
| profit | `eps_decline` | `eps_growth < 0` | 5 |
| cashflow | `negative_cash` | 最近年报 `op_cash_per_share ≤ 0` | 15 |
| cashflow | `cash_repeated` | `cash_yoy_history` 前 `repeated_periods`(4) 条中 `yoy < 0` 的条数 ≥ `repeated_min_negative`(3) | 15 |
| cashflow | `cash_decline` | `cash_yoy_history[0].yoy < 0` | 5 |
| coverage | `cash_cover` | `cash_cover > 100` | 10 |
| debt | `debt_jump` | `debt_ratio_yoy ≥ debt_jump_threshold`(10) | 10 |
| debt | `interest_cover` | `0 < interest_cover < interest_cover_threshold`(2) | 10 |

`repeated_periods` 与 `repeated_min_negative` 为 profit 与 cashflow 两个维度**共用**（本次不做维度差异化窗口）。

**边界**：`growth < 0` / `yoy < 0` 为严格小于，`= 0` 不触发；`debt_ratio_yoy ≥ threshold` 含等号；`interest_cover < threshold` 为严格小于，`= 2.0` 不触发。

**降级**：任一判据所需数据缺失 → 该判据不触发，其余判据照常评估。`eps_history` 或 `cash_yoy_history` 的条数 < `repeated_periods` → 对应 `*_repeated` 不触发，降级为该维度的单期判据（决策 11）。数据全缺 → 空列表，`total` 原样返回、0 扣减（保持现有行为）。

`apply_sustainability_penalty(total, sustainability, config)` 签名与返回 `(调整后总分, 实际扣减百分比)` 不变，内部改为调用 `evaluate_sustainability` 求和后封顶：

```
penalty = min(Σ 各维度最重 penalty, max_penalty)
调整后总分 = round(total × (1 − penalty / 100), 1)
```

`DEFAULT_SUSTAINABILITY_PENALTY` 新增键（均可被 `rules.yaml` 覆盖）：

```python
{
    "eps_decline_penalty": 5.0,
    "eps_repeated_penalty": 15.0,
    "repeated_periods": 4,
    "repeated_min_negative": 3,
    "negative_cash_penalty": 15.0,
    "cash_cover_penalty": 10.0,
    "cash_decline_penalty": 5.0,
    "cash_repeated_penalty": 15.0,
    "debt_jump_penalty": 10.0,
    "debt_jump_threshold": 10.0,
    "interest_cover_penalty": 10.0,
    "interest_cover_threshold": 2.0,
    "max_penalty": 45.0,
}
```

原 `eps_decline_penalty` 默认值由 10 改为 5（决策 9）。

### main.py — `_sustainability()` 与 result 契约

`_sustainability()` 在现有四个键之外，把 `get_financial_health` 的新字段原样透传进 sustainability dict：`eps_history`、`cash_yoy_history`、`debt_ratio_yoy`、`interest_cover`。`cash_cover` 的现有算法（`ttm_per_share / op_cash × 100`，要求 `op_cash > 0`）不变。

`_result()`（`main.py:58`）在 result dict 中新增一键，其中 `sustainability_cfg` 即 `rules_section.get("sustainability")`：

```python
"sustainability_flags": scorer.evaluate_sustainability(sustainability, sustainability_cfg),
```

该函数为纯函数且 `_result()` 已持有 `rules_section`，故在此处求值，与已有的 `apply_sustainability_penalty` 调用（`main.py:66`）并列。result dict 契约由

```
{code, name, total, values, scores, missing, tech, position,
 sustainability, turnover_wan, new_constituent}
```

变为追加 `sustainability_flags: list[dict]`（无红标或数据缺失时为空列表，**不是** `None`）。`CLAUDE.md` 的 result 契约描述需同步更新。

### reporter.py — 展示

`_sustainability_phrases` 的入参由 sustainability dict 改为 `sustainability_flags`（`list[dict]`），按 `key` 渲染中文短语，保持维度顺序（profit → cashflow → coverage → debt）。**不再自行判断任何阈值**——现有实现重新判断 `eps_growth < 0`、`cash_cover > 100` 的做法随本次改动移除。

| key | 短语 |
|---|---|
| `eps_decline` | `盈利下滑 13%（2026中报）`（保留现有报告期标注，取 `eps_period`） |
| `eps_repeated` | `盈利连续下滑 3/4 期` |
| `negative_cash` | `经营现金流为负`（不变） |
| `cash_decline` | `现金流下滑 22%` |
| `cash_repeated` | `现金流连续下滑 3/4 期` |
| `cash_cover` | `分红超现金流 118%`（不变） |
| `debt_jump` | `负债率上升 12pp` |
| `interest_cover` | `利息保障 1.4 倍` |

三个调用点统一改读 `item.get("sustainability_flags")`：

| 位置 | 用途 |
|---|---|
| `_risk_text`（`reporter.py:203`） | 表格风险列 |
| `render_detail` 扣分链（`reporter.py:319`） | 详情页「可持续性扣分 X%（原因）」 |
| `render_detail` 亮点/风险（`reporter.py:404`） | 详情页风险段 |

`render_detail` 的 `eps_decline` 短语需要报告期，而 flags 里不带 `eps_period`——该调用点保留对 sustainability dict 的访问，从 `item["sustainability"]["eps_period"]` 取。`reporter.py:290` 的 `apply_sustainability_penalty` 调用保留（详情页需要展示扣分链）。

**风险列上限由 4 项提到 6 项**（`_risk_text` 的 `items[:4]` → `items[:6]`）。可持续性警示最多 4 条，加流动性提示、120 日高位提示后需要更多空间；现有顺序已保证可持续性优先，不改排序。

### config.py — 不修改

`sustainability` 段保持自由格式，键的可选性与缺省值由 `scorer.DEFAULT_SUSTAINABILITY_PENALTY` 承担，与现状一致。**不新增校验。**

### rules.yaml — 新增可覆盖键

`stocks.sustainability` 现有三个键全部保留（`negative_cash_penalty`、`cash_cover_penalty` 值不变；`eps_decline_penalty` 由 10 改 5，`max_penalty` 由 30 改 45），新增九个键：`repeated_periods`、`repeated_min_negative`、`eps_repeated_penalty`、`cash_decline_penalty`、`cash_repeated_penalty`、`debt_jump_penalty`、`debt_jump_threshold`、`interest_cover_penalty`、`interest_cover_threshold`。注释同步改写为四维度说明。

---

## 影响面

- **所有现存标的的总分都会变**：单期盈利下滑扣分 10 → 5（上调），新增判据可能追加扣分（下调）。排序与买卖信号会变，与历史报表不可比。
- **首次运行**因缓存 schema 升级，会为每只标的重新请求一轮财务数据（约 4 个 HTTP 请求/只，80 只候选约 320 次），耗时增加，之后恢复正常。
- 不新增 akshare 调用，不影响东财熔断、行情缓存与行业 PE 链路。
- **`CLAUDE.md` 需同步更新**两处：result dict 契约（新增 `sustainability_flags`）与「打分模型的方向性约定」段落（红标由三项变四维度、封顶 45、规模最大的单期扣分由 10 降为 5）。

---

## 测试策略

遵循项目约定：**测试绝不联网**，fixture 使用真实上游列名；`tests/conftest.py` 的 autouse fixture 需覆盖任何新增的模块级状态（本次无新增）。

- `tests/test_data_stock.py`
  - 新字段解析（`eps_history`、`cash_yoy_history`、`debt_ratio`、`debt_ratio_yoy`、`interest_cover`）
  - `cash_yoy_history` 剔除规则：去年同季为负、为零、缺失三种情形均整条剔除，且不计入连续期数
  - `eps_history` 只收录有效值、新→旧、上限 8 条
  - `latest_period` = 帧内最新报告期（含该期字段全空的情形；终审 I1）；非法报告期（无 `-` 分隔符）整期跳过而不抛错（M1）；基期 ≤0 剔除导致窗口前移时，趋势判据端到端不触发（601166 型）
  - 无 `schema` 字段的旧缓存条目 → 触发重取
  - 单一字段缺失不影响其他字段
- `tests/test_scorer.py`
  - `evaluate_sustainability`：8 个 key 各自单独触发（含边界：`growth = 0`/`yoy = 0` 不触发、`debt_ratio_yoy = 10.0` 触发、`interest_cover = 2.0` 不触发、`interest_cover = 0` 与负值均不触发）
  - 维度取最重：`eps_repeated` 触发时 `eps_decline` 不出现；`negative_cash` 与 `cash_repeated` 同时成立时只出一条
  - 序列不足 `repeated_periods` 条 → `*_repeated` 不触发，单期判据仍生效
  - 现金流趋势窗口未锚在最新报告期（`cash_yoy_history[0]["period"] != latest_period`，含 `latest_period` 缺失/None）→ `cash_repeated`/`cash_decline` 不触发，`negative_cash`/`cash_cover`/利润侧/负债侧照常（终审 I1 门禁）
  - 封顶 45：四维度满配的 penalty 为 45（合计 50 被截断）
  - 银行型（`interest_cover=None`、`debt_ratio_yoy` 平稳）→ 空列表
  - 格力型（`interest_cover` 为负）→ 不触发 `interest_cover`
  - 数据全缺 → 空列表、总分与 0 扣减不变
  - `apply_sustainability_penalty` 的 `total=None` 行为不变
- `tests/test_main.py`
  - result dict 含 `sustainability_flags` 键；无红标时为 `[]` 而非 `None`
- `tests/test_reporter.py`
  - 8 个 key 的中文短语渲染（含 `eps_decline` 的报告期标注）
  - 风险列 6 项上限、可持续性优先于流动性与高位提示
  - `sustainability_flags` 为空/缺失时风险列与详情页不出现可持续性文案

---

## 不纳入范围

- **严格的自由现金流（FCF）口径**：上游 86 列中无资本开支字段，`FCF = 经营现金流 − 资本开支` 无法计算。本次以经营现金流作为近似，此近似需在代码注释中写明。
- **公告类事件**：分红政策/承诺变更、管理层变动、审计意见异常、监管政策影响——均无量化数据源。
- **行业前景判断**：无数据源，且属定性判断。
- **持仓跟踪与主动告警**：本工具仍是「跑一次排一次名」的筛选器，不引入持仓概念、历史评分快照、变化推送。
- **负债的行业内相对位置**：本次不引入 `cache/sina_industry.json` 的行业分类依赖。
- **`financial_cache_days` 调整**：保持 30 天。新财报最长滞后 30 天，可接受。
