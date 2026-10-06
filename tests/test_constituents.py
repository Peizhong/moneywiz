"""src.constituents 红利指数成分股定期刷新的单元测试（全部 mock，不联网）。"""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
import yaml

from src import constituents, data

TODAY = date(2026, 10, 6)
TTL = 14


def _write_index_file(tmp_path, *, updated_at, entries):
    payload = {
        "index_code": "000015",
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "constituents": entries,
    }
    path = tmp_path / "dividend_index.yaml"
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def _frame(entries):
    return pd.DataFrame(
        {"code": [c for c, _ in entries], "name": [n for _, n in entries]}
    )


def _patch_fetch(monkeypatch, frame):
    calls = []

    def fake(index_code):
        calls.append(index_code)
        return frame

    monkeypatch.setattr(data, "get_index_constituents", fake)
    return calls


def test_fresh_file_skips_fetch_and_returns_entries(monkeypatch, tmp_path):
    path = _write_index_file(
        tmp_path,
        updated_at=datetime.now() - timedelta(days=3),
        entries=[
            {"code": "600015", "name": "华夏银行"},
            {"code": "601088", "name": "中国神华", "added": "2026-09-01"},
        ],
    )
    calls = _patch_fetch(monkeypatch, _frame([("600015", "华夏银行")]))

    result = constituents.refresh_constituents(path, "000015", TTL, today=TODAY)

    assert result["refreshed"] is False
    assert calls == []  # 未过期不请求上游
    assert [c["code"] for c in result["constituents"]] == ["600015", "601088"]
    assert result["constituents"][1]["added"] == "2026-09-01"


def test_stale_file_refreshes_marks_new_and_drops_removed(monkeypatch, tmp_path):
    path = _write_index_file(
        tmp_path,
        updated_at=datetime.now() - timedelta(days=20),
        entries=[
            {"code": "600015", "name": "华夏银行"},
            {"code": "601088", "name": "中国神华", "added": "2026-09-01"},
        ],
    )
    calls = _patch_fetch(
        monkeypatch,
        _frame([("600015", "华夏银行"), ("600028", "中国石化"), ("600900", "长江电力")]),
    )

    result = constituents.refresh_constituents(path, "000015", TTL, today=TODAY)

    assert result["refreshed"] is True
    assert calls == ["000015"]
    assert result["added"] == ["600028", "600900"]
    assert result["removed"] == ["601088"]

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(payload["updated_at"]).date() == datetime.now().date()
    by_code = {entry["code"]: entry for entry in payload["constituents"]}
    assert set(by_code) == {"600015", "600028", "600900"}
    assert by_code["600028"]["added"] == TODAY.isoformat()
    assert by_code["600900"]["added"] == TODAY.isoformat()
    assert "added" not in by_code["600015"]


def test_refresh_failure_keeps_file_untouched(monkeypatch, tmp_path):
    path = _write_index_file(
        tmp_path,
        updated_at=datetime.now() - timedelta(days=20),
        entries=[{"code": "600015", "name": "华夏银行"}],
    )
    before = path.read_text(encoding="utf-8")
    calls = _patch_fetch(monkeypatch, None)

    result = constituents.refresh_constituents(path, "000015", TTL, today=TODAY)

    assert result["refreshed"] is False
    assert result["error"]
    assert calls == ["000015"]
    assert path.read_text(encoding="utf-8") == before  # 沿用旧名单
    assert [c["code"] for c in result["constituents"]] == ["600015"]


def test_kept_entries_preserve_their_added_dates(monkeypatch, tmp_path):
    path = _write_index_file(
        tmp_path,
        updated_at=datetime.now() - timedelta(days=20),
        entries=[{"code": "601088", "name": "中国神华", "added": "2026-09-01"}],
    )
    _patch_fetch(monkeypatch, _frame([("601088", "中国神华"), ("600015", "华夏银行")]))

    result = constituents.refresh_constituents(path, "000015", TTL, today=TODAY)

    assert result["added"] == ["600015"]  # 只有真正新增的
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    by_code = {entry["code"]: entry for entry in payload["constituents"]}
    assert by_code["601088"]["added"] == "2026-09-01"  # 原有 added 保留
