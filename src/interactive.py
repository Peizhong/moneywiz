"""报表后的交互式详情会话（唯一 import questionary 的模块）。

报表打印完后进入选择循环：选中一只股票看指标明细（数值/档位/权重/贡献），看完
详情回到列表继续选，Ctrl-C/Ctrl-Q 退出。**非交互式终端一律不进**（``enabled()``：
stdout 不是 TTY，或显式 ``--no-interactive``）——管道、重定向、Docker、CI 下行为
与从前完全一致，绝不阻塞无人值守的运行。

终端问答只在这一层；详情文本由 ``src.reporter.render_detail``（纯函数）渲染。
"""

from __future__ import annotations

import logging
import sys

try:  # 交互式详情是可选的：环境里没有 questionary 也不该让主流程崩
    import questionary
except ImportError:  # pragma: no cover - 取决于运行环境
    questionary = None

from src.reporter import ranked_rows, render_detail, row_label

logger = logging.getLogger(__name__)

PROMPT = "选择要查看详情的股票"
INSTRUCTION = "↑↓ 选择 · 输入即筛选 · 回车查看 · Ctrl-C/Ctrl-Q 退出"


def enabled(no_interactive: bool = False) -> bool:
    """是否进入交互：显式禁用、stdout 不是终端（管道/重定向）、或没装 questionary → False。"""
    if no_interactive:
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except (AttributeError, ValueError):  # stdout 被替换或已关闭
        return False
    if questionary is None:
        logger.warning("未安装 questionary，跳过交互式详情（pip install -r requirements.txt）")
        return False
    return True


def session(stock_results: list[dict], rules_section: dict, output_cfg: dict) -> None:
    """选择循环：选中一只 → 打印其详情 → 回到列表，直到用户退出（Ctrl-C/Ctrl-Q）。"""
    rows = ranked_rows(stock_results, output_cfg)
    if not rows:
        return
    while True:
        choice = _ask(rows)
        if choice is None:
            return
        print()
        print(render_detail(choice, rules_section))
        print()


def _ask(rows: list[dict]):
    """弹一次选择列表；用户取消（Ctrl-C/Ctrl-Q/EOF）→ None。"""
    try:
        return questionary.select(
            PROMPT,
            choices=[questionary.Choice(row_label(row), value=row) for row in rows],
            use_search_filter=True,
            use_jk_keys=False,  # j/k 留给筛选输入，不当上下移动
            instruction=INSTRUCTION,
        ).ask()
    except (KeyboardInterrupt, EOFError):
        return None
