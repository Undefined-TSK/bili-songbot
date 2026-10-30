"""pytest 全局夹具：每个用例前后清空进程内缓存，避免用例间状态串扰。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture(autouse=True)
def _isolate_bot_state(tmp_path, monkeypatch):
    """清空进程内缓存，并把队列/统计的落盘路径指到临时目录，避免污染仓库根目录。"""
    from bot import core, search
    search.clear_caches()
    monkeypatch.setattr(core, "QUEUE_FILE", str(tmp_path / "queue.json"), raising=False)
    monkeypatch.setattr(core, "STATS_FILE", str(tmp_path / "song_stats.json"), raising=False)
    yield
    search.clear_caches()
