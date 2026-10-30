#!/usr/bin/env python3
"""搜索缓存 / 播放 URL 缓存单元测试：TTL 语义、命中复用、失效作废、容量上限。

全程无真实网络：音源桥与播放器均为假对象。
用法: python3 tests/test_cache.py
"""
import asyncio
import os
import sys
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.cache import TTLCache
from bot.models import SongInfo
from bot.search import (
    cache_music_url, cached_music_url, cache_stats, clear_caches,
    invalidate_music_url, search_song,
)


class _FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TestTTLCache(unittest.TestCase):

    def test_hit_and_expiry(self):
        clock = _FakeClock()
        cache = TTLCache(ttl=10, clock=clock)
        cache.set("k", "v")
        self.assertEqual(cache.get("k"), "v")
        clock.now += 9.9
        self.assertEqual(cache.get("k"), "v", "未到 TTL 仍有效")
        clock.now += 0.2
        self.assertIsNone(cache.get("k"), "超过 TTL 应失效")
        self.assertEqual(len(cache), 0, "失效条目应被移除")

    def test_ttl_zero_disables_cache(self):
        cache = TTLCache(ttl=0)
        cache.set("k", "v")
        self.assertIsNone(cache.get("k"))
        self.assertEqual(len(cache), 0)
        self.assertFalse(cache.enabled)

    def test_capacity_evicts_oldest(self):
        cache = TTLCache(ttl=60, maxsize=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        self.assertIsNone(cache.get("a"), "超容量应淘汰最旧条目")
        self.assertEqual(cache.get("c"), 3)

    def test_get_promotes_recently_used(self):
        cache = TTLCache(ttl=60, maxsize=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")          # a 变为最近使用
        cache.set("c", 3)       # 应淘汰 b 而非 a
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("b"))

    def test_invalidate_and_clear(self):
        cache = TTLCache(ttl=60)
        cache.set("k", "v")
        cache.invalidate("k")
        self.assertIsNone(cache.get("k"))
        cache.invalidate("不存在")  # 作废不存在的键不炸
        cache.set("x", 1)
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_stats_counters(self):
        cache = TTLCache(ttl=60)
        self.assertIsNone(cache.get("miss"))
        cache.set("k", "v")
        self.assertEqual(cache.get("k"), "v")
        self.assertEqual((cache.hits, cache.misses), (1, 1))


class _CountingBridge:
    """记录各源调用次数的假音源桥。"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def search(self, source, kw, limit=5):
        self.calls.append((source, kw))
        return self.responses.get(source, [])

    async def get_url(self, platform, song_info, quality):
        self.calls.append((platform, quality))
        return f"http://cdn.{platform}.com/{song_info['song_id']}-{quality}.mp3"


class TestSearchCache(unittest.TestCase):

    def _run(self, bridge, keyword, singer="", exclude_source=None):
        with patch("bot.search.get_bridge", return_value=bridge):
            return asyncio.run(search_song(None, keyword, singer, exclude_source=exclude_source))

    def test_second_search_reuses_cache_without_hitting_sources(self):
        resp = {s: [{"name": "告白气球", "singer": "周杰伦", "id": "MUSIC_001"}]
                for s in ("kw", "wy", "tx", "kg", "mg")}
        bridge = _CountingBridge(resp)
        first = self._run(bridge, "告白气球", "周杰伦")
        self.assertIsNotNone(first)
        calls_after_first = len(bridge.calls)
        second = self._run(bridge, "告白气球", "周杰伦")
        self.assertIsNotNone(second)
        self.assertEqual(len(bridge.calls), calls_after_first, "缓存命中不应再打音源")
        self.assertEqual(second.song_id, first.song_id)

    def test_different_query_not_shared(self):
        bridge = _CountingBridge({s: [{"name": "晴天", "singer": "周杰伦", "id": "2"}]
                                  for s in ("kw", "wy", "tx", "kg", "mg")})
        self._run(bridge, "晴天", "周杰伦")
        n = len(bridge.calls)
        self._run(bridge, "稻香", "周杰伦")
        self.assertEqual(len(bridge.calls), n + 5, "不同查询应重新搜索全部音源")

    def test_exclude_source_is_part_of_key(self):
        bridge = _CountingBridge({s: [{"name": "晴天", "singer": "周杰伦", "id": "2"}]
                                  for s in ("kw", "wy", "tx", "kg", "mg")})
        self._run(bridge, "晴天", "周杰伦")
        n = len(bridge.calls)
        self._run(bridge, "晴天", "周杰伦", exclude_source=["kw"])
        self.assertEqual(len(bridge.calls), n + 4, "排除源改变了检索范围，不应命中旧缓存")
        self.assertNotIn("kw", {c[0] for c in bridge.calls[n:]})

    def test_negative_result_not_cached(self):
        bridge = _CountingBridge({})
        self.assertIsNone(self._run(bridge, "不存在的歌XYZ"))
        bridge.responses = {s: [{"name": "不存在的歌XYZ", "singer": "无名", "id": "3"}]
                            for s in ("kw", "wy", "tx", "kg", "mg")}
        self.assertIsNotNone(self._run(bridge, "不存在的歌XYZ"), "未找到不应被缓存，后续可重新命中")


class TestMusicUrlCache(unittest.TestCase):

    def test_set_get_invalidate(self):
        song = SongInfo(name="晴天", singer="周杰伦", song_id="2", platform="kw")
        cache_music_url(song, "320k", "http://cdn.example/a.mp3")
        self.assertEqual(cached_music_url(song, "320k"), "http://cdn.example/a.mp3")
        invalidate_music_url(song, "320k")
        self.assertIsNone(cached_music_url(song, "320k"))

    def test_non_http_url_not_cached(self):
        song = SongInfo(name="晴天", singer="周杰伦", song_id="2", platform="kw")
        cache_music_url(song, "320k", "ftp://bad")
        self.assertIsNone(cached_music_url(song, "320k"))

    def test_key_falls_back_to_name_singer_when_no_id(self):
        song = SongInfo(name="晴天", singer="周杰伦", song_id="", platform="kw")
        cache_music_url(song, "128k", "http://cdn.example/b.mp3")
        self.assertEqual(cached_music_url(song, "128k"), "http://cdn.example/b.mp3")

    def test_stats_shape(self):
        stats = cache_stats()
        self.assertIn("search", stats)
        self.assertIn("url", stats)
        self.assertIn("hits", stats["search"])


class TestPlaybackUrlCacheIntegration(IsolatedAsyncioTestCase):
    """core._try_play_song 真正复用 URL 缓存；失效链接会被作废并重新取链。"""

    async def _play_once(self, bot_ins, song):
        return await bot_ins._try_play_song(song)

    def _make_bot(self, bridge, played=True):
        from bot.core import BiliMusicBot
        from bot.player import PlaybackManager

        async def fake_play(url, song, on_finish=None, on_start=None):
            # 契约：下载失败返回 False 且不调用 on_finish
            if played and on_finish:
                await on_finish(True)
            return played

        bot_ins = BiliMusicBot()
        bot_ins.playback = PlaybackManager(session=None)
        bot_ins.playback.play = fake_play
        bot_ins.sender = type("S", (), {"send": lambda self, m: None})()
        bot_ins.session = None
        return bot_ins

    async def test_second_play_reuses_url_cache(self):
        bridge = _CountingBridge({})
        bot_ins = self._make_bot(bridge)
        song = SongInfo(name="晴天", singer="周杰伦", song_id="2", platform="kw")
        with patch("bot.core.get_bridge", return_value=bridge):
            self.assertTrue(await self._play_once(bot_ins, song))
            first_calls = len(bridge.calls)
            self.assertEqual(first_calls, 5, "首播应并发取 5 个音质的链接")
            self.assertTrue(await self._play_once(bot_ins, song))
            self.assertEqual(len(bridge.calls), first_calls, "二次播放应全部命中 URL 缓存")
        self.assertEqual(cached_music_url(song, "flac24bit"),
                         "http://cdn.kw.com/2-flac24bit.mp3")

    async def test_validation_failure_invalidates_cached_url(self):
        bridge = _CountingBridge({})
        bot_ins = self._make_bot(bridge)

        async def fail_validate(session, url, platform=""):
            return False

        song = SongInfo(name="晴天", singer="周杰伦", song_id="2", platform="kw")
        with patch("bot.core.get_bridge", return_value=bridge), \
                patch("bot.core.validate_audio_url", fail_validate):
            await self._play_once(bot_ins, song)
        self.assertIsNone(cached_music_url(song, "flac24bit"), "预验证失败的链接应作废缓存")

    async def test_download_failure_invalidates_quality(self):
        bridge = _CountingBridge({})
        bot_ins = self._make_bot(bridge, played=False)
        song = SongInfo(name="晴天", singer="周杰伦", song_id="2", platform="kw")
        with patch("bot.core.get_bridge", return_value=bridge):
            self.assertFalse(await self._play_once(bot_ins, song), "全音质下载失败应返回 False（可跨源回退）")
        for q in ("flac24bit", "flac", "320k", "192k", "128k"):
            self.assertIsNone(cached_music_url(song, q), f"下载失败后 {q} 缓存应被作废")

    async def test_fully_cached_song_skips_bridge_entirely(self):
        bridge = _CountingBridge({})
        bot_ins = self._make_bot(bridge)
        song = SongInfo(name="晴天", singer="周杰伦", song_id="2", platform="kw")
        for q in ("flac24bit", "flac", "320k", "192k", "128k"):
            cache_music_url(song, q, f"http://cdn.kw.com/{q}.mp3")
        with patch("bot.core.get_bridge", return_value=bridge):
            self.assertTrue(await self._play_once(bot_ins, song))
        self.assertEqual(bridge.calls, [], "全命中缓存时不应再调用音源")


if __name__ == '__main__':
    unittest.main(verbosity=2)
