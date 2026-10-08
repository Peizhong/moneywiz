# 分红可持续性增强（负债红标 + 连续恶化判定）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有三项「当期快照」分红红标扩展为四维度（利润 / 现金流 / 分红覆盖 / 负债），补上负债恶化与连续多期恶化两类判定，并把红标判定收敛为单一口径来源。

**Architecture:** 数据层仍在 `get_financial_health` 的**同一次** akshare 调用上多读字段（新增两个序列 + 三个负债字段，缓存 schema 升到 2）；判定层新增纯函数 `scorer.evaluate_sustainability()` 作为红标唯一口径，`apply_sustainability_penalty` 与展示层都消费它的输出；触发明细经 result dict 新增的 `sustainability_flags` 键传给 `reporter`，从结构上消除「scorer 与 reporter 各判一次」的口径漂移。

**Tech Stack:** Python 3.12 / pandas / pytest；akshare 取数；无新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-08-sustainability-debt-trend-design.md`

## Global Constraints

- **测试绝不联网**：`tests/conftest.py` 的 `_no_real_network` 会把 `src.data.requests.get` 打桩为失败；`ak.*` 需各用例 monkeypatch。fixture 使用真实上游列名。
- **一律用 `.venv/bin/pytest`**（系统环境没装依赖）。
- 新增 akshare 调用只能写在 `src/data.py`；indicators/scorer/reporter/config/constituents 不得 `import akshare`。
- `src/indicators.py` 是纯计算层，本次不修改。
- 上游列名逐字照抄（含全角括号与单位）：`净利润增长率(%)`、`每股经营性现金流(元)`、`资产负债率(%)`、`利息支付倍数`、`股息发放率(%)`。
- 数据层约定：**失败返回 `None`；成功但无数据返回带列的空 DataFrame**。
- 缓存：只缓存非 None 结果；`Cache.read_json` 已处理整数值浮点列推断。
- 提交信息用中文，末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 本次**不改** `config/rules.yaml` 的指标权重（股票指标权重和仍为 100）、不改 `src/config.py` 的校验、不改 `financial_cache_days`。

## Review Focus

以下 5 类输入/故障在 spec 里没有逐条落到某个用例上，但最可能伤到真实使用者。每行都已在下方对应 Task 中绑定测试。

1. **上游返回的帧缺少某个新增列**（上游改版，或该股根本没有该指标——银行缺 `利息支付倍数` 就是真实例子）：该项应降级为 `None`，**不得 KeyError 崩溃**、不得让整只标的变成不可评分 → Task 1。
2. **缓存里同时存在 schema 1 与 schema 2 条目**（升级后第一次运行，部分标的先重取了）：读取时不得崩溃，也不得把旧条目当新条目用 → Task 1。
3. **`净利润增长率(%)` 某期返回非数值**（`"-"`、空串、NaN）：不得进入 `eps_history`，否则 `growth < 0` 比较会抛 `TypeError` 把整轮扫描打断 → Task 1。
4. **次新股报告期不足 4 期**：`*_repeated` 不触发，且不得因切片越界崩溃；降级为单期判据 → Task 2。
5. **`sustainability` 为 `None`（财务数据整体获取失败）**：表格风险列、详情页扣分链、详情页风险段三处都不得出现可持续性文案或崩溃 → Task 4。

---

### Task 1: 数据层新字段与缓存 schema 2

**Files:**
- Modify: `src/data.py`（`get_financial_health`，约 1099-1169 行）
- Test: `tests/test_data_stock.py`（`get_financial_health` 段，约 754 行起）

**Interfaces:**
- Produces: `get_financial_health(code: str, cache_dir, cache_days: int = 30) -> dict | None`，成功时返回 **9 个键**：
  - 既有：`eps_growth: float|None`、`eps_period: str|None`、`op_cash_per_share: float|None`、`payout_stmt: float|None`
  - 新增：`eps_history: list[{"period": str, "growth": float}]`（新→旧，≤8 条）
  - 新增：`cash_yoy_history: list[{"period": str, "yoy": float}]`（新→旧，≤8 条）
  - 新增：`debt_ratio: float|None`、`debt_ratio_yoy: float|None`（单位 pp）、`interest_cover: float|None`

- [ ] **Step 1: 写失败测试**

在 `tests/test_data_stock.py` 的 `get_financial_health` 段追加。先给既有 fixture 补上新列（**追加列，不改既有列的值**）：

```python
def _financial_frame_extended():
    """在 _financial_frame 的报告期上加负债列与跨年现金流配对所需的期。"""
    return pd.DataFrame(
        {
            "日期": [
                "2024-06-30", "2024-12-31",
                "2025-06-30", "2025-12-31",
                "2026-03-31", "2026-06-30",
            ],
            "净利润增长率(%)": [5.0, 5.0, 4.0, 3.0, -14.7, -12.9],
            "每股经营性现金流(元)": [0.50, 1.00, 0.40, 2.77, 0.17, 0.80],
            "资产负债率(%)": [55.0, 56.0, 57.0, 58.0, 60.0, 70.0],
            "利息支付倍数": [4.0, 4.0, 3.0, 2.5, 2.0, 1.4],
            "股息发放率(%)": [35.0, 40.0, 45.0, 60.0, 0.1, 30.0],
        }
    )
