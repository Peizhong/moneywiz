"""红利指数成分股的定期刷新。

``dividend_index.yaml`` 的 ``updated_at`` 超过 ``ttl_days`` 时向中证指数官网
重新拉取；新增成分股写入 ``added`` 字段（YYYY-MM-DD），调出的个股从文件中移除。
刷新失败时沿用现有名单，绝不中断运行。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src import data

logger = logging.getLogger(__name__)

FILE_HEADER = (
    "# 红利指数成分股，自动维护：\n"
    "# updated_at 超过 index_refresh_days（rules.yaml，默认 14 天）时自动向中证指数官网刷新；\n"
    "# 新加入的成分股带 added 字段（YYYY-MM-DD），并在报告表格中标注 (新增)。\n"
)


def refresh_constituents(
    index_path: Path, index_code: str, ttl_days: int, today: date | None = None
) -> dict:
    """过期则刷新成分股文件；返回含有效名单的结果 dict。

    返回 ``{"refreshed", "added", "removed", "error", "constituents"}``：
    ``constituents`` 是刷新后（或沿用）的条目列表（``code/name/added``）。
    """
    today = today or date.today()
    path = Path(index_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updated_at = datetime.fromisoformat(str(payload.get("updated_at")))
    old_entries = [dict(item) for item in payload.get("constituents") or []]

    if datetime.now() - updated_at < timedelta(days=ttl_days):
        return _result(refreshed=False, old_entries=old_entries)

    fresh = data.get_index_constituents(index_code)
    if fresh is None:
        logger.warning(
            "成分股刷新失败，沿用现有名单（updated_at=%s）", payload.get("updated_at")
        )
        return _result(refreshed=False, error="获取失败", old_entries=old_entries)

    old_by_code = {str(item.get("code")): item for item in old_entries}
    entries, added = [], []
    for row in fresh.itertuples():
        code, name = str(row.code), str(row.name)
        entry = {"code": code, "name": name}
        old = old_by_code.get(code)
        if old is not None and old.get("added"):
            entry["added"] = str(old["added"])  # 原有 added 保留
        elif old is None:
            entry["added"] = today.isoformat()
            added.append(code)
        entries.append(entry)

    removed = sorted(set(old_by_code) - {entry["code"] for entry in entries})
    new_payload = {
        "index_code": index_code,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "constituents": entries,
    }
    path.write_text(
        FILE_HEADER + yaml.safe_dump(new_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    if added:
        names = "、".join(
            f"{e['code']} {e['name']}" for e in entries if e["code"] in added
        )
        logger.info("成分股更新：新增 %d 只（%s）", len(added), names)
    if removed:
        logger.info("成分股更新：调出 %d 只（%s）", len(removed), "、".join(removed))
    return _result(
        refreshed=True, added=added, removed=removed, entries=entries
    )


def _result(refreshed, added=None, removed=None, error=None, entries=None, old_entries=None):
    return {
        "refreshed": refreshed,
        "added": added or [],
        "removed": removed or [],
        "error": error,
        "constituents": entries if entries is not None else (old_entries or []),
    }


def new_constituent_codes(entries: list[dict], ttl_days: int, today: date | None = None) -> set[str]:
    """``added`` 在 ``ttl_days`` 之内的成分股代码集合（用于报告里的 (新增) 标注）。"""
    today = today or date.today()
    cutoff = today - timedelta(days=ttl_days)
    codes = set()
    for entry in entries:
        added = entry.get("added")
        if not added:
            continue
        try:
            if date.fromisoformat(str(added)) > cutoff:
                codes.add(str(entry.get("code")))
        except ValueError:
            continue
    return codes
