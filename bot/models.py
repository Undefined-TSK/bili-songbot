"""数据模型：歌曲信息、点歌请求项、播放队列。"""
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


# ======================== 歌曲信息 ========================
@dataclass
class SongInfo:
    """搜索结果中的一首歌曲。"""
    name: str = ""
    singer: str = ""
    album: str = ""
    song_id: str = ""
    platform: str = ""  # wy, tx, kw, kg, mg
    interval: str = ""
    alternates: list = field(default_factory=list)  # 同歌其他平台候选（跨源回退用）
    score: float = 0  # 搜索匹配评分（批量验证/调试用，搜索层负责填写）

    @property
    def search_keyword(self) -> str:
        """用于关键词搜索的字符串。"""
        if self.singer:
            return f"{self.name} {self.singer}"
        return self.name


# ======================== 播放队列 ========================
@dataclass
class QueueItem:
    song: SongInfo
    url: str = ""  # 可选：为空时在播放时按音质降级获取
    requester: str = ""
    requester_uid: int = 0  # 点歌人 uid（公共台边界判定用：重名用户也能正确识别归属）


class PlayQueue:
    """FIFO 播放队列。"""
    def __init__(self):
        self._items: deque = deque()

    def add(self, item: QueueItem):
        self._items.append(item)
        return len(self._items)

    def next(self) -> Optional[QueueItem]:
        if self._items:
            return self._items.popleft()
        return None

    def peek(self) -> Optional[QueueItem]:
        if self._items:
            return self._items[0]
        return None

    def remove_current(self):
        """移除当前播放的歌曲（队首）。"""
        if self._items:
            self._items.popleft()

    def remove_at(self, index: int) -> Optional[QueueItem]:
        """按位置（0 起）移除队列中的项并返回；越界返回 None（/remove 撤回用）。"""
        if 0 <= index < len(self._items):
            item = self._items[index]
            del self._items[index]
            return item
        return None

    def snapshot(self) -> list:
        """当前队列快照（列表，位置与展示一致，/queue 查看队列用）。"""
        return list(self._items)

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        return len(self._items) == 0