```

用例（名称与断言都要落到位）：

```python
def test_financial_health_debt_and_history_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator",
        lambda symbol, start_year: _financial_frame_extended(),
    )
    out = get_financial_health("600015", tmp_path)

    # 负债：最新期 2026-06-30 = 70.0；去年同季 2025-06-30 = 57.0 → +13.0pp
    assert out["debt_ratio"] == pytest.approx(70.0)
    assert out["debt_ratio_yoy"] == pytest.approx(13.0)
    assert out["interest_cover"] == pytest.approx(1.4)
    # 序列新→旧
    assert out["eps_history"][0] == {"period": "2026-06-30", "growth": pytest.approx(-12.9)}
    assert [e["period"] for e in out["eps_history"]] == [
        "2026-06-30", "2026-03-31", "2025-12-31",
        "2025-06-30", "2024-12-31", "2024-06-30",
    ]
    # 现金流同季同比：2026-06-30 0.80 / 2025-06-30 0.40 → +100.0%
    #                 2025-12-31 2.77 / 2024-12-31 1.00 → +177.0%
    #                 2024-12-31 1.00 / 2023-12-31 缺失 → 剔除
    assert out["cash_yoy_history"] == [
        {"period": "2026-06-30", "yoy": pytest.approx(100.0)},
        {"period": "2025-12-31", "yoy": pytest.approx(177.0)},
    ]


def test_financial_health_cash_yoy_excludes_nonpositive_base(monkeypatch, tmp_path):
    # 去年同季 ≤ 0 → 整条剔除（从负值算变化率没有意义），也不得计入连续期数
    frame = pd.DataFrame({
        "日期": ["2025-06-30", "2026-06-30", "2025-12-31", "2026-12-31"],
        "每股经营性现金流(元)": [-0.5, 0.8, 0.0, 1.0],
        "净利润增长率(%)": [1.0, 2.0, 3.0, 4.0],
    })
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator", lambda symbol, start_year: frame
    )
    out = get_financial_health("600015", tmp_path)
    assert out["cash_yoy_history"] == []


def test_financial_health_missing_new_columns_degrade_to_none(monkeypatch, tmp_path):
    # Review Focus 1：上游帧没有负债列 → None，不得崩溃
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator", lambda symbol, start_year: _financial_frame()
    )
    out = get_financial_health("600015", tmp_path)
    assert out["debt_ratio"] is None
    assert out["debt_ratio_yoy"] is None
    assert out["interest_cover"] is None
    assert out["eps_history"]  # 有值的仍要给出


def test_financial_health_eps_history_skips_non_numeric(monkeypatch, tmp_path):
    # Review Focus 3：非数值不得进入序列（否则下游 < 0 比较抛 TypeError）
    frame = _financial_frame()
    frame["净利润增长率(%)"] = frame["净利润增长率(%)"].astype(object)
    frame.loc[frame["日期"] == "2026-06-30", "净利润增长率(%)"] = "-"
    monkeypatch.setattr(
        ak, "stock_financial_analysis_indicator", lambda symbol, start_year: frame
    )
    out = get_financial_health("600015", tmp_path)
    assert all(isinstance(e["growth"], float) for e in out["eps_history"])
    assert "2026-06-30" not in [e["period"] for e in out["eps_history"]]


