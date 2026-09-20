from __future__ import annotations

import pytest
from acps_sdk.aip.aip_rpc_server import TaskManager


@pytest.fixture(autouse=True)
def clear_official_sdk_memory_store():
    # Official TaskManager is a process-global example store. Keep tests isolated.
    TaskManager._tasks.clear()
    yield
    TaskManager._tasks.clear()
