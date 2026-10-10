# 快照与历史分析 skill 化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 moneywiz 增加快照能力（每跑一次写当日 JSON+文本双份快照、同日覆盖）与 `.claude/skills/moneywiz` skill 定义，使 Claude Code 能运行筛选并对比历史快照生成分析。

**Architecture:** 快照是消费 `main.run_scan()` 返回值的独立组件——`src/snapshot.py` 纯函数写盘、根目录 `snapshot.py` 薄 CLI 编排；`main.py` 只给返回 dict 加四个键。排名/信号复用 `reporter.ranked_rows`，不复制任何渲染逻辑；分析由 Claude 按 SKILL.md 指引生成。

**Tech Stack:** Python 3.12+、pytest、json、numpy（归一化序列化）；无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-10-skill-snapshot-design.md`

## Global Constraints

- 测试绝不联网（conftest `_no_real_network` 已兜底）；本计划新代码不调数据层：Task 2 纯函数、Task 3 打桩 `run_scan`。
- 一律 `.venv/bin/pytest`（系统环境无依赖）。
- 快照条目必须经 `reporter.ranked_rows` 包装（spec 决策 2）——不得自写排序/信号逻辑。
- 不触碰 `indicators`/`scorer`/`reporter` 渲染逻辑与任何取数；分数/信号/报告内容零变化。
- `main.py` 只加返回键，不改编排。
- 快照写盘用「临时文件 + `os.replace`」原子覆盖；数值对比只从 JSON。
- 代码、注释、提交信息以中文为主；提交信息末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 快照 JSON 顶层 `"schema": 1`——将来改字段必须升版本（spec 决策 6）。

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `main.py` | 修改 | `run_scan()` 返回 dict 加 `market`/`fund_results`/`elapsed`/`as_of` 四键 |
| `src/snapshot.py` | 新建 | `write_snapshot(scan, snapshot_dir)` 纯函数：`ranked_rows` 包装 + numpy 归一化 + 原子写盘 |
| `snapshot.py` | 新建 | 薄 CLI：`run_scan()` → `write_snapshot()` → 打印路径与摘要行 |
| `.claude/skills/moneywiz/SKILL.md` | 新建 | skill 定义：运行指引 + Claude 历史对比分析剧本 |
| `tests/test_snapshot.py` | 新建 | 快照纯函数与 CLI 测试（不联网） |
| `tests/test_main.py` | 修改 | 追加 `run_scan` 四键断言 |
| `.gitignore` | 修改 | 加 `snapshots/` |
| `README.md`、`CLAUDE.md` | 修改 | 快照用法与架构说明 |

---

### Task 1: `run_scan()` 返回快照所需四键

**Files:**
- Modify: `main.py`（`run_scan` 的 return，约 495-519 行）
- Test: `tests/test_main.py`（文件末尾追加）

**Interfaces:**
- Consumes: 无（现有 `run_scan` 内部变量）
- Produces: `run_scan()` 返回 dict 新增键 `"market"`（dict|None）、`"fund_results"`（list[dict]）、`"elapsed"`（float）、`"as_of"`（date）——Task 2 的 `write_snapshot(scan, ...)` 依赖这四个键。

- [ ] **Step 1: 写失败测试**

在 `tests/test_main.py` 文件末尾追加：

```python
def test_run_scan_exposes_snapshot_fields(monkeypatch, config_dir, tmp_path):
    """快照入口依赖 run_scan 返回的四个新键：market/fund_results/elapsed/as_of。"""
    _patch_data(monkeypatch, **_happy_overrides())

    scan = main.run_scan(config_dir=config_dir, cache_dir=tmp_path, as_of=AS_OF)

    assert scan["as_of"] == AS_OF
    assert isinstance(scan["elapsed"], float) and scan["elapsed"] >= 0
    assert scan["market"]["dividend_yield"] == pytest.approx(4.2)
    assert scan["market"]["bond_10y"] == pytest.approx(1.8)
    assert [r["code"] for r in scan["stock_results"]] == [STOCK_A, STOCK_B]
    assert [r["code"] for r in scan["fund_results"]] == [FUND_CODE]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/pytest tests/test_main.py::test_run_scan_exposes_snapshot_fields -v`
Expected: FAIL，`KeyError: 'as_of'`

- [ ] **Step 3: 实现**

`main.py` 的 `run_scan` 结尾（约 495-519 行）改为：

```python
    elapsed = time.monotonic() - started
    return {
        "report": reporter.render_report(
            stock_results,
            fund_results,
            cfg.output,
            elapsed,
            market=market,
            risk_hints=cfg.rules["stocks"].get("risk_hints"),
            position_window=kline_days,
        ),
        "stock_results": stock_results,
        "fund_results": fund_results,
        "market": market,
        "elapsed": elapsed,
        "as_of": as_of,
        "rules": cfg.rules,
        "output": cfg.output,
    }
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: 全 PASS（新键向后兼容，现有用例无返回键集合断言，不受影响）

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_main.py
git commit -m "feat(main): run_scan 返回 market/fund_results/elapsed/as_of 供快照消费

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: `src/snapshot.py` 快照写盘模块

