"""bili-songbot 包：按职责拆分的 B 站直播点歌机器人。

模块地图：
    config   —— 配置加载、日志系统、配置常量
    protocol —— B站 HTTP 协议层（限流/WBI/buvid3/Cookie 热重载）
    bridge   —— lx 音源 Node.js 桥接器（单例注入）
    models   —— 数据模型（SongInfo/QueueItem/PlayQueue）
    search   —— 音源搜索与评分择优
    player   —— 播放与下载
    vote     —— 切歌投票
    danmaku  —— 弹幕监听与发送
    core     —— BiliMusicBot 主控编排
"""

from .config import CFG, CONFIG_PATH, ROOM_ID, log, load_config
from .protocol import CookieManager, get_real_room_id
from .bridge import SourceBridge, get_bridge, set_bridge
from .models import PlayQueue, QueueItem, SongInfo
from .search import get_music_url, search_song
from .player import PlaybackManager
from .vote import VoteManager
from .danmaku import BiliSender
from .danmaku_biliapi import BiliDanmaku
from .core import BiliMusicBot

__all__ = [
    "CFG", "CONFIG_PATH", "ROOM_ID", "log", "load_config",
    "CookieManager", "get_real_room_id",
    "SourceBridge", "get_bridge", "set_bridge",
    "PlayQueue", "QueueItem", "SongInfo",
    "get_music_url", "search_song",
    "PlaybackManager", "VoteManager",
    "BiliDanmaku", "BiliSender", "BiliMusicBot",
]