def test_financial_health_schema1_cache_entry_refetches(monkeypatch, tmp_path):
    # Review Focus 2：schema 1 条目（有 eps_period 但无 schema）必须重取
    calls = []

    def fake(symbol, start_year):
        calls.append(1)
        return _financial_frame()

    monkeypatch.setattr(ak, "stock_financial_analysis_indicator", fake)

    cache_path = tmp_path / "financial_cache.json"
    cache_path.write_text(json.dumps({"stocks": {"600015": {
        "eps_growth": 1.0, "eps_period": "2026-06-30",
        "op_cash_per_share": 2.0, "payout_stmt": None,
        "updated_at": datetime.now().isoformat(),
    }}}), encoding="utf-8")

    out = get_financial_health("600015", tmp_path)
    assert len(calls) == 1          # 旧条目被忽略，重取上游
    assert out["eps_history"]       # 新字段来自重取
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/test_data_stock.py -k "financial_health" -v`
Expected: 新增 5 个用例 FAIL（`KeyError: 'debt_ratio'` / 序列键缺失）

- [ ] **Step 3: 实现 `src/data.py`**

在 `get_financial_health` 内、既有 `_value` 闭包之后实现。**算法（签名与用例未完全决定，照此写）**：

```python
CACHE_SCHEMA = 2   # 模块级常量，放在 FINANCIAL_CACHE_FILENAME 附近

def _period_key(period: str) -> tuple[int, str]:
    """报告期 → (年, 月日)，用于配相同季。"""
    year, rest = period.split("-", 1)
    return int(year), rest

def _pair_same_period(pairs: dict[str, float], periods: list[str]) -> list[dict]:
    """同季同比序列（新→旧，≤8）：value(P) / value(P 去年同季) − 1，单位 %。

    基期（去年同季）缺失或 ≤ 0 → 整条剔除；本期非数值 → 整条剔除。
    """
    out = []
    for period in periods:
        value = pairs.get(period)
        if value is None:
            continue
        year, md = _period_key(period)
        base = pairs.get(f"{year - 1}-{md}")
        if base is None or base <= 0:
            continue
        out.append({"period": period, "yoy": (value / base - 1.0) * 100.0})
        if len(out) == 8:
            break
    return out
```

按此实现，并注意：

- `eps_history`：遍历 `frame.sort_values("日期", ascending=False)`，`_value(row, "净利润增长率(%)")` 非 None 才收，最多 8 条。`eps_growth`/`eps_period` 改为取 `eps_history[0]`（同有同无的约束因此自动成立）。
- `cash_yoy_history`：先构造 `{日期: 每股经营性现金流}` 字典（非数值不放入），报告期列表按新→旧，调用 `_pair_same_period`。
- `debt_ratio`、`interest_cover`：从新到旧第一个有效值（与 `eps_growth` 同一回退规则）。
- `debt_ratio_yoy`：取到 `debt_ratio` 的那个报告期 P，找 P 的去年同季值；两者都在才相减（**单位 pp，不相除**），否则 `None`。
- 缓存条目写入 `"schema": CACHE_SCHEMA`；读取时 `entry.get("schema") != CACHE_SCHEMA` → 视为过期重取（**保留**既有 `numeric_ok`/`period_ok`/`paired_ok` 守卫并扩展到新键，它们仍防手改缓存文件）。
- 无年报行仍返回 `None`（既有行为不变）。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/pytest tests/test_data_stock.py -v`
Expected: 全绿。既有 `test_financial_health_eps_growth_uses_latest_period` 的精确字典断言需扩充为 9 键（`eps_history` 为 5 条、`cash_yoy_history` 只有 `2025-12-31 +177.0` 与 `2024-12-31 -9.0909…` 两条、三个负债键为 `None`）；`test_financial_health_eps_growth_falls_back_when_latest_blank` 等其余用例**不改断言**。

- [ ] **Step 5: 提交**

```bash
git add src/data.py tests/test_data_stock.py
git commit -m "feat(data): 财务健康指标补负债字段与多期序列，缓存 schema 升 2"
```

---

### Task 2: 红标判定收敛为单一来源

**Files:**
- Modify: `src/scorer.py`（`DEFAULT_SUSTAINABILITY_PENALTY` 与 `apply_sustainability_penalty`，88-205 行）
- Modify: `config/rules.yaml`（`stocks.sustainability` 段）
- Test: `tests/test_scorer.py`（约 203-255 行）、`tests/test_main.py:540`

