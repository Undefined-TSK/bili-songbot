#!/usr/bin/env python3
"""内核增强单元测试：跨平台共识加权 + 显式版本词例外（TDD：先 RED 后 GREEN）。

这些能力源自对洛雪/LX Music/Levyra/Q-music 等聚合播放器方案的调研：
- 共识加权（Levyra/LX）：同一 (歌名,歌手) 被 >=2 音源共同返回 => 规范原版，加权盖过单源误排。
- 显式版本词例外（Q-music）：用户主动点名翻唱/remix/live 等版本时，该版本精准命中不降权。
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.search import (
    _score_search_result, _select_best_with_consensus,
    _detect_query_version, search_song,
)
from bot.core import split_play_query
from bot.models import SongInfo


class TestArtistAlias(unittest.TestCase):
    """中英/多语言艺名别名等价（通用增强）：中文艺名、英文名、混写、昵称互认。"""

    def test_zh_to_en(self):
        from bot.search import _alias_match
        self.assertTrue(_alias_match('贾斯汀比伯', 'Justin Bieber'))
        self.assertTrue(_alias_match('Justin Bieber', '贾斯汀比伯'))

    def test_hybrid_mixed_script(self):
        # 中英混写（如 中文音译+英文姓氏）也应与标准英文/中文写法互认
        from bot.search import _alias_match
        self.assertTrue(_alias_match('迈克尔jackson', 'Michael Jackson'))
        self.assertTrue(_alias_match('迈克尔jackson', '迈克尔·杰克逊'))

    def test_nickname(self):
        from bot.search import _alias_match
        self.assertTrue(_alias_match('霉霉', 'Taylor Swift'))
        self.assertTrue(_alias_match('mj', 'Michael Jackson'))

    def test_chinese_community_nickname(self):
        # 歌手中文昵称（如"蹲妹"）应与英文艺名互认
        from bot.search import _alias_match
        self.assertTrue(_alias_match('蹲妹', 'Carly Rae Jepsen'))
        self.assertTrue(_alias_match('Carly Rae Jepsen', '蹲妹'))

    def test_unrelated_rejected(self):
        from bot.search import _alias_match
        self.assertFalse(_alias_match('周杰伦', '林俊杰'))
        self.assertFalse(_alias_match('贾斯汀比伯', 'Taylor Swift'))
        self.assertFalse(_alias_match('', 'Michael Jackson'))

    def test_scoring_alias_as_inclusion(self):
        # 别名等价按「包含歌手」档计分（+30），而非 -10
        sc = _score_search_result('Thriller', 'Michael Jackson', 'thriller', '迈克尔jackson')
        self.assertEqual(sc, 100 + 30)


class TestAliasSearchSong(unittest.TestCase):
    """mock 桥验证 search_song 全链路应用艺名别名（此前这两类查询会因跨语言歌手不匹配而落空）。"""

    def test_pure_zh_singer_finds_en_artist(self):
        # 只搜中文艺名：各音源返回英文艺名，应通过别名互认命中
        resp = {s: [{"name": "Baby", "singer": "Justin Bieber、Ludacris", "id": "1"}]
                for s in ("kw", "wy", "tx", "kg", "mg")}
        bridge = FakeBridge(resp)
        with patch("bot.search.get_bridge", return_value=bridge):
            song = asyncio.run(search_song(None, "贾斯汀比伯", ""))
        self.assertIsNotNone(song)
        self.assertEqual(song.name, "Baby")

    def test_split_singer_hybrid_passes_filter(self):
        # 拆分路径（歌名, 中英混写歌手）：此前歌手过滤器会全部拒绝，别名后应通过
        resp = {s: [{"name": "Thriller", "singer": "Michael Jackson", "id": "2"}]
                for s in ("kw", "wy", "tx", "kg", "mg")}
        bridge = FakeBridge(resp)
        with patch("bot.search.get_bridge", return_value=bridge):
            song = asyncio.run(search_song(None, "thriller", "迈克尔jackson"))
        self.assertIsNotNone(song)
        self.assertEqual(song.name, "Thriller")

    def test_consensus_across_languages(self):
        # 同一歌手被不同音源以中/英文写法返回 -> 应视为同一人成立跨语言共识
        top = {
            "kw": (100, "Thriller", "迈克尔·杰克逊", {"id": "1"}),
            "wy": (100, "Thriller", "Michael Jackson", {"id": "2"}),
            "tx": (100, "Thriller", "Michael Jackson", {"id": "2"}),
            "kg": (100, "Thriller", "Michael Jackson", {"id": "2"}),
        }
        chosen = _select_best_with_consensus(top, None)
        self.assertEqual(chosen[0], 135)  # 100 + 跨语言共识加权 35
        self.assertEqual(chosen[3], "Thriller")


class FakeBridge:
    """mock 系统边界（Node 桥）：按音源返回预设结果，可控制各源响应延迟以决定完成顺序。"""

    def __init__(self, responses: dict, delay_sources: dict = None):
        self.responses = responses
        self.delay = delay_sources or {}

    async def search(self, source: str, kw: str, limit: int = 5):
        if source in self.delay:
            await asyncio.sleep(self.delay[source])
        return self.responses.get(source, [])


class TestSelectBestWithConsensus(unittest.TestCase):
    """跨平台共识纯函数（确定性，不依赖 asyncio 顺序）。"""

    def test_consensus_picks_canonical(self):
        # 李健版仅 1 源、王菲版被 4 源共识 -> 选王菲（原唱/规范版）
        top = {
            "kw": (100, "传奇", "李健", {"id": "1"}),
            "wy": (100, "传奇", "王菲", {"id": "2"}),
            "tx": (100, "传奇", "王菲", {"id": "2"}),
            "kg": (100, "传奇", "王菲", {"id": "2"}),
            "mg": (100, "传奇", "王菲", {"id": "2"}),
        }
        chosen = _select_best_with_consensus(top, None)
        self.assertEqual(chosen[3], "传奇")   # name
        self.assertEqual(chosen[4], "王菲")    # singer（共识胜出）

    def test_no_consensus_falls_back_to_max(self):
        # 各源结果互不相同 -> 退回全局最高分
        top = {
            "kw": (100, "A", "x", {"id": "1"}),
            "wy": (120, "B", "y", {"id": "2"}),
            "tx": (90, "C", "z", {"id": "3"}),
        }
        chosen = _select_best_with_consensus(top, None)
        self.assertEqual(chosen[3], "B")
        self.assertEqual(chosen[0], 120)

    def test_empty_returns_fallback(self):
        self.assertIsNone(_select_best_with_consensus({}, None))


class TestVersionQueryDetection(unittest.TestCase):

    def test_detect_cover(self):
        self.assertEqual(_detect_query_version("告白气球 翻唱"), "翻唱")
        self.assertEqual(_detect_query_version("告白气球 cover"), "cover")

    def test_detect_none(self):
        self.assertIsNone(_detect_query_version("告白气球 周杰伦"))


class TestScoreVersionException(unittest.TestCase):
    """显式版本词例外：用户点名某版本时，该版本命中不降权并加分。"""

    def test_cover_requested_no_penalty(self):
        # 用户点名"翻唱"：Cover 版应得 +25（版本命中）而非 -55。
        # query_version 由 search_song 从查询检测到后注入；此处 kw 为干净歌名，
        # Cover 归一化后与之精确匹配(+100)。
        with_version = _score_search_result(
            '告白气球 (Cover)', '周杰伦', '告白气球', '周杰伦', query_version='cover')
        self.assertEqual(with_version, 100 + 50 + 25)  # 歌名精确+歌手+版本命中

    def test_cover_without_request_penalized(self):
        # 未点名版本时，Cover 版照常 -55 降权（100+50-55=95，低于原版150）
        without = _score_search_result(
            '告白气球 (Cover)', '周杰伦', '告白气球', '周杰伦')
        self.assertEqual(without, 100 + 50 - 55)

    def test_version_request_makes_cover_beat_original(self):
        # 点名翻唱时，Cover(175) 应高于原版(150)，版本例外生效（否则原版胜出）
        cover = _score_search_result(
            '告白气球 (Cover)', '周杰伦', '告白气球', '周杰伦', query_version='cover')
        original = _score_search_result(
            '告白气球', '周杰伦', '告白气球', '周杰伦')
        self.assertGreater(cover, original)


class TestSplitVersionQuery(unittest.TestCase):
    """显式版本词查询：整句作为歌名，不误拆歌手、不丢版本信号。"""

    def test_version_word_kept_as_song(self):
        c = split_play_query('告白气球 翻唱')
        self.assertEqual(c, [('告白气球 翻唱', '')])

    def test_version_word_english(self):
        c = split_play_query('see you again remix')
        self.assertEqual(c, [('see you again remix', '')])


class TestSearchSongConsensus(unittest.TestCase):
    """用 mock 桥验证 search_song 真正采用跨平台共识（不依赖 asyncio 完成顺序）。"""

    def test_consensus_overrides_single_source(self):
        minority = [{"name": "传奇", "singer": "李健", "id": "1"}]
        majority = [{"name": "传奇", "singer": "王菲", "id": "2"}]
        resp = {"kw": minority, "wy": majority, "tx": majority,
                "kg": majority, "mg": majority}
        # 让少数源(kw)立即返回、多数源稍慢 -> 模拟"首响源给出错误候选"的真实竞态
        bridge = FakeBridge(resp, delay_sources={"wy": 0.02, "tx": 0.02, "kg": 0.02, "mg": 0.02})
        with patch("bot.search.get_bridge", return_value=bridge):
            song = asyncio.run(search_song(None, "传奇", ""))
        self.assertIsNotNone(song)
        self.assertEqual(song.singer, "王菲")  # 共识盖过单源误排

    def test_populates_cross_source_alternates(self):
        # 同名同歌手歌曲被 kw/wy/kg 多源共同返回 -> 主候选之外的同歌平台作为跨源回退候选
        same = {"name": "真夜中のドア Single", "singer": "松原みき", "id": "X1"}
        resp = {"kw": [same], "wy": [same], "kg": [same], "tx": [], "mg": []}
        bridge = FakeBridge(resp)
        with patch("bot.search.get_bridge", return_value=bridge):
            song = asyncio.run(search_song(None, "真夜中のドア", "松原みき"))
        self.assertIsNotNone(song)
        self.assertIn(song.platform, {"kw", "wy", "kg"})
        alt_plat = {a.platform for a in song.alternates}
        # 主候选 + 跨源候选 覆盖全部 3 个同歌平台
        self.assertEqual({song.platform} | alt_plat, {"kw", "wy", "kg"})
        for a in song.alternates:
            self.assertEqual(a.name, "真夜中のドア Single")
            self.assertEqual(a.singer, "松原みき")


class TestCrossSourcePlaybackFallback(unittest.TestCase):
    """主源不可用（下载失败）时，跨源回退到同歌其他平台并播放成功（严守同歌不播错）。"""

    def test_fallback_to_alternate_when_primary_download_fails(self):
        from bot.core import BiliMusicBot
        from bot.player import PlaybackManager

        plays = []      # 记录实际播放的 (platform, url)
        finished = []   # 记录 on_finish(success) 调用

        async def fake_play(url, song, on_finish=None, on_start=None):
            plays.append((song.platform, url))
            if song.platform == "kw":
                return False  # 主源下载失败（play 契约：未调用 on_finish，可重试）
            if on_finish:
                await on_finish(True)
            return True

        class BridgeURLs:
            async def get_url(self, platform, song_info, quality):
                return f"http://src.{platform}.com/{quality}.mp3"

        bot_ins = BiliMusicBot()
        bot_ins.playback = PlaybackManager(session=None)
        bot_ins.playback.play = fake_play
        bot_ins.sender = type("S", (), {"send": lambda self, m: finished.append(m)})()

        primary = SongInfo(name="X", singer="Y", platform="kw",
                           alternates=[SongInfo(name="X", singer="Y", platform="wy")])
        with patch("bot.core.get_bridge", return_value=BridgeURLs()):
            asyncio.run(bot_ins._play_with_fallback(primary))

        # 主源(kw)下载失败应被跳过，回退到 wy 并播放成功
        self.assertTrue(any(p[0] == "kw" for p in plays), "主源应有尝试")
        self.assertTrue(any(p[0] == "wy" for p in plays), "主源失败后应回退到 wy 播放")
        self.assertTrue(plays, "至少应有一次真实播放调用")


class TestProxyUrlNotRejected(unittest.TestCase):
    """代理 .php 链接不应被预先拒绝（实测已返回真实音频 ID3 魔数），
    应交给下载层的小文件/魔数校验兜底判断。"""

    def test_all_proxy_urls_still_tried_for_download(self):
        from bot.core import BiliMusicBot
        from bot.player import PlaybackManager

        plays = []

        async def fake_play(url, song, on_finish=None, on_start=None):
            plays.append((song.platform, url))
            if on_finish:
                await on_finish(True)
            return True

        class BridgeProxyOnly:
            async def get_url(self, platform, song_info, quality):
                return f"http://x/musicapi/{platform}.php?type=mp3&id=1&q={quality}"

        bot_ins = BiliMusicBot()
        bot_ins.playback = PlaybackManager(session=None)
        bot_ins.playback.play = fake_play
        bot_ins.sender = type("S", (), {"send": lambda self, m: None})()

        primary = SongInfo(name="X", singer="Y", platform="kw")
        with patch("bot.core.get_bridge", return_value=BridgeProxyOnly()):
            asyncio.run(bot_ins._play_with_fallback(primary))

        self.assertTrue(any(p[0] == "kw" for p in plays), "全代理 URL 也应尝试下载播放，而非直接判死")


class TestGarbageEchoFilter(unittest.TestCase):
    """回显垃圾过滤增强：搜索词被 API 原样当歌名返回（哪怕配了假歌手）应被跳过。"""

    def test_echo_with_fake_singer_rejected(self):
        # mg 类平台把 "firework Katy Perry" 原样当歌名 + 假歌手 -> 垃圾，不能盖过真候选
        garbage = {"name": "Firework Katy Perry", "singer": "草莓波波", "id": "9"}
        real = {"name": "Firework", "singer": "Katy Perry", "id": "1"}
        resp = {s: [garbage, real] for s in ("kw", "wy", "tx", "kg", "mg")}
        bridge = FakeBridge(resp)
        with patch("bot.search.get_bridge", return_value=bridge):
            song = asyncio.run(search_song(None, "firework Katy Perry", ""))
        self.assertIsNotNone(song)
        self.assertEqual(song.name, "Firework")
        self.assertEqual(song.singer, "Katy Perry")

    def test_exact_song_name_not_rejected(self):
        # 纯歌名搜索（无空格）返回同名歌是正常精确匹配，绝不能误杀
        resp = {s: [{"name": "Lemon", "singer": "米津玄師", "id": "1"}]
                for s in ("kw", "wy", "tx", "kg", "mg")}
        bridge = FakeBridge(resp)
        with patch("bot.search.get_bridge", return_value=bridge):
            song = asyncio.run(search_song(None, "Lemon", ""))
        self.assertIsNotNone(song)
        self.assertEqual(song.name, "Lemon")


if __name__ == '__main__':
    unittest.main(verbosity=2)
