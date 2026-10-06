"""红利指数成分股的定期刷新（支持多个指数）。

``dividend_index.yaml`` 中每个指数的 ``updated_at`` 超过 ``ttl_days`` 时向中证指数
官网重新拉取；新增成分股写入 ``added`` 字段（YYYY-MM-DD），调出的个股从文件中移除。
刷新失败时该指数沿用现有名单，绝不中断运行。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src import data

logger = logging.getLogger(__name__)

FILE_HEADER = (
    "# 红利指数成分股，自动维护（多个指数，各自独立的 updated_at）：\n"
    "# 某指数 updated_at 超过 index_refresh_days（rules.yaml，默认 14 天）时自动向中证指数\n"
    "# 官网刷新；新加入的成分股带 added 字段（YYYY-MM-DD），并在报告表格中标注 (新增)。\n"
)


def refresh_indices(
    index_path: Path, indices, ttl_days: int, today: date | None = None
) -> dict:
    """逐个指数检查 TTL 并刷新，最后重写文件；返回合并结果。

    ``indices`` 为配置中的 ``IndexCfg`` 列表（其 ``updated_at`` 与 ``constituents``
    即文件当前内容）。返回 ``{"refreshed", "added", "removed", "error", "constituents"}``：
    ``constituents`` 是全部指数刷新后（或沿用）的条目合并列表（``code/name/added``），
    供扫描使用。文件总是按当前内容重写（格式归一），失败指数保持原 ``updated_at``。
    """
    today = today or date.today()
    path = Path(index_path)
    merged: list[dict] = []
    added_all: list[str] = []
    removed_all: list[str] = []
    errors: list[str] = []
    refreshed_any = False
    output: list[dict] = []

    for index in indices:
        old_entries = [
            {"code": c.code, "name": c.name, **({"added": c.added} if c.added else {})}
            for c in index.constituents
        ]
        entries, updated_at = old_entries, index.updated_at

        if datetime.now() - index.updated_at >= timedelta(days=ttl_days):
            fresh = data.get_index_constituents(index.index_code)
            if fresh is None:
                logger.warning(
                    "成分股刷新失败（%s %s），沿用现有名单",
                    index.index_code,
                    index.index_name,
                )
                errors.append(f"{index.index_code} 获取失败")
            else:
                entries, added, removed = _rebuild_entries(fresh, old_entries, today)
                updated_at = datetime.now()
                refreshed_any = True
                added_all.extend(added)
                removed_all.extend(removed)
                if added:
                    names = "、".join(
                        f"{e['code']} {e['name']}"
                        for e in entries
                        if e["code"] in set(added)
                    )
                    logger.info(
                        "成分股更新（%s %s）：新增 %d 只（%s）",
                        index.index_code,
                        index.index_name,
                        len(added),
                        names,
                    )
                if removed:
                    logger.info(
                        "成分股更新（%s %s）：调出 %d 只（%s）",
                        index.index_code,
                        index.index_name,
                        len(removed),
                        "、".join(removed),
                    )

        merged.extend(entries)
        output.append(
            {
                "index_code": index.index_code,
                "index_name": index.index_name,
                "updated_at": updated_at.isoformat(timespec="seconds"),
                "constituents": entries,
            }
        )

    path.write_text(
        FILE_HEADER + yaml.safe_dump({"indices": output}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "refreshed": refreshed_any,
        "added": added_all,
        "removed": removed_all,
        "error": "；".join(errors) if errors else None,
        "constituents": merged,
    }


def _rebuild_entries(fresh, old_entries: list[dict], today: date) -> tuple[list[dict], list[str], list[str]]:
    """按最新名单重建条目：保留原有 added、标记新成员、列出调出。"""
    old_by_code = {str(entry["code"]): entry for entry in old_entries}
    entries: list[dict] = []
    added: list[str] = []
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
    return entries, added, removed


def new_constituent_codes(
    entries: list[dict], ttl_days: int, today: date | None = None
) -> set[str]:
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