**Interfaces:**
- Consumes: Task 1 的 `get_financial_health` 返回键（经 main.py 组装进 sustainability dict）
- Produces:
  - `evaluate_sustainability(sustainability: dict | None, config: dict | None = None) -> list[dict]`，每条 `{"key": str, "dimension": str, "value": float|int, "penalty": float}`，`*_repeated` 额外带 `"window": int`。`dimension ∈ {"profit","cashflow","coverage","debt"}`，按此顺序排列。
  - `apply_sustainability_penalty(total: float | None, sustainability: dict | None, config: dict | None = None) -> tuple[float | None, float]`（签名与语义不变）

- [ ] **Step 1: 写失败测试**

替换 `tests/test_scorer.py` 的 `PENALTY_CFG` 为显式全键（**既有用例因此保持原断言不变**），并追加新用例：

```python
PENALTY_CFG = {
    "eps_decline_penalty": 10.0, "eps_repeated_penalty": 15.0,
    "repeated_periods": 4, "repeated_min_negative": 3,
    "negative_cash_penalty": 15.0, "cash_cover_penalty": 10.0,
    "cash_decline_penalty": 5.0, "cash_repeated_penalty": 15.0,
    "debt_jump_penalty": 10.0, "debt_jump_threshold": 10.0,
    "interest_cover_penalty": 10.0, "interest_cover_threshold": 2.0,
    "max_penalty": 45.0,
}


def test_evaluate_sustainability_returns_ordered_flags():
    out = evaluate_sustainability(
        {"eps_growth": -12.9, "op_cash_per_share": 0.5, "cash_cover": 200.0,
         "debt_ratio_yoy": 13.0, "interest_cover": 1.4}, PENALTY_CFG
    )
    assert [(f["key"], f["dimension"]) for f in out] == [
        ("eps_decline", "profit"), ("cash_cover", "coverage"),
        ("debt_jump", "debt"), ("interest_cover", "debt"),
    ]
    assert out[0]["value"] == pytest.approx(-12.9)
    assert out[0]["penalty"] == 10.0


def test_evaluate_sustainability_keeps_one_flag_per_dimension():
    # 连续恶化触发时单期不再叠加；现金流同理
    out = evaluate_sustainability(
        {"eps_growth": -12.9, "eps_history": [
            {"period": "2026-06-30", "growth": -12.9},
            {"period": "2026-03-31", "growth": -3.0},
            {"period": "2025-12-31", "growth": -1.0},
            {"period": "2025-09-30", "growth": 2.0},
        ], "op_cash_per_share": -0.1}, PENALTY_CFG
    )
    keys = [f["key"] for f in out]
    assert "eps_repeated" in keys and "eps_decline" not in keys
    assert out[[f["key"] for f in out].index("eps_repeated")]["window"] == 4
    assert "negative_cash" in keys and "cash_decline" not in keys


def test_evaluate_sustainability_short_series_skips_repeated():
    # Review Focus 4：不足 4 期 → 不触发连续判据，单期判据仍生效
    out = evaluate_sustainability(
        {"eps_growth": -12.9, "eps_history": [
            {"period": "2026-06-30", "growth": -12.9},
            {"period": "2026-03-31", "growth": -3.0},
        ]}, PENALTY_CFG
    )
    assert [f["key"] for f in out] == ["eps_decline"]


def test_evaluate_sustainability_boundaries():
    assert evaluate_sustainability({"eps_growth": 0.0}, PENALTY_CFG) == []
    assert evaluate_sustainability({"debt_ratio_yoy": 10.0}, PENALTY_CFG)[0]["key"] == "debt_jump"
    assert evaluate_sustainability({"interest_cover": 2.0}, PENALTY_CFG) == []
    # 银行：利息支付倍数缺失 + 负债率平稳 → 零红标
    assert evaluate_sustainability(
        {"debt_ratio_yoy": 0.3, "interest_cover": None}, PENALTY_CFG) == []


def test_evaluate_sustainability_ignores_nonpositive_interest_cover():
    # 格力型：利息净收入为正（倍数为负）属财务健康，不得误报
    assert evaluate_sustainability({"interest_cover": -860.0}, PENALTY_CFG) == []
    assert evaluate_sustainability({"interest_cover": 0.0}, PENALTY_CFG) == []


def test_penalty_caps_at_four_dimension_total():
    both = {"eps_growth": -12.9, "op_cash_per_share": -0.1,
            "cash_cover": 200.0, "debt_ratio_yoy": 13.0}
    # 利润 10 + 现金流 15 + 覆盖 10 + 负债 10 = 45（未触发封顶）
    assert apply_sustainability_penalty(80.0, both, PENALTY_CFG) == (44.0, 45.0)
    # 现金流维度只取最重一条：negative_cash(15) 与 cash_repeated(15) 不叠加
    assert evaluate_sustainability(
        {"op_cash_per_share": -0.1, "cash_cover": None}, PENALTY_CFG
    )[0]["key"] == "negative_cash"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/test_scorer.py -v`
