#!/usr/bin/env python3
"""歌词点歌单元测试：片段→候选(name,singer)→内核 search_song 链路（mock provider，不触网）。

验证：
- 提供方返回候选后，resolve_from_lyric 把候选逐個交给内核 search_song 解析（复用现有内核，不自行搜源）；
- 提供方异常/空返回 → 优雅返回 None（绝不炸主流程）；
- looks_like_lyric 启发式只对"像歌词"的输入返回 True，避免对正常点歌误触发。
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.lyrics import looks_like_lyric, resolve_from_lyric
from bot.models import SongInfo


class FakeProvider:
    """可配置候选与异常的 mock 歌词提供方。"""
    def __init__(self, candidates=None, raise_exc=False):
        self.candidates = candidates or []
        self.raise_exc = raise_exc
        self.calls = []

    async def search(self, session, fragment):
        self.calls.append(fragment)
        if self.raise_exc:
            raise RuntimeError("network down")
        return self.candidates


def _kernel(name, singer):
    """mock 内核 search_song：返回带评分的 SongInfo；命中即视为解析成功。
    注意：歌词解析只用歌名（歌手留空），故 sg 不参与匹配。"""
    async def _search(session, kw, sg=""):
        if kw == name:
            return SongInfo(name=name, singer=singer, platform="wy", song_id="1", score=150)
        return None
    return _search


class TestLooksLikeLyric(unittest.TestCase):
    """启发式：仅"像歌词"的输入（较长 / 多词）才触发，正常短歌名不触发。"""

    def test_long_cjk_fragment(self):
        self.assertTrue(looks_like_lyric('天青色等烟雨'))           # 6 字中文串
        self.assertTrue(looks_like_lyric('窗外的麻雀在电线杆上多嘴'))  # 较长中文

    def test_multiword_english_fragment(self):
        self.assertTrue(looks_like_lyric('are we out of the woods'))      # 多词英文

    def test_short_song_name_not_lyric(self):
        self.assertFalse(looks_like_lyric('七里香'))                 # 短歌名
        self.assertFalse(looks_like_lyric('晴天'))

    def test_delimiter_song_singer_not_lyric(self):
        # 含显式分隔符的"歌名 歌手"不算歌词（走既有逻辑）
        self.assertFalse(looks_like_lyric('告白气球 - 周杰伦'))


class TestResolveFromLyric(unittest.TestCase):
    """片段→内核 链路：候选经内核解析；无候选/异常优雅降级。"""

    def test_fragment_resolves_via_kernel(self):
        # 提供方给候选 (青花瓷, 周杰伦) → 内核解析成功返回 SongInfo（歌手由内核共识定）
        provider = FakeProvider(candidates=[("青花瓷", "周杰伦"), ("无关歌", "某人")])
        kernel = _kernel("青花瓷", "周杰伦")
        song = asyncio.run(resolve_from_lyric(None, "天青色等烟雨", provider=provider, search_song=kernel))
        self.assertIsNotNone(song)
        self.assertEqual(song.name, "青花瓷")

    def test_kernel_called_with_candidate(self):
        # 内核应以"提供方候选的歌名"为参数被调用（歌手留空，由内核共识选规范原唱）
        provider = FakeProvider(candidates=[("青花瓷", "周杰伦")])
        seen = {}
        async def kernel(session, kw, sg=""):
            seen["kw"], seen["sg"] = kw, sg
            return SongInfo(name=kw, singer=sg, platform="wy", song_id="1", score=150)
        asyncio.run(resolve_from_lyric(None, "天青色等烟雨", provider=provider, search_song=kernel))
        self.assertEqual(seen["kw"], "青花瓷")
        self.assertEqual(seen["sg"], "")  # 歌手留空，交内核选规范原唱

    def test_empty_candidates_returns_none(self):
        provider = FakeProvider(candidates=[])
        song = asyncio.run(resolve_from_lyric(None, "天青色等烟雨", provider=provider, search_song=_kernel("青花瓷", "周杰伦")))
        self.assertIsNone(song)

    def test_provider_exception_returns_none(self):
        # 提供方抛异常（网络故障）→ 优雅降级为 None，不向外抛
        provider = FakeProvider(raise_exc=True)
        song = asyncio.run(resolve_from_lyric(None, "天青色等烟雨", provider=provider, search_song=_kernel("青花瓷", "周杰伦")))
        self.assertIsNone(song)

    def test_skip_title_collision_candidate(self):
        # 提供方把"歌名=歌词"的巧合候选（天青色等烟雨（粤语版））排在榜首时，
        # resolve 应跳过它，优先取真正含该歌词的歌（青花瓷），而非返回同名巧合歌
        provider = FakeProvider(candidates=[
            ("天青色等烟雨（粤语版）", "某人"),   # 歌名=歌词的巧合候选
            ("青花瓷", "周杰伦"),               # 真正含该歌词的歌
        ])
        seen = {}
        async def kernel(session, kw, sg=""):
            seen.setdefault("tried", []).append((kw, sg))
            if kw == "青花瓷":
                return SongInfo(name=kw, singer="周杰伦", platform="wy", song_id="1", score=150)
            return None
        song = asyncio.run(resolve_from_lyric(None, "天青色等烟雨", provider=provider, search_song=kernel))
        self.assertIsNotNone(song)
        self.assertEqual(song.name, "青花瓷")
        # 榜首巧合候选应被跳过（不应以它去内核解析）
        self.assertNotIn(("天青色等烟雨（粤语版）", "某人"), seen["tried"])


if __name__ == '__main__':
    unittest.main(verbosity=2)
