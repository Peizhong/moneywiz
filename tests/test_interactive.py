"""src.interactive 报表后交互式详情会话的单元测试。

questionary 全部打桩（不碰终端、不等待输入），渲染仍走真实的 reporter。
"""

import sys

import pytest

from src import interactive

OUTPUT_CFG = {"buy_top_n": 5, "avoid_bottom_n": 5}
RULES = {
    "indicators": {
        "dividend_yield": {"weight": 100, "thresholds": {"high": 4.0, "mid": 2.0}}
    }
}


def _stock(code, name, total, value, score):
    """自洽的 result dict：value 经 {high: 4.0, mid: 2.0} 档位应得 score，
    总分 = score × 100（详情会按 values 重算档位，故 fixture 不能自相矛盾）。"""
    return {
        "code": code,
        "name": name,
        "total": total,
        "values": {"dividend_yield": value},
        "scores": {"dividend_yield": score},
        "missing": [],
        "tech": None,
    }


STOCKS = [
    _stock("600036", "招商银行", 100.0, 5.0, 1.0),  # 5.0% ≥ 4.0 → 满分档
    _stock("000001", "平安银行", 0.0, 1.0, 0.0),  # 1.0% < 2.0 → 零分档
]


class _StubQuestion:
    """questionary.select 的替身：ask() 依次吐出脚本里的答案（异常则抛出）。"""

    def __init__(self, answer):
        self._answer = answer

    def ask(self):
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer

    def unsafe_ask(self):
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer


def _patch_select(monkeypatch, answers):
    """替换交互式选择；返回每次提问时展示的选项标题列表（用于断言列表内容）。"""
    calls = []

    def fake_select(message, choices=None, **kwargs):
        calls.append([choice.title for choice in choices])
        return _StubQuestion(answers.pop(0))

    monkeypatch.setattr(interactive.questionary, "select", fake_select)
    return calls


def _patch_pause(monkeypatch, answer=None):
    """替换「按任意键返回」的暂停（默认：按了一下键）。"""
    def fake_pause(message=None, **kwargs):
        return _StubQuestion(answer)

    monkeypatch.setattr(
        interactive.questionary, "press_any_key_to_continue", fake_pause
    )


def test_enabled_follows_tty_and_flag(monkeypatch):
    class _Tty:
        def isatty(self):
            return True

    class _Pipe:
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdout", _Tty())
    assert interactive.enabled() is True
    assert interactive.enabled(no_interactive=True) is False

    monkeypatch.setattr(sys, "stdout", _Pipe())
    assert interactive.enabled() is False  # 管道/重定向/Docker：绝不阻塞
    assert interactive.enabled(no_interactive=True) is False


def test_session_lists_ranked_stocks_as_choices(monkeypatch):
    calls = _patch_select(monkeypatch, [None])

    interactive.session(STOCKS, RULES, OUTPUT_CFG)

    assert calls == [["1  600036 招商银行  100.0  买入", "2  000001 平安银行  0.0  买入"]]


def test_session_prints_detail_of_chosen_stock_and_loops(monkeypatch, capsys):
    from src.reporter import ranked_rows

    rows = ranked_rows(STOCKS, OUTPUT_CFG)
    calls = _patch_select(monkeypatch, [rows[1], None])
    _patch_pause(monkeypatch)

    interactive.session(STOCKS, RULES, OUTPUT_CFG)

    out = capsys.readouterr().out
    assert "000001 平安银行" in out
    assert "股息率" in out  # 指标明细行
    assert "加权小计 0.0 → 总分 0.0" in out  # 各贡献之和与总分自洽
    assert "600036 招商银行" not in out  # 只打印被选中的那一只
    assert len(calls) == 2  # 看完详情回到列表继续选


def test_session_exits_cleanly_on_cancel(monkeypatch, capsys):
    """Ctrl-C / Ctrl-Q 由 questionary 转成 None；异常路径也要干净退出。"""
    _patch_select(monkeypatch, [None, KeyboardInterrupt()])

    interactive.session(STOCKS, RULES, OUTPUT_CFG)  # 不抛异常

    assert "股息率" not in capsys.readouterr().out  # 没有打印任何详情


def test_session_without_stocks_returns_immediately(monkeypatch):
    calls = _patch_select(monkeypatch, [])

    interactive.session([], RULES, OUTPUT_CFG)

    assert calls == []  # 没有可选项就不提问


@pytest.mark.parametrize("answer", [None, KeyboardInterrupt()])
def test_session_stops_when_first_answer_quits(monkeypatch, capsys, answer):
    _patch_select(monkeypatch, [answer])

    interactive.session(STOCKS, RULES, OUTPUT_CFG)

    assert capsys.readouterr().out == ""


def test_enabled_is_false_without_questionary(monkeypatch, caplog):
    """老环境没装 questionary 时降级为「不进入交互」，绝不让主流程崩。"""
    import logging

    class _Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdout", _Tty())
    monkeypatch.setattr(interactive, "questionary", None)

    with caplog.at_level(logging.WARNING):
        assert interactive.enabled() is False

    assert "questionary" in caplog.text and "pip install" in caplog.text


def test_session_pauses_after_detail_until_keypress(monkeypatch):
    """详情打印后必须等一次按键再回列表——否则列表立刻重绘，详情被顶出屏幕。"""
    from src.reporter import ranked_rows

    rows = ranked_rows(STOCKS, OUTPUT_CFG)
    events = []

    def fake_select(message, choices=None, **kwargs):
        events.append("列表")
        return _StubQuestion(rows[0] if events.count("列表") == 1 else None)

    def fake_pause(message=None, **kwargs):
        events.append("暂停")
        return _StubQuestion(None)

    monkeypatch.setattr(interactive.questionary, "select", fake_select)
    monkeypatch.setattr(interactive.questionary, "press_any_key_to_continue", fake_pause)

    interactive.session(STOCKS, RULES, OUTPUT_CFG)

    assert events == ["列表", "暂停", "列表"]


def test_session_exits_when_cancelled_at_pause(monkeypatch):
    """在详情暂停处 Ctrl-C：结束会话，不再弹回列表。"""
    from src.reporter import ranked_rows

    rows = ranked_rows(STOCKS, OUTPUT_CFG)
    calls = _patch_select(monkeypatch, [rows[0]])  # 只准备了一次列表答案
    monkeypatch.setattr(
        interactive.questionary,
        "press_any_key_to_continue",
        lambda *a, **k: _StubQuestion(KeyboardInterrupt()),
    )

    interactive.session(STOCKS, RULES, OUTPUT_CFG)

    assert len(calls) == 1  # 没有回到列表（否则答案用尽会抛 IndexError）


def test_session_ends_cleanly_when_pause_hits_eof(monkeypatch, capsys):
    """暂停处拿到 EOF（非终端/输入关闭）→ 会话干净结束，不把异常抛给调用方。"""
    from src.reporter import ranked_rows

    rows = ranked_rows(STOCKS, OUTPUT_CFG)
    _patch_select(monkeypatch, [rows[0]])
    _patch_pause(monkeypatch, EOFError())

    interactive.session(STOCKS, RULES, OUTPUT_CFG)  # 不抛异常

    assert "股息率" in capsys.readouterr().out  # 详情已经打印出来了