Expected: FAIL —— `ImportError: cannot import name 'evaluate_sustainability'`

- [ ] **Step 3: 实现 `src/scorer.py`**

新增 `DEFAULT_SUSTAINABILITY_PENALTY`（照 spec 的 13 个键，`eps_decline_penalty` 为 5.0、`max_penalty` 为 45.0）与两个函数。**维度取最重的聚合（照此写）**：

```python
_DIMENSIONS = ("profit", "cashflow", "coverage", "debt")


def _repeated(series, key, cfg) -> int | None:
    """前 repeated_periods 条中 < 0 的条数；序列不足 window 条 → None。"""
    window = int(cfg["repeated_periods"])
    if series is None or len(series) < window:
        return None
    negative = sum(1 for item in series[:window] if item[key] < 0)
    return negative if negative >= int(cfg["repeated_min_negative"]) else None
```

每个维度构造候选 flag 列表（触发条件见 spec 的表格），按 `penalty` 降序取第一条，再按 `_DIMENSIONS` 顺序拼成结果。`value` 取该判据的原始数值（`*_repeated` 取负值条数，并附 `window`）。`sustainability` 为假值 → `[]`。

`apply_sustainability_penalty` 内部改为 `sum(f["penalty"] for f in flags)` 后 `min(..., max_penalty)`，其余（`total is None` 原样返回、`round(total * (1 - penalty/100), 1)`）不变。**中间那条 `elif cash_cover` 的互斥逻辑删除**——它现在由维度聚合承担。

同步更新 `config/rules.yaml` 的 `stocks.sustainability` 段：新增 9 个键、`eps_decline_penalty` 改 5、`max_penalty` 改 45，注释改写为四维度说明。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/pytest tests/test_scorer.py tests/test_main.py -v`

Expected: 全绿。需同步改 `tests/test_main.py::test_run_applies_sustainability_penalty_to_total`（`RULES_CONFIG` 无 `sustainability` 段 → 走新默认值）：

- 平安银行：`eps_decline` 5 + `cash_cover` 10 = 15% → `92.5 × 0.85 = 78.625` → **`78.6`**（原 `74.0`）
- 招商银行：仅 `eps_decline` 5% → `25.0 × 0.95 = 23.75` → **`23.8`**（原 `22.5`）

`test_penalty_cap_and_defaults` 末行的 `None` 配置断言改为 `(76.0, 5.0)`（`80.0 × 0.95`）。

- [ ] **Step 5: 提交**

```bash
git add src/scorer.py config/rules.yaml tests/test_scorer.py tests/test_main.py
git commit -m "feat(scorer): 红标判定收敛为 evaluate_sustainability，新增负债与连续恶化四维度"
```

---

### Task 3: result 契约透传 flags

**Files:**
- Modify: `main.py`（`_result` 50-79 行、`_sustainability` 265-290 行）
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: Task 1 的 9 键返回、Task 2 的 `scorer.evaluate_sustainability`
- Produces: result dict 新增 `sustainability_flags: list[dict]`（无红标或数据缺失时为 `[]`，**不是** `None`）；sustainability dict 新增透传键 `eps_history`、`cash_yoy_history`、`debt_ratio_yoy`、`interest_cover`

- [ ] **Step 1: 写失败测试**

```python
def test_run_exposes_sustainability_flags(monkeypatch, config_dir, tmp_path):
    def health(code, cache_dir, cache_days=30):
        return {"eps_growth": -12.6, "eps_period": "2026-06-30",
                "op_cash_per_share": 0.5, "payout_stmt": None,
                "eps_history": [], "cash_yoy_history": [],
                "debt_ratio": 70.0, "debt_ratio_yoy": 13.0, "interest_cover": 1.4}

    _patch_data(monkeypatch, **_happy_overrides(get_financial_health=health))

    scan = main.run_scan(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)
    item = next(r for r in scan["stock_results"] if r["code"] == "000001")

    assert [f["key"] for f in item["sustainability_flags"]] == [
        "eps_decline", "cash_cover", "debt_jump", "interest_cover",
    ]
    assert item["sustainability"]["debt_ratio_yoy"] == pytest.approx(13.0)


