# 取数缓存按 A/B/C 分档 Implementation Plan

**Goal:** 把「一个 `market_cache_hours` 管所有取数」拆成三档 TTL：实时类每次运行都取最新、日频类跨交易日失效、慢变类长缓存。

**Architecture:** 只动取数层的缓存策略与配置接线，不碰任何指标公式、打分、展示。`src/data.py` 的 `_cached()` 增加必填的 `tier` 参数（`live`/`daily`/`slow`），`configure_cache()` 由单个 `ttl_hours` 改为按档接收秒数；`main.py` 从 `rules.yaml` 的三个新键换算后传入。**每个 `_cached` 调用点必须显式声明档位**——档位是取数语义的一部分，不接受默认值（默认值会让新增取数函数悄悄继承一个不合适的 TTL）。

**Tech Stack:** Python 3.12 / pytest；无新增依赖。

## Global Constraints

- **测试绝不联网**；沿用各 test_data_*.py 既有的 monkeypatch 风格（真实上游列名的帧）。
- **一律用 `.venv/bin/pytest`**。
- **零分数变化、零名单变化**：不改 `src/indicators.py`、`src/scorer.py`、`src/reporter.py` 的任何逻辑。
- **不改既有独立缓存的取值**：`pe_cache_days: 7`、`financial_cache_days: 30`、`index_refresh_days: 14` 三个键与它们各自的 JSON 缓存文件一律不动。
- **不改 `src/cache.py`**：`Cache.get(key, ttl_seconds)` 的语义（`now - fetched_at >= ttl` 即过期）保持不变，分档只发生在 `src/data.py`。
- docstring / 注释 / 提交信息一律中文。提交信息末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## 关键口径决策

### 1. 三档划分与默认 TTL

| 档 | 成员（缓存键） | 默认 TTL | 配置键 | 理由 |
|---|---|---|---|---|
| **live** | `spot:v2:`（个股行情）、`fund_quotes:`（ETF/LOF 行情）、`fund_latest_nav:` | **0（不缓存）** | `quote_cache_minutes: 0` | 价格是股息率/折溢价的分母，随分钟变；批量接口每次运行只多 1 次请求（个股行情一次拉全表、每类基金一次），代价可忽略。东财熔断（30 分钟持久化）兜住了回退源的重试成本。 |
| **daily** | `kline:`、`kline_raw:`、`fund_nav:`、`index_pe:`、`index_div_yield:`、`bond_10y:` | **6 小时** | `daily_cache_hours: 6` | 每个交易日只变一次。6h < 隔夜间隔（≥15h）→ 隔夜必失效；也覆盖「上午跑一次、盘后再跑一次」（≥6h 即过期，`>=` 判定）。**已知边界**：盘中午间两次运行若间隔 <6h，第二次会沿用上午的快照——这不是交易日历感知的失效，想要严格每次运行都取最新就把该键设为 0。 |
| **slow** | `dividend:`（个股分红明细）、`fund_dividends:`、`fund_overview:` | **7 天** | `slow_cache_days: 7` | 分红明细一年实施 1~2 次；基金概况（规模）季度更新、跟踪标的静态。 |

### 2. 个股分红明细放 slow 是安全的（不是疏漏）

直觉上「股息率是核心指标，分红数据不该缓存 7 天」，但 TTM 口径下这不成立：`calc_dividend_yield` 的分母（近 12 个月分红合计）在除息日前后**近似不变**——新的分红进入窗口的同时，去年同期的分红滚出窗口，两者只差分红增长率。因此除息日当天价格下跌、而分红列表尚未刷新时，股息率不会被系统性算低，误差量级是个位数百分点的分红增速。分红年数/分红趋势是年度粒度，一周的滞后不影响判定。

### 3. 为什么 `live` 的默认是 0（不缓存）而不是一个短 TTL