**Files:**
- Create: `src/snapshot.py`
- Test: `tests/test_snapshot.py`（本任务创建，Task 3 继续追加）

**Interfaces:**
- Consumes: `run_scan()` 的返回 dict（Task 1 的四键 + `report`/`stock_results`/`output`）；`src.reporter.ranked_rows(results, output_cfg)`。
- Produces: `write_snapshot(scan: dict, snapshot_dir: Path) -> Path`（返回 JSON 文件路径）——Task 3 的 CLI 依赖它。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_snapshot.py`（完整内容，含 Task 3 的 CLI 用例，本任务先只跑 write_snapshot 部分）：

```python
"""快照写盘（src/snapshot.write_snapshot）与快照 CLI（snapshot.main）的测试。

纯函数 + 打桩 run_scan，不联网、不碰仓库 config/。条目经 reporter.ranked_rows
包装（spec 决策 2：排名/信号只允许这一处实现）；数值对比只从 JSON 取。
"""

import json
from datetime import date

import numpy as np

from src.snapshot import write_snapshot

AS_OF = date(2026, 10, 10)


def _scan(**overrides):
    """最小合法的 run_scan 返回值（含快照专用四键）。"""
    scan = {
        "report": "扫描 1 只标的（股票 1 / 基金 0），数据不足 0，耗时 1.0s",
        "stock_results": [
            {
                "code": "600036",
                "name": "招商银行",
                "total": 85.0,
                "values": {"dividend_yield": 3.2},
                "scores": {"dividend_yield": 1.0},
                "missing": [],
                "tech": None,
                "position": None,
                "sustainability": None,
                "sustainability_flags": [],
                "turnover_wan": None,
            }
        ],
        "fund_results": [],
        "rules": {"stocks": {"indicators": {}}},
        "output": {"buy_top_n": 5, "avoid_bottom_n": 5},
        "market": {
            "dividend_yield": 4.2,
            "bond_10y": 1.8,
            "spread": 2.4,
            "index_pe_position": 42.0,
        },
        "elapsed": 1.5,
        "as_of": AS_OF,
    }
    scan.update(overrides)
    return scan


def test_write_snapshot_creates_json_and_txt(tmp_path):
    path = write_snapshot(_scan(), tmp_path)

    json_path = tmp_path / "2026-10-10.json"
    txt_path = tmp_path / "2026-10-10.txt"
    assert path == json_path
    assert json_path.is_file() and txt_path.is_file()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["as_of"] == "2026-10-10"
    assert "created_at" in data
    assert data["elapsed_s"] == 1.5
    assert data["market"]["dividend_yield"] == 4.2
    # 条目经 ranked_rows 包装：排名/并列/信号 + 完整 item（spec 决策 2）
    row = data["stocks"][0]
    assert set(row) == {"rank", "tied_count", "signal", "item"}
    assert row["rank"] == 1 and row["signal"] == "买入"
    assert row["item"]["code"] == "600036" and row["item"]["total"] == 85.0
    assert data["funds"] == []
    # txt 为报告原文
    assert txt_path.read_text(encoding="utf-8") == _scan()["report"]


