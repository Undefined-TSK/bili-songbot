"""点歌统计：记录点播次数（json 持久化），/top 查看点歌排行（人性化互动层）。

零依赖、可插拔：core 在点歌成功入队时调用 record()，/top 命令调用 top()；
文件读写失败静默降级（统计不影响点歌主链路）。
"""
import json
import os
import threading
import time
from typing import List, Optional, Tuple


class SongStats:
    """按 (歌名 - 歌手) 维度统计点播次数，json 持久化、跨重启保留。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = {}  # key -> {"count": int, "last": ts}
        if path:
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict):
                        self._data = raw
            except Exception:
                self._data = {}  # 损坏文件静默重置

    @staticmethod
    def _key(name: str, singer: str) -> str:
        return f"{name} - {singer}" if singer else name

    def record(self, name: str, singer: str, requester: str = ""):
        """点歌成功入队时 +1 并落盘；任何异常不影响点歌主链路。"""
        try:
            with self._lock:
                k = self._key(name, singer)
                entry = self._data.get(k) or {"count": 0}
                entry["count"] = int(entry.get("count", 0)) + 1
                entry["last"] = time.time()
                if requester:
                    entry["last_by"] = requester
                self._data[k] = entry
                if self.path:
                    tmp = self.path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self._data, f, ensure_ascii=False)
                    os.replace(tmp, self.path)  # 原子替换，防写坏
        except Exception:
            pass

    def top(self, n: int = 5) -> List[Tuple[str, int]]:
        """点播次数前 n 的 (歌名 - 歌手, 次数) 列表，按次数降序。"""
        with self._lock:
            items = [(k, int(v.get("count", 0))) for k, v in self._data.items()]
        items.sort(key=lambda x: (-x[1], x[0]))
        return items[:n]

    def total(self) -> int:
        """历史点播总次数（跨重启累计）。"""
        with self._lock:
            return sum(int(v.get("count", 0)) for v in self._data.values())

    def today(self) -> int:
        """今日有过点播记录的歌曲数（按最后一次点播时间判断，用于概览 KPI）。"""
        import datetime
        start = datetime.datetime.combine(
            datetime.date.today(), datetime.time.min).timestamp()
        with self._lock:
            return sum(1 for v in self._data.values()
                       if float(v.get("last", 0) or 0) >= start)
