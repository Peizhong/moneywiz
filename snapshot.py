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
