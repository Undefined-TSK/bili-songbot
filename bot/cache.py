"""进程内 TTL 缓存：搜索结果与播放 URL 复用，减少重复打源（可配置开关/时长）。"""
import time
from collections import OrderedDict
from typing import Any, Callable, Optional


class TTLCache:
    """带过期时间与容量上限的字典缓存（LRU 淘汰）。

    ttl <= 0 视为关闭：get 恒未命中、set 不写入（调用方无需额外分支）。
    clock 可注入（测试用假时钟），默认 time.monotonic（不受系统时间跳变影响）。
    """

    def __init__(self, ttl: float, maxsize: int = 256, clock: Optional[Callable[[], float]] = None):
        self._ttl = ttl
        self._maxsize = max(1, int(maxsize))
        self._clock = clock or time.monotonic
        self._data: "OrderedDict[Any, tuple]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def get(self, key: Any) -> Any:
        item = self._data.get(key)
        if item is None:
            self.misses += 1
            return None
        ts, value = item
        if self._clock() - ts >= self._ttl:
            del self._data[key]
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: Any, value: Any):
        if not self.enabled:
            return
        self._data[key] = (self._clock(), value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def invalidate(self, key: Any):
        self._data.pop(key, None)

    def clear(self):
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)
