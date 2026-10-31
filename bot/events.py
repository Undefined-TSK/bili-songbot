"""运行时事件缓冲：弹幕流、日志流、弹幕速率与近段活动统计。

只做内存环形缓冲（弹幕最近 200 条、日志最近 300 条），供本地 API 的
`/api/snapshot`、`/api/logs`、`/api/danmaku` 读取；不参与点歌/播放主链路，
写入失败或溢出都静默降级。
"""
import logging
import time
from collections import deque
from typing import Dict, List, Optional

DANMAKU_CAP = 200
LOG_CAP = 300


class EventHub:
    """弹幕/日志事件缓冲与速率统计（进程内单例）。"""

    def __init__(self, danmaku_cap: int = DANMAKU_CAP, log_cap: int = LOG_CAP):
        self._danmaku: deque = deque(maxlen=danmaku_cap)
        self._logs: deque = deque(maxlen=log_cap)
        self._danmaku_times: deque = deque()
        self._danmaku_id = 0
        self._log_id = 0
        self.started_at = time.time()

    # ---------------------------------------------------------------- 弹幕

    def add_danmaku(self, user: str, text: str, kind: str = "") -> dict:
        """记录一条弹幕；返回带自增 id 的条目（供前端增量渲染）。"""
        self._danmaku_id += 1
        entry = {
            "id": self._danmaku_id,
            "ts": time.time(),
            "user": user or "匿名",
            "text": text or "",
            "kind": kind or "",
        }
        self._danmaku.append(entry)
        self._danmaku_times.append(entry["ts"])
        self.rate(60)  # 顺手清理过期时间戳
        return entry

    def danmaku_recent(self, limit: int = 30) -> List[dict]:
        if limit <= 0:
            return []
        return list(self._danmaku)[-limit:]

    def danmaku_after(self, after_id: int, limit: int = 100) -> List[dict]:
        return [d for d in self._danmaku if d["id"] > after_id][:limit]

    # ---------------------------------------------------------------- 日志

    def add_log(self, level: str, name: str, message: str) -> dict:
        self._log_id += 1
        entry = {
            "id": self._log_id,
            "ts": time.time(),
            "level": level,
            "name": name,
            "msg": message,
        }
        self._logs.append(entry)
        return entry

    def logs_after(self, after_id: int = 0, limit: int = 200) -> List[dict]:
        if after_id <= 0:
            return list(self._logs)[-limit:]
        return [r for r in self._logs if r["id"] > after_id][:limit]

    # ---------------------------------------------------------------- 统计

    def rate(self, seconds: int = 60) -> int:
        """最近 N 秒弹幕条数（用于弹幕速率 KPI）。"""
        cutoff = time.time() - seconds
        while self._danmaku_times and self._danmaku_times[0] < cutoff:
            self._danmaku_times.popleft()
        return len(self._danmaku_times)

    def activity(self, bucket_seconds: int = 10, buckets: int = 18) -> List[int]:
        """近 N 个时间桶（默认 18×10s = 3 分钟）的弹幕条数，末位为当前桶。"""
        now = time.time()
        start = now - bucket_seconds * buckets
        counts = [0] * buckets
        for ts in self._danmaku_times:
            if ts < start:
                continue
            idx = int((ts - start) // bucket_seconds)
            if 0 <= idx < buckets:
                counts[idx] += 1
        return counts

    def snapshot_counters(self) -> Dict[str, int]:
        return {
            "danmaku_rate": self.rate(60),
            "danmaku_total": self._danmaku_id,
            "log_total": self._log_id,
        }


hub = EventHub()


class EventLogHandler(logging.Handler):
    """把点歌机 logger 的记录同步到 EventHub（日志页数据源）。"""

    def __init__(self, event_hub: EventHub):
        super().__init__(level=logging.NOTSET)
        self.hub = event_hub

    def emit(self, record: logging.LogRecord):
        try:
            self.hub.add_log(record.levelname, record.name, record.getMessage())
        except Exception:
            pass


_handler: Optional[EventLogHandler] = None


def install_log_handler(target_logger: logging.Logger) -> EventLogHandler:
    """幂等挂载日志缓冲处理器（API 启动时调用一次）。"""
    global _handler
    if _handler is None:
        _handler = EventLogHandler(hub)
    if _handler not in target_logger.handlers:
        target_logger.addHandler(_handler)
    return _handler