def test_write_snapshot_overwrites_same_day(tmp_path):
    """当天重复执行只保留最后一次：同名覆盖，不产生第二个文件。"""
    write_snapshot(_scan(report="第一次"), tmp_path)
    write_snapshot(_scan(report="第二次"), tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-10-10.json",
        "2026-10-10.txt",
    ]
    assert (tmp_path / "2026-10-10.txt").read_text(encoding="utf-8") == "第二次"


def test_write_snapshot_creates_snapshot_dir(tmp_path):
    target = tmp_path / "nested" / "snapshots"

    write_snapshot(_scan(), target)

    assert (target / "2026-10-10.json").is_file()


def test_write_snapshot_normalizes_numpy_values(tmp_path):
    """numpy 标量 → 原生类型、NaN → None，否则 json.dump 会崩或写出不可读值。"""
    scan = _scan()
    scan["stock_results"][0]["values"]["pe_vs_industry"] = np.float64(-12.5)
    scan["stock_results"][0]["sustainability"] = {
        "interest_cover": np.float64(np.nan)
    }
    scan["market"]["index_pe_position"] = np.int64(42)

    write_snapshot(scan, tmp_path)
    data = json.loads((tmp_path / "2026-10-10.json").read_text(encoding="utf-8"))

    item = data["stocks"][0]["item"]
    assert item["values"]["pe_vs_industry"] == -12.5
    assert isinstance(item["values"]["pe_vs_industry"], float)
    assert item["sustainability"]["interest_cover"] is None  # NaN → None
    assert data["market"]["index_pe_position"] == 42
    assert isinstance(data["market"]["index_pe_position"], int)
```

（Task 3 的 CLI 用例在 Task 3 追加到本文件，本任务先不写。）

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/pytest tests/test_snapshot.py -v`
Expected: 全部 FAIL，`ModuleNotFoundError: No module named 'src.snapshot'`

- [ ] **Step 3: 实现**

创建 `src/snapshot.py`：

```python
"""快照写盘：把 run_scan() 的结果写成按日命名的 JSON+文本双份快照。

快照是消费 main.run_scan 返回值的独立组件：条目经 reporter.ranked_rows
包装（排名/并列/信号只允许这一处实现，见 reporter.ranked_rows 的说明），
数值归一化为 JSON 可序列化的原生类型（NaN → None）。同日重复运行覆盖同名
文件（文件名按 scan["as_of"] 的日期），即「当天只保留最后一次」。
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np

from src.reporter import ranked_rows

SNAPSHOT_SCHEMA = 1  # 快照 JSON 结构版本：改字段时必须升版本


def _native(value):
    """JSON 可序列化：numpy 标量 → Python 原生类型，NaN → None；dict/list 递归。"""
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return None if math.isnan(number) else number
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _atomic_write(path: Path, content: str) -> None:
    """临时文件 + os.replace：写一半崩溃不留半截快照（坏快照会污染历史分析）。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_snapshot(scan: dict, snapshot_dir: Path) -> Path:
    """把 run_scan() 的结果写成 YYYY-MM-DD.{json,txt} 两份快照，同日覆盖。

    ``scan`` 是 main.run_scan 的返回值（含 market/fund_results/elapsed/as_of
    四个快照专用键）。返回 JSON 文件路径。
    """
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    day = scan["as_of"].isoformat()
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "as_of": day,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": _native(scan["elapsed"]),
        "market": _native(scan["market"]),
        "stocks": [
            _native(row) for row in ranked_rows(scan["stock_results"], scan["output"])
        ],
        "funds": [
            _native(row) for row in ranked_rows(scan["fund_results"], scan["output"])
        ],
    }
    json_path = snapshot_dir / f"{day}.json"
    _atomic_write(
        json_path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    _atomic_write(snapshot_dir / f"{day}.txt", scan["report"])
    return json_path
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/pytest tests/test_snapshot.py -v`
Expected: 4 个用例全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/snapshot.py tests/test_snapshot.py
git commit -m "feat(snapshot): 快照写盘模块（JSON+文本双份、同日覆盖、numpy 归一化）

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: `snapshot.py` 快照 CLI

