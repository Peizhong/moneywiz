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
