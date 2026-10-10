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

SNAPSHOT_SCHEMA = 2  # 快照 JSON 结构版本：改字段时必须升版本


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

    ``scan`` 是 main.run_scan 的返回值（含 market/elapsed/as_of
    三个快照专用键）。返回 JSON 文件路径。
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
    }
    json_path = snapshot_dir / f"{day}.json"
    _atomic_write(
        json_path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    _atomic_write(snapshot_dir / f"{day}.txt", scan["report"])
    return json_path