**Files:**
- Create: `snapshot.py`（仓库根目录，与 main.py 平级）
- Test: `tests/test_snapshot.py`（追加两个 CLI 用例）

**Interfaces:**
- Consumes: `main.run_scan`（`run_scan(as_of=date.today())`）、`src.snapshot.write_snapshot(scan, snapshot_dir)`、`src.config.ConfigError`。
- Produces: `snapshot.main(snapshot_dir: Path = Path("snapshots")) -> int`（退出码 0/1）——`.claude/skills/moneywiz/SKILL.md`（Task 4）调用 `.venv/bin/python snapshot.py`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_snapshot.py` 的 import 区（现有 `import json`、`from datetime import date`、`import numpy as np`、`from src.snapshot import write_snapshot` 保留）追加三行：

```python
import pytest

import snapshot as snapshot_cli
from src import config
```

并在文件末尾追加：

```python
import pytest

import snapshot as snapshot_cli
from src import config


def _scan_stub():
    """与 _scan 同构；CLI 不依赖实现细节，单独构造。"""
    return _scan(
        report="【股票】\n扫描 1 只标的（股票 1 / 基金 0），数据不足 0，耗时 1.0s"
    )


def test_snapshot_cli_writes_snapshot_and_prints_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(snapshot_cli, "run_scan", lambda **kw: _scan_stub())

    assert snapshot_cli.main(snapshot_dir=tmp_path) == 0

    assert (tmp_path / "2026-10-10.json").is_file()
    captured = capsys.readouterr()
    assert str(tmp_path / "2026-10-10.json") in captured.out
    assert "扫描 1 只标的" in captured.out  # 摘要行


def test_snapshot_cli_config_error_returns_one_without_snapshot(
    monkeypatch, tmp_path, capsys
):
    def failing_scan(**kw):
        raise config.ConfigError("stocks.yaml: 配置文件不存在")

    monkeypatch.setattr(snapshot_cli, "run_scan", failing_scan)

    assert snapshot_cli.main(snapshot_dir=tmp_path) == 1

    assert list(tmp_path.iterdir()) == []  # 配置错误不写快照
    captured = capsys.readouterr()
    assert "stocks.yaml: 配置文件不存在" in captured.err
