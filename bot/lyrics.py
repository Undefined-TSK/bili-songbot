"""歌词点歌（片段→歌曲）：把"天青色等烟雨"这类歌词片段解析成具体歌曲。

设计（与既有架构正交，复用内核）：
- 本模块**只做"片段→候选(name,singer)"的建议**，绝不自行搜源 / 取链 / 播放；
- 真正的解析、原版优先、跨平台共识、置信度门禁、跨源回退、取链播放**全部交给内核
  search_song**（由 core 注入），保持"用现有内核"的铁律；
- 提供方默认用网易云片段搜索（无鉴权、仅取歌名/歌手建议），超时与解析失败均优雅降级，
  绝不抛异常炸主流程；
- 仅在 ENABLE_LYRICS_SEARCH 且输入"像歌词"（较长、多词）时由 core._resolve_single 触发，
  普通点歌与单曲逻辑完全不受影响。

这是把"怎么输都能搜到"从"歌名/歌手容错"推进到"凭一句歌词也能点歌"的终极大招，
也是竞品普遍缺失、本项目可脱颖而出的差异化能力。
"""
import asyncio
import re
from typing import List, Optional, Tuple

import aiohttp

from .config import LYRICS_TIMEOUT, log
from .fuzzy import PINYIN_AVAILABLE, pinyin_key
from .models import SongInfo


# 网易云片段搜索（无鉴权，返回按综合相关度排序的歌曲，含歌词相关度，仅需歌名/歌手建议）
_LYRICS_CANDIDATE_URL = "https://music.163.com/api/search/get"


def looks_like_lyric(query: str) -> bool:
    """启发式：判断输入是否"像歌词片段"而非普通歌名/歌手，避免对正常点歌误触发。

    仅在正常搜索落空后的兜底阶段调用，因此即使误判也只会多花一次片段搜索，
    不会污染正常点歌路径。
    """
    q = (query or "").strip()
    if len(q) < 6:
        return False  # 太短多为歌名
    # 含显式分隔符或版本词 → 按既有"歌名 歌手 / 版本"逻辑走，不算歌词
    if any(d in q for d in (" - ", "—", "/", "|", "by", "，", "、")):
        return False
    tokens = q.split()
    if len(tokens) >= 2:
        return True  # 多词英文/混合片段
    cjk = re.sub(r"[\s\W]", "", q)
    return len(cjk) >= 6  # 较长中文串（无空格）


class LyricsProvider:
    """片段→候选(name,singer) 提供方接口。"""

    async def search(self, session: aiohttp.ClientSession, fragment: str) -> List[Tuple[str, str]]:
        raise NotImplementedError


class NeteaseFragmentProvider(LyricsProvider):
    """网易云片段搜索：无鉴权，取 top 候选的 (歌名, 歌手)。超时/解析失败优雅降级返回 []。"""

    async def search(self, session: aiohttp.ClientSession, fragment: str) -> List[Tuple[str, str]]:
        try:
            headers = {"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"}
            params = {"s": fragment, "type": 1, "limit": 6}
            async with session.get(
                _LYRICS_CANDIDATE_URL, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=LYRICS_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
            songs = (data.get("result") or {}).get("songs") or []
            out: List[Tuple[str, str]] = []
            for s in songs:
                name = (s.get("name") or "").strip()
                artists = s.get("artists") or []
                singer = ((artists[0] or {}).get("name") or "").strip() if artists else ""
                if name:
                    out.append((name, singer))
            return out
        except Exception as e:  # 网络/解析异常：优雅降级，交回 normal "未找到"
            log.debug(f"歌词片段搜索异常: {e}")
            return []


_DEFAULT_PROVIDER = NeteaseFragmentProvider()


def _fragment_in_name(fragment: str, name: str) -> bool:
    """判断候选歌名是否"就是这句歌词本身"（标题与歌词行撞车），用于歌词搜索去巧合。

    例：用户唱"天青色等烟雨"，网易云可能返回《天青色等烟雨（粤语版）》这类
    "歌名=歌词"的巧合候选；真正的目标往往是另一首（如《青花瓷》）——其歌名与歌词不同。
    """
    f = fragment.strip().lower()
    n = name.strip().lower()
    if not f or not n:
        return False
    if f in n or n in f:
        return True
    if PINYIN_AVAILABLE:  # 繁体/谐音歌词与歌名拼音相同也算撞车
        pf, pn = pinyin_key(f), pinyin_key(n)
        if pf and pf == pn:
            return True
    return False


async def resolve_from_lyric(
    session: aiohttp.ClientSession,
    fragment: str,
    provider: Optional[LyricsProvider] = None,
    search_song=None,
) -> Optional[SongInfo]:
    """片段→候选→内核解析。返回经原版优先/共识/门禁的 SongInfo，或 None。

    策略：提供方只建议 (name,singer)；先跳过"歌名就是这句歌词"的巧合候选
    （歌词行被当歌名返回），在剩余候选里取内核评分最高者——真正含有该歌词的歌曲；
    若剩余候选都解析失败，再回退取首个能解析的巧合候选，避免空手而归。
    search_song 由调用方注入（core 的内核），真正解析/取链/播放仍走内核，严守"用现有内核"铁律。
    """
    if search_song is None:
        from .search import search_song as search_song  # 懒导入防循环
    provider = provider or _DEFAULT_PROVIDER
    try:
        candidates = await provider.search(session, fragment)
    except Exception as e:  # 提供方异常（网络/解析）：优雅降级，交回 normal "未找到"
        log.debug(f"歌词片段搜索提供方异常: {e}")
        return None
    if not candidates:
        return None
    best: Optional[SongInfo] = None
    for name, singer in candidates:
        if not name:
            continue
        if _fragment_in_name(fragment, name):
            continue  # 歌名=歌词的巧合候选，先跳过，优先取真正含该歌词的歌
        # 只用歌名交内核解析：让"原版优先/跨平台共识"自动选定规范原唱（如《青花瓷》→周杰伦），
        # 不被提供方可能给出的翻唱歌手带偏
        song = await search_song(session, name, "")
        if song and (best is None or song.score > best.score):
            best = song
    if best is None:  # 都是巧合候选：退而求其次，取首个能解析的
        for name, singer in candidates:
            if not name:
                continue
            song = await search_song(session, name, "")
            if song:
                return song
    return best
