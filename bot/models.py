"""数据模型：歌曲信息、点歌请求项、播放队列（可选 json 持久化，重启恢复队列）。"""
import json
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


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


def _song_to_dict(song: SongInfo) -> dict:
    return {
        "name": song.name, "singer": song.singer, "album": song.album,
        "song_id": song.song_id, "platform": song.platform, "interval": song.interval,
        "alternates": [_song_to_dict(a) for a in (song.alternates or [])],
        "score": song.score,
    }


def _song_from_dict(raw: dict) -> SongInfo:
    return SongInfo(
        name=str(raw.get("name", "")),
        singer=str(raw.get("singer", "")),
        album=str(raw.get("album", "")),
        song_id=str(raw.get("song_id", "")),
        platform=str(raw.get("platform", "")),
        interval=str(raw.get("interval", "")),
        alternates=[_song_from_dict(a) for a in raw.get("alternates") or [] if isinstance(a, dict)],
        score=raw.get("score", 0) or 0,
    )


def _item_to_dict(item: QueueItem) -> dict:
    # 不持久化 url：CDN 链接有效期极短，重启后必然失效，恢复时一律重新取链
    return {
        "song": _song_to_dict(item.song),
        "requester": item.requester,
        "requester_uid": item.requester_uid,
    }


def _item_from_dict(raw: dict) -> Optional[QueueItem]:
    song_raw = raw.get("song")
    if not isinstance(song_raw, dict) or not song_raw.get("name"):
        return None
    return QueueItem(
        song=_song_from_dict(song_raw),
        requester=str(raw.get("requester", "")),
        requester_uid=int(raw.get("requester_uid", 0) or 0),
    )


class PlayQueue:
    """FIFO 播放队列（可选 json 持久化：进程重启后恢复未播完的队列）。

    persist_path 为 None 时为纯内存队列（行为与旧版完全一致）；
    持久化失败一律静默降级，绝不影响点歌主链路。
    """
    def __init__(self, persist_path: Optional[str] = None):
        self._items: deque = deque()
        self.persist_path = persist_path
        if persist_path:
            self._load()

    def _load(self):
        try:
            if not os.path.exists(self.persist_path):
                return
            with open(self.persist_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                return
            for entry in raw:
                if isinstance(entry, dict):
                    item = _item_from_dict(entry)
                    if item:
                        self._items.append(item)
        except Exception:
            self._items = deque()  # 损坏文件静默重置，不让启动失败

    def _save(self):
        if not self.persist_path:
            return
        try:
            data = [_item_to_dict(it) for it in self._items]
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.persist_path)  # 原子替换，防写坏
        except Exception:
            pass

    def add(self, item: QueueItem):
        self._items.append(item)
        self._save()
        return len(self._items)

    def next(self) -> Optional[QueueItem]:
        if self._items:
            item = self._items.popleft()
            self._save()
            return item
        return None

    def peek(self) -> Optional[QueueItem]:
        if self._items:
            return self._items[0]
        return None

    def remove_current(self):
        """移除当前播放的歌曲（队首）。"""
        if self._items:
            self._items.popleft()
            self._save()

    def remove_at(self, index: int) -> Optional[QueueItem]:
        """按位置（0 起）移除队列中的项并返回；越界返回 None（/remove 撤回用）。"""
        if 0 <= index < len(self._items):
            item = self._items[index]
            del self._items[index]
            self._save()
            return item
        return None

    def move_to(self, index: int, target: int) -> Optional[QueueItem]:
        """把位置 index（0 起）的项移动到 target 位置并返回；越界返回 None。"""
        if not (0 <= index < len(self._items)) or not (0 <= target < len(self._items)):
            return None
        if index == target:
            return self._items[index]
        item = self._items[index]
        del self._items[index]
        self._items.insert(target, item)
        self._save()
        return item

    def clear(self, keep_first: bool = False):
        """清空队列；keep_first=True 时保留队首（正在播放的当前项）。"""
        kept = [self._items[0]] if keep_first and self._items else []
        self._items.clear()
        for item in kept:
            self._items.append(item)
        self._save()

    def snapshot(self) -> list:
        """当前队列快照（列表，位置与展示一致，/queue 查看队列用）。"""
        return list(self._items)

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        return len(self._items) == 0
