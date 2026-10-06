"""共享 fixture：测试前后重置数据层运行时状态（东财熔断标记、取数缓存）。"""

import pytest

from src import data


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    data.reset_quote_source_state()
    data.configure_cache(None)
    yield
    data.reset_quote_source_state()
    data.configure_cache(None)
