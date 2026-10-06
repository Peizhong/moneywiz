"""src.constituents 多指数成分股刷新的单元测试（全部 mock，不联网）。"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
import yaml

from src import constituents, data
from src.config import ConstituentCfg, IndexCfg

TODAY = date(2026, 10, 6)
TTL = 14


def _index_cfg(code="000015", name="上证红利", age_days=0, entries=None):
    return IndexCfg(
        index_code=code,
        index_name=name,
        updated_at=datetime.now() - timedelta(days=age_days),
        constituents=[
            ConstituentCfg(**entry)
            for entry in (entries or [{"code": "600015", "name": "华夏银行"}])
        ],
    )


def _frame(entries):
    return pd.DataFrame(
        {"code": [c for c, _ in entries], "name": [n for _, n in entries]}
    )


def _patch_fetch(monkeypatch, frames):
    """按指数代码返回成分帧（None 表示获取失败）；记录调用。"""
    calls = []

    def fake(index_code):
        calls.append(index_code)
        return frames.get(index_code)

    monkeypatch.setattr(data, "get_index_constituents", fake)
    return calls


def _read_file(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_fresh_indices_skip_fetch_and_merge_entries(monkeypatch, tmp_path):
    path = tmp_path / "dividend_index.yaml"
    indices = [
        _index_cfg(code="000015", age_days=3),
        _index_cfg(
            code="931468",
            name="红利质量",
            age_days=5,
            entries=[{"code": "600519", "name": "贵州茅台", "added": "2026-10-01"}],
        ),
    ]
    calls = _patch_fetch(monkeypatch, {})

    result = constituents.refresh_indices(path, indices, TTL, today=TODAY)

    assert result["refreshed"] is False
    assert calls == []  # 都未过期，不请求上游
    assert [c["code"] for c in result["constituents"]] == ["600015", "600519"]
    payload = _read_file(path)
    assert [item["index_code"] for item in payload["indices"]] == ["000015", "931468"]
    assert payload["indices"][0]["updated_at"] == indices[0].updated_at.isoformat(
        timespec="seconds"
    )


def test_stale_index_refreshes_while_other_stays_fresh(monkeypatch, tmp_path):
    path = tmp_path / "dividend_index.yaml"
    stale = _index_cfg(
        code="000015",
        age_days=20,
        entries=[
            {"code": "600015", "name": "华夏银行"},
            {"code": "601088", "name": "中国神华", "added": "2026-09-01"},
        ],
    )
    fresh = _index_cfg(
        code="931468", name="红利质量", age_days=3, entries=[{"code": "600519", "name": "贵州茅台"}]
    )
    calls = _patch_fetch(
        monkeypatch,
        {"000015": _frame([("600015", "华夏银行"), ("600028", "中国石化"), ("600900", "长江电力")])},
    )

    result = constituents.refresh_indices(path, [stale, fresh], TTL, today=TODAY)

    assert result["refreshed"] is True
    assert calls == ["000015"]  # 只有过期的指数被刷新
    assert result["added"] == ["600028", "600900"]
    assert result["removed"] == ["601088"]

    payload = _read_file(path)
    first, second = payload["indices"]
    by_code = {entry["code"]: entry for entry in first["constituents"]}
    assert set(by_code) == {"600015", "600028", "600900"}
    assert by_code["600028"]["added"] == TODAY.isoformat()
    assert datetime.fromisoformat(first["updated_at"]).date() == datetime.now().date()
    assert second["updated_at"] == fresh.updated_at.isoformat(timespec="seconds")
    # 两个指数的条目都会被合并进扫描清单
    assert [c["code"] for c in result["constituents"]] == [
        "600015",
        "600028",
        "600900",
        "600519",
    ]


def test_refresh_failure_keeps_entries_and_updated_at(monkeypatch, tmp_path):
    path = tmp_path / "dividend_index.yaml"
    stale = _index_cfg(code="000015", age_days=20)
    calls = _patch_fetch(monkeypatch, {"000015": None})

    result = constituents.refresh_indices(path, [stale], TTL, today=TODAY)

    assert result["refreshed"] is False
    assert result["error"] and "000015" in result["error"]
    assert calls == ["000015"]
    assert [c["code"] for c in result["constituents"]] == ["600015"]
    payload = _read_file(path)
    assert payload["indices"][0]["updated_at"] == stale.updated_at.isoformat(
        timespec="seconds"
    )  # 失败不改时间戳，下轮仍会重试


def test_kept_entries_preserve_their_added_dates(monkeypatch, tmp_path):
    path = tmp_path / "dividend_index.yaml"
    stale = _index_cfg(
        code="000015",
        age_days=20,
        entries=[{"code": "601088", "name": "中国神华", "added": "2026-09-01"}],
    )
    _patch_fetch(
        monkeypatch,
        {"000015": _frame([("601088", "中国神华"), ("600015", "华夏银行")])},
    )

    result = constituents.refresh_indices(path, [stale], TTL, today=TODAY)

    assert result["added"] == ["600015"]
    payload = _read_file(path)
    by_code = {entry["code"]: entry for entry in payload["indices"][0]["constituents"]}
    assert by_code["601088"]["added"] == "2026-09-01"
