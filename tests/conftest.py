"""共享 fixture：测试前后重置数据层运行时状态（东财行情熔断标记）。"""

import pytest

from src import data


@pytest.fixture(autouse=True)
def _reset_quote_source_state():
    data.reset_quote_source_state()
    yield
    data.reset_quote_source_state()
