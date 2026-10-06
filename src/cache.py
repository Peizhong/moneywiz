"""通用取数缓存（SQLite，默认 24 小时）。

数据层在每次运行开始用 ``configure_cache`` 指定缓存位置与 TTL；命中时直接返回
上次的规范化结果（DataFrame 或 JSON 可序列化对象），24 小时内不再请求上游。
缓存自身故障（文件损坏、不可写）一律安静降级为"未命中"，绝不影响取数正确性。
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 3600
CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS cache ("
    "key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
)


class Cache:
    """键值缓存：DataFrame 以 JSON(split) 序列化，其余值要求 JSON 可序列化。"""

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(CREATE_TABLE)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def get(self, key: str, ttl_seconds: int, now: datetime | None = None):
        """未命中/过期/损坏 → None；命中 → 原值。"""
        now = now or datetime.now()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload, updated_at FROM cache WHERE key = ?", (key,)
                ).fetchone()
        except sqlite3.Error as exc:
            logger.debug("缓存读取失败（按未命中处理）：%s", exc)
            return None
        if row is None:
            return None

        payload, updated_at = row
        try:
            fetched_at = datetime.fromisoformat(updated_at)
        except ValueError:
            return None
        if now - fetched_at >= timedelta(seconds=ttl_seconds):
            return None

        try:
            envelope = json.loads(payload)
            if envelope.get("type") == "dataframe":
                # 字符串列（如 6 位代码 "000001"）必须按原 dtype 回读，否则
                # read_json 会把数字串推断成 int（前导零丢失，按代码查行情全部失配）。
                stored_dtypes = envelope.get("dtypes") or {}
                string_columns = {
                    column: str
                    for column, name in stored_dtypes.items()
                    if name in ("object", "string", "str")
                }
                frame = pd.read_json(
                    io.StringIO(envelope["data"]),
                    orient="split",
                    dtype=string_columns or None,
                )
                # 整数值的浮点列会被推断为 int64；本项目的数值列（收盘价/净值/
                # PE/分红/成交量）语义上均为 float，逐一还原 dtype。
                for column in frame.columns:
                    if pd.api.types.is_numeric_dtype(
                        frame[column]
                    ) and not pd.api.types.is_float_dtype(frame[column]):
                        frame[column] = frame[column].astype("float64")
                return frame
            return envelope["data"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug("缓存载荷损坏（按未命中处理）：%s", exc)
            return None

    def set(self, key: str, value, now: datetime | None = None) -> None:
        """写入缓存；DataFrame 走 JSON(split) 序列化。失败仅记录日志。"""
        now = now or datetime.now()
        if isinstance(value, pd.DataFrame):
            envelope = {
                "type": "dataframe",
                "dtypes": {column: str(dtype) for column, dtype in value.dtypes.items()},
                "data": value.to_json(orient="split", date_format="iso"),
            }
        else:
            envelope = {"type": "json", "data": value}
        try:
            payload = json.dumps(envelope, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            logger.debug("缓存值不可序列化（忽略）：%s", exc)
            return
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO cache (key, payload, updated_at) "
                    "VALUES (?, ?, ?)",
                    (key, payload, now.isoformat()),
                )
        except sqlite3.Error as exc:
            logger.debug("缓存写入失败（忽略）：%s", exc)