def test_run_sustainability_flags_empty_when_health_unavailable(monkeypatch, config_dir, tmp_path):
    # get_financial_health 走默认替身（返回 None）→ sustainability 为 None，
    # flags 必须是空列表而非 None（reporter 依赖这个区分）
    _patch_data(monkeypatch, **_happy_overrides())

    scan = main.run_scan(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)
    item = next(r for r in scan["stock_results"] if r["code"] == "000001")

    assert item["sustainability"] is None
    assert item["sustainability_flags"] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/test_main.py -k sustainability -v`
Expected: FAIL —— `KeyError: 'sustainability_flags'`

- [ ] **Step 3: 实现 `main.py`**

- `_sustainability()` 在返回 dict 中透传四个新键（`debt_ratio_yoy`、`interest_cover` 直接从 `health` 取；两个序列原样带过）。`cash_cover` 算法不动。
- `_result()` 增加一行：

```python
"sustainability_flags": scorer.evaluate_sustainability(
    sustainability, rules_section.get("sustainability")
),
```

注意 `_result` 的其他调用点（基金）传 `sustainability=None` → 得 `[]`，符合契约。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_main.py
git commit -m "feat(main): result 契约新增 sustainability_flags，红标明细随数据层透传"
```

---

### Task 4: 展示层消费 flags

**Files:**
- Modify: `src/reporter.py`（`_risk_text` 392-413、`render_detail` 265-360、`_sustainability_phrases` 449-469）
- Modify: `CLAUDE.md`
- Test: `tests/test_reporter.py`（452-532 行）

**Interfaces:**
- Consumes: `sustainability_flags`（Task 3）与既有 sustainability dict
- Produces: `_sustainability_phrases(flags: list[dict] | None, sustainability: dict | None = None) -> list[str]`

- [ ] **Step 1: 写失败测试**

文件顶部 import 需补 `ranked_rows`、`render_detail`（现只 import 了 `_competition_ranks, _signals, render_report`）。

改写既有三个用例（它们目前直接构造 sustainability dict 让 `_sustainability_phrases` 自行判断）为构造 flags，并追加：

```python
def test_sustainability_phrases_render_all_keys():
    from src.reporter import _sustainability_phrases

    def phrase(flag, period=None):
        return _sustainability_phrases([flag], {"eps_period": period})[0]

    assert phrase({"key": "eps_decline", "value": -13.0}, "2026-06-30") == "盈利下滑 13%（2026中报）"
    assert phrase({"key": "eps_decline", "value": -13.0}) == "盈利下滑 13%"
    assert phrase({"key": "eps_repeated", "value": 3, "window": 4}) == "盈利连续下滑 3/4 期"
    assert phrase({"key": "negative_cash", "value": -0.1}) == "经营现金流为负"
    assert phrase({"key": "cash_decline", "value": -22.0}) == "现金流下滑 22%"
    assert phrase({"key": "cash_repeated", "value": 3, "window": 4}) == "现金流连续下滑 3/4 期"
    assert phrase({"key": "cash_cover", "value": 118.0}) == "分红超现金流 118%"
    assert phrase({"key": "debt_jump", "value": 12.0}) == "负债率上升 12pp"
    assert phrase({"key": "interest_cover", "value": 1.4}) == "利息保障 1.4 倍"


def test_risk_column_holds_six_items():
    # 4 条可持续性 + 流动性提示 + 高位提示 = 6 项，全部保留（原上限 4 会砍掉后两项）
    result = _result("600015", "华夏银行", 70.0, scores={"dividend_yield": 1.0})
    result["sustainability_flags"] = [
        {"key": "eps_repeated", "dimension": "profit", "value": 3, "window": 4, "penalty": 15.0},
        {"key": "negative_cash", "dimension": "cashflow", "value": -0.1, "penalty": 15.0},
        {"key": "cash_cover", "dimension": "coverage", "value": 200.0, "penalty": 10.0},
        {"key": "debt_jump", "dimension": "debt", "value": 12.0, "penalty": 10.0},
    ]
    result["position"] = 82.0     # ≥ POSITION_HIGH → 高位提示
    result["turnover_wan"] = 1200.0  # < turnover_low(5000) → 流动性提示

    row = _table_rows(
        render_report([result], [], OUTPUT_CFG, 1.0, risk_hints={"turnover_low": 5000}),
        STOCK_HEADER,
    )[0]

    for phrase in ("盈利连续下滑 3/4 期", "经营现金流为负", "分红超现金流 200%",
                   "负债率上升 12pp", "日均成交 1200万", "120日高位 82%"):
        assert phrase in row


def test_sustainability_none_renders_no_warning():
    # Review Focus 5：sustainability 与 flags 均缺失 → 三处调用点都不出文案、不崩溃
    result = _result("000001", "平安银行", 80.0, scores={"dividend_yield": 1.0})
    result["sustainability"] = None
    result.pop("sustainability_flags", None)

    report = render_report([result], [], OUTPUT_CFG, 1.0)
    detail = render_detail(ranked_rows([result], OUTPUT_CFG)[0], RULES_SECTION)

    assert "盈利下滑" not in report and "利息保障" not in report
    assert "可持续性扣分" not in detail
```