```

- [ ] **Step 2: 跑测试验证失败**

Run: `.venv/bin/pytest tests/test_snapshot.py -v`
Expected: 2 个新用例 FAIL，`ModuleNotFoundError: No module named 'snapshot'`

- [ ] **Step 3: 实现**

创建 `snapshot.py`（仓库根目录）：

```python
"""快照 CLI：跑一遍筛选并把结果写成当日快照（同日重复运行覆盖）。

与 main.py 的差别：不进交互式详情（skill/定时场景非 TTY），只写快照并打印
快照路径与报告摘要行；配置错误退出码 1 且不写快照。
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

from main import run_scan
from src import config
from src.snapshot import write_snapshot

logger = logging.getLogger(__name__)


def main(snapshot_dir: Path = Path("snapshots")) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        scan = run_scan(as_of=date.today())
    except config.ConfigError as exc:
        logger.error("配置加载失败：%s", exc)
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    path = write_snapshot(scan, snapshot_dir)
    print(f"快照已写入 {path}（同日重复运行将覆盖）")
    print(scan["report"].splitlines()[-1])  # 摘要行：扫描 N 只标的…
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试验证通过**

Run: `.venv/bin/pytest tests/test_snapshot.py -v`
Expected: 6 个用例全 PASS

- [ ] **Step 5: 提交**

```bash
git add snapshot.py tests/test_snapshot.py
git commit -m "feat(snapshot): 快照 CLI 入口（跑筛选 → 写当日快照，配置错误不写盘）

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: `.claude/skills/moneywiz/SKILL.md` 与 `.gitignore`

**Files:**
- Create: `.claude/skills/moneywiz/SKILL.md`
- Modify: `.gitignore`（末尾追加一行）

**Interfaces:**
- Consumes: Task 3 的 `.venv/bin/python snapshot.py`、`snapshots/YYYY-MM-DD.{json,txt}` 目录布局。
- Produces: `/moneywiz` skill（Claude Code 触发入口）。

- [ ] **Step 1: 创建 SKILL.md**

创建 `.claude/skills/moneywiz/SKILL.md`：

```markdown
---
name: moneywiz
description: 红利投资筛选快照与分析。运行筛选脚本生成当日快照（当天重复运行自动覆盖），并对比历史快照分析榜单、信号、分数与板块温度计的变化。当用户想查看红利股票/基金筛选结果、跟踪标的排名变化或做历史对比时使用。
---

# 红利筛选快照与分析

在 moneywiz 仓库内运行。快照目录 `snapshots/`，每天最多一份
（`YYYY-MM-DD.json` 结构化结果 + `YYYY-MM-DD.txt` 报告文本）。

## 运行

```bash
.venv/bin/python snapshot.py
```

- 快照落盘 `snapshots/YYYY-MM-DD.{json,txt}`；**当天重复运行覆盖同名文件**（只保留最后一次）；
- 脚本打印 JSON 快照路径与报告摘要行；
- 不进入交互式详情（详情由本 skill 按需从 JSON 数据组织）。

## 分析（每次运行后必做）

1. 列出历史快照 `snapshots/*.json`，选参照点：**昨日**（最近一份更早日期的快照）、
   **约 7 天前**最近一份、**约 30 天前**最近一份——存在才用，不存在跳过；
2. 先读当前 txt 与参照 txt 抓全局变化，再回 JSON 核对具体数值；
3. 对比维度：
   - 买入区新进/跌出（`output.buy_top_n` 名额内）与名次显著升降；
   - 信号变化（买入↔观察↔末位）及归因：对照 `item.scores`/`item.values` 的变化；
   - 总分漂移显著的标的：对照 `item.values` 逐指标归因；
   - 板块温度计趋势：`market` 的利差（spread）、上证红利 PE 分位（index_pe_position）；
   - 红标变化：`item.sustainability_flags` 新增/消失；
   - `item.new_constituent` 新成分股；
4. 输出中文分析：变化摘要 + 值得注意的标的 + 带具体数字与快照日期的归因。

## 注意事项

- **数值对比只从 JSON 取**，不从 txt 表格解析（txt 是格式化渲染产物，数值有截断）；
- `item.missing` 数据不足导致的变化要如实说明，不当成真实变化；
- 快照是当日最后一次运行的结果；
- 不修改 `config/` 与 `snapshots/` 下的历史文件；
- 用户想看单只标的详情时，用 JSON 里该标的 `item` 的数据组织回答
  （`reporter.render_detail` 需要的字段都在 item 里），不要再跑一遍取数。
```

- [ ] **Step 2: .gitignore 追加**

`.gitignore` 末尾（`cache/` 之后）追加一行：

```
snapshots/
```

- [ ] **Step 3: 回归**

Run: `.venv/bin/pytest`
Expected: 全 PASS

- [ ] **Step 4: 提交**

```bash
git add .claude/skills/moneywiz/SKILL.md .gitignore
git commit -m "feat(skill): moneywiz skill 定义（快照运行 + 历史对比分析指引）与 snapshots/ gitignore

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: README 与 CLAUDE.md 文档

**Files:**
- Modify: `README.md`（「## 输出说明」之前插入新节）
- Modify: `CLAUDE.md`（Commands 节补一行；Architecture 节补一段）

**Interfaces:**
- Consumes: 前四个任务的成品（`snapshot.py`、`snapshots/`、`/moneywiz` skill）。

- [ ] **Step 1: README 插入「快照与历史分析」节**

在 README.md 的 `## 输出说明` 标题之前插入：

```markdown
## 快照与历史分析

`snapshot.py` 是快照入口：跑一遍筛选并把结果写成 `snapshots/YYYY-MM-DD.{json,txt}`
两份快照（结构化榜单/指标 JSON + 与 main 相同的报告文本）。**同一天重复运行覆盖
同名文件**（只保留最后一次），历史快照全部保留、不自动清理；`snapshots/` 已加入
`.gitignore`。

在 Claude Code 里输入 `/moneywiz`（skill 定义见 `.claude/skills/moneywiz/`）：
skill 会运行快照脚本，然后对比历史快照（默认参照昨日、约 7 天前、约 30 天前）
分析买入区进出、信号与总分变化、板块温度计趋势，输出中文变化分析。
数值对比一律以 JSON 为准，txt 仅供人读。
```

- [ ] **Step 2: CLAUDE.md 的 Commands 节补一行**

在 `CLAUDE.md` Commands 段的

```markdown
.venv/bin/python main.py --no-interactive   # 只打报表（管道/重定向/Docker 下本就不会进交互）
```

之后追加：

```markdown
.venv/bin/python snapshot.py   # 跑一遍筛选并写当日快照 snapshots/YYYY-MM-DD.{json,txt}（同日覆盖）
```

- [ ] **Step 3: CLAUDE.md 的 Architecture 节补一段**

在 `- **main.py 的候选池裁剪**` 段落之前插入：

```markdown
- **snapshot.py（根目录）与 src/snapshot.py** — 快照入口与写盘模块（`/moneywiz` skill 用）：`run_scan()` → `write_snapshot()` 写 `snapshots/YYYY-MM-DD.{json,txt}`（同日重复运行覆盖，历史全部保留，`.gitignore` 排除）。条目经 `reporter.ranked_rows` 包装（排名/信号只此一处实现）；数值归一化为 JSON 原生类型（NaN→None）；JSON 带 `schema` 版本号，改字段必须升版本；**数值对比只从 JSON 取**。`.claude/skills/moneywiz/SKILL.md` 是 skill 定义：运行 + 历史对比分析指引（分析由 Claude 生成，脚本不产出 diff）。
```

- [ ] **Step 4: 回归**

Run: `.venv/bin/pytest`
Expected: 全 PASS（纯文档改动）

- [ ] **Step 5: 提交**

```bash
git add README.md CLAUDE.md
git commit -m "docs: README/CLAUDE.md 补快照用法与 skill 架构说明

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 自审记录

- **Spec 覆盖**：spec 的模块详细设计五小节各对应 Task 1/2/3/4/5；关键口径决策 1-7 分别由 Task 2 的模块注释、`ranked_rows` 包装、Task 1 四键、原子覆盖、SKILL.md 注意事项、`_native` 归一化、Task 3 的 ConfigError 分支落实；测试策略的两组用例（write_snapshot 五个断言点 + CLI 两用例）已全部写入。
- **占位符**：无 TBD/TODO，所有代码与文档内容均为最终文本。
- **类型一致性**：`write_snapshot(scan, snapshot_dir) -> Path` 在 Task 2 定义、Task 3 消费，签名一致；`run_scan` 四键在 Task 1 定义、Task 2 的 `_scan` 与 Task 3 的 `_scan_stub` 同名同型；`snapshot.main(snapshot_dir)` 参数名与 SKILL.md 的调用方式一致。