用户在 `market_cache.db` 里的 `spot:v2:{codes}` 键对整个候选池只请求一次，短 TTL 与 0 的差别只体现在「同一分钟内连跑两次」这种场景，而那正是希望拿到最新价格的场景。`ttl_seconds <= 0` 时 `_cached` 直接跳过缓存读写（不写库、不读库），避免留下永远读不到的垃圾行。

### 4. `market_cache_hours` 移除，不做兼容别名

三档已覆盖它的全部职责，保留一个不再被读取的键只会让人以为它还有效。`config.py` 的 `DEFAULT_DATA` 同步替换；用户自己 `rules.yaml` 里残留的 `market_cache_hours` 不会被校验拒绝（`_section_with_defaults` 只做合并），但也不再有任何效果。

## Review Focus

1. **档位分类错配**：把 `spot` 误标 `daily`（价格最多旧 6 小时）或把 `kline` 误标 `live`（每次运行多 160 次请求）。→ 每个取数入口一条分类测试钉死。
2. **`ttl_seconds <= 0` 时仍写缓存**：会造成「每次运行都白写一次库」且语义含糊。→ 测试断言此时 `market_cache.db` 里该键不存在。
3. **`configure_cache(None)` 关闭缓存后档位残留**：`tests/conftest.py` 每个用例前后都调它；若 `None` 分支只清 `_market_cache` 而不复位档位，跨用例会串味。
4. **`main.py` 的换算**：`quote_cache_minutes`(分钟) / `daily_cache_hours`(小时) / `slow_cache_days`(天) 三个单位不同，乘错系数会让默认值差 60 倍且静默生效。→ main 级测试断言传给 `configure_cache` 的确切秒数。
5. **`market_cache_hours` 残留引用**：`src/`、`tests/`、`README.md`、`CLAUDE.md`、`config/rules.yaml` 里都要清干净——留一处就会出现「配了不生效」的困惑。

---

### Task 1: `src/data.py` 分档机制

**Files:**
- Modify: `src/data.py`（`configure_cache` 约 152 行、`_cached` 约 162 行、12 处 `_cached` 调用点）
- Test: `tests/test_data_stock.py`、`tests/test_data_fund.py`、`tests/test_data_market.py`

**Interfaces:**
- Produces:
  - `TTL_LIVE = "live"`、`TTL_DAILY = "daily"`、`TTL_SLOW = "slow"`（模块常量）
  - `configure_cache(cache_dir, *, live_seconds: float = 0, daily_seconds: float = 6 * 3600, slow_seconds: float = 7 * 86400) -> None`
  - `_cached(key: str, fetch, tier: str)`

- [ ] **Step 1: 写失败测试**

在三个 test_data_*.py 里各加分类测试（沿用文件既有的 monkeypatch helper）。

- [ ] **Step 2: 运行确认失败**（`_cached() missing 1 required positional argument: 'tier'` / 未知参数）

- [ ] **Step 3: 实现分档**

- [ ] **Step 4: 迁移既有用例的 `configure_cache(ttl_hours=…)` 调用**

- [ ] **Step 5: 全量测试**

---

### Task 2: 配置与接线

**Files:**
- Modify: `src/config.py:16-23`（`DEFAULT_DATA`）
- Modify: `config/rules.yaml`（`data` 段）
- Modify: `main.py:446-448`（`configure_cache` 调用）
- Test: `tests/test_config.py`、`tests/test_main.py`

- [ ] **Step 1: 写失败测试**（默认值断言 + main 传入的确切秒数）
- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现**
- [ ] **Step 4: 全量测试**

---

### Task 3: 文档

**Files:**
- Modify: `README.md`（「运行」节的缓存说明、「已知限制」的「数据时效」）
- Modify: `CLAUDE.md`（「缓存分层」表）
- Modify: `docs/superpowers/plans/2026-10-06-dividend-screener.md`（如涉及取数缓存描述）

- [ ] **Step 1: 三处同步三档 TTL 与默认值**
- [ ] **Step 2: `grep -rn "market_cache_hours" .` 确认零残留**
- [ ] **Step 3: 提交**