（`RULES_SECTION` 若文件内没有现成常量，用 `{"stocks": {"indicators": {"dividend_yield": {"weight": 100, "thresholds": {"high": 4.0, "mid": 2.0}}}}, "funds": {"indicators": {}}}`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/test_reporter.py -v`
Expected: FAIL —— `_sustainability_phrases` 收到 flags 后 `AttributeError`／短语为空

- [ ] **Step 3: 实现 `src/reporter.py`**

- `_sustainability_phrases(flags, sustainability=None)`：按 flags 顺序逐条 `key → 文案`（映射见 spec 表格），**不判断任何阈值**；`eps_decline` 的报告期从 `sustainability.get("eps_period")` 取并经 `_report_period_label` 标注；`eps_repeated`/`cash_repeated` 文案用 `flag["value"]` / `flag["window"]`；`debt_jump` 用 `f"{value:.0f}pp"`、`interest_cover` 用 `f"{value:.1f} 倍"`、`cash_decline` 用 `f"{abs(value):.0f}%"`。未知 key → 跳过（不崩溃）。
- 三个调用点改传 flags：`_risk_text`（`reporter.py:203`，新增形参 `flags`）与 `render_detail` 的两处（`reporter.py:319`、`reporter.py:404`）。`_risk_text` 的 sustainability 形参保留（`eps_period` 与「是否有扣分」判断仍用）。`render_detail` 的 `apply_sustainability_penalty` 调用（`reporter.py:290`）保留。
- `_risk_text` 的 `items[:4]` → `items[:6]`。
- **`CLAUDE.md` 同步**：result dict 契约加 `sustainability_flags`；「打分模型的方向性约定」段的红标描述改为四维度、封顶 45、单期盈利下滑 −5%。

- [ ] **Step 4: 运行全量测试**

Run: `.venv/bin/pytest -v`
Expected: 全绿（含 `tests/test_interactive.py` 与 `tests/test_reporter.py` 的既有用例）

- [ ] **Step 5: 提交**

```bash
git add src/reporter.py CLAUDE.md tests/test_reporter.py
git commit -m "feat(reporter): 风险列改用红标明细渲染，上限提到 6 项；同步 CLAUDE.md"
```

---

## 收尾验证

全部 Task 完成后，用真实配置跑一次端到端（会真实联网，需数分钟）：

```bash
.venv/bin/python main.py --no-interactive
```

检查三件事：**① 风险列出现新的负债/连续恶化文案**（四大行的资产负债率若被误报即为回归，必须没有）；**② 总分与上次运行有差异且差异可解释**（单期盈利下滑扣分减半 + 新增判据）；**③ 日志中没有 `KeyError`/`TypeError` 且覆盖列没有大面积掉到 N/A**（掉则说明 schema 升级后重取失败）。
