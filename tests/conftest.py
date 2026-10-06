"""共享 fixture：测试前后重置数据层运行时状态（东财熔断标记、取数缓存），
并默认禁网——任何未被 monkeypatch 的 requests 调用都会失败而不是真实联网。"""

import pytest

from src import data


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    data.reset_quote_source_state()
    data.configure_cache(None)
    yield
    data.reset_quote_source_state()
    data.configure_cache(None)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """默认禁止数据层真实联网；需要 HTTP 的测试自行 monkeypatch requests.get 覆盖。"""

    def blocked(*args, **kwargs):
        raise ConnectionError("测试禁止真实网络：请在用例中打桩 requests.get / _http_text")

    monkeypatch.setattr("src.data.requests.get", blocked)
