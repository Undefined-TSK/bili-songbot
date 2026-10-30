"""交互层测试：/queue /now /remove /top /help 命令、did-you-mean 智能建议、自定义回复模板。

全部离线（假 sender + 现成对象），不碰网络；验证纯增量交互层不破坏既有链路。
"""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import PropertyMock as _PropertyMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.models import PlayQueue, QueueItem, SongInfo


def _song(name="告白气球", singer="周杰伦", score=150, platform="wy"):
    return SongInfo(name=name, singer=singer, platform=platform, song_id="1", score=score)


class FakeSender:
    def __init__(self):
        self.messages = []

    async def send(self, msg):
        self.messages.append(msg)

    def joined(self):
        return "\n".join(self.messages)


def _bot(tmp_stats=None):
    from bot.core import BiliMusicBot
    bot_ins = BiliMusicBot()
    bot_ins.sender = FakeSender()
    if tmp_stats is not None:
        from bot.stats import SongStats
        bot_ins.stats = SongStats(tmp_stats)
    return bot_ins


class TestQueueCommands(unittest.TestCase):
    """/queue /now /remove /top 交互命令。"""

    def test_queue_empty(self):
        bot_ins = _bot()
        asyncio.run(bot_ins._handle_queue())
        self.assertIn("队列为空", bot_ins.sender.joined())

    def test_queue_lists_items_with_requester(self):
        bot_ins = _bot()
        bot_ins.queue.add(QueueItem(song=_song("晴天"), requester="小明"))
        bot_ins.queue.add(QueueItem(song=_song("江南"), requester="小红"))
        asyncio.run(bot_ins._handle_queue())
        out = bot_ins.sender.joined()
        self.assertIn("1. 晴天-小明", out)
        self.assertIn("2. 江南-小红", out)

    def test_now_playing_and_idle(self):
        bot_ins = _bot()
        asyncio.run(bot_ins._handle_now())
        self.assertIn("当前没有在播放", bot_ins.sender.joined())
        bot_ins.playback.current_song = _song("青花瓷")
        asyncio.run(bot_ins._handle_now())
        self.assertIn("青花瓷", bot_ins.sender.joined())

    def test_remove_own_song(self):
        bot_ins = _bot()
        bot_ins.queue.add(QueueItem(song=_song("晴天"), requester="小明"))
        bot_ins.queue.add(QueueItem(song=_song("江南"), requester="小红"))
        asyncio.run(bot_ins._handle_remove("1", "小明"))
        self.assertIn("已撤回", bot_ins.sender.joined())
        self.assertEqual(bot_ins.queue.size, 1)
        self.assertEqual(bot_ins.queue.snapshot()[0].song.name, "江南")

    def test_remove_other_persons_song_denied(self):
        bot_ins = _bot()
        bot_ins.queue.add(QueueItem(song=_song("晴天"), requester="小明"))
        asyncio.run(bot_ins._handle_remove("1", "路人甲"))
        self.assertIn("只能撤回自己点的歌", bot_ins.sender.joined())
        self.assertEqual(bot_ins.queue.size, 1, "他人歌曲不可被撤回")

    def test_remove_bad_index(self):
        bot_ins = _bot()
        bot_ins.queue.add(QueueItem(song=_song("晴天"), requester="小明"))
        asyncio.run(bot_ins._handle_remove("99", "小明"))
        asyncio.run(bot_ins._handle_remove("abc", "小明"))
        self.assertEqual(bot_ins.sender.messages.count(
            __import__("bot.config", fromlist=["reply"]).reply("remove_bad_index")), 2)
        self.assertEqual(bot_ins.queue.size, 1)


class TestSmartSuggest(unittest.TestCase):
    """did-you-mean：点歌未找到时给出最接近候选建议（只提示不播放）。"""

    def test_suggest_song_raw_closest(self):
        # 建议层绕过预过滤 + token 配对评分：长查询（批量失败回退场景）
        # 中的「歌名精确命中 + 歌手 token 互认」能被建议出来
        from bot.search import suggest_song

        class FakeBridge:
            async def search(self, src, kw, limit=5):
                return [{"name": "Gee", "singer": "少女时代"}]

        with patch("bot.search.get_bridge", return_value=FakeBridge()):
            sug = asyncio.run(suggest_song(None, "gee 少女时代 a妹 u will nev know"))
        self.assertIsNotNone(sug)
        self.assertEqual(sug.name, "Gee")
        self.assertEqual(sug.singer, "少女时代")

    def test_suggest_exact_name_alone(self):
        # 歌名完全一致（哪怕没给歌手）即可建议
        from bot.search import suggest_song

        class FakeBridge:
            async def search(self, src, kw, limit=5):
                return [{"name": "告白气球", "singer": "周杰伦"}]

        with patch("bot.search.get_bridge", return_value=FakeBridge()):
            sug = asyncio.run(suggest_song(None, "告白气球（吉他版超好听的）"))
        self.assertIsNotNone(sug)
        self.assertEqual(sug.name, "告白气球")

    def test_suggest_garbage_returns_none(self):
        # 完全无关的输入（歌名只拿底分10分）不给建议，避免瞎猜
        from bot.search import suggest_song

        class FakeBridge:
            async def search(self, src, kw, limit=5):
                return [{"name": "完全无关的歌", "singer": "路人甲"}]

        with patch("bot.search.get_bridge", return_value=FakeBridge()):
            sug = asyncio.run(suggest_song(None, "天马行空瞎打的词"))
        self.assertIsNone(sug)

    def test_suggest_skips_echo_garbage(self):
        # 垃圾回显（歌名=完整搜索词原样）不作为建议
        from bot.search import suggest_song

        class FakeBridge:
            async def search(self, src, kw, limit=5):
                return [{"name": "告白求球 周杰轮", "singer": "草莓波波"}]

        with patch("bot.search.get_bridge", return_value=FakeBridge()):
            sug = asyncio.run(suggest_song(None, "告白求球 周杰轮"))
        self.assertIsNone(sug)

    def test_not_found_triggers_suggestion(self):
        bot_ins = _bot()

        async def fake_resolve(query):
            return None

        async def fake_suggest(session, kw):
            return _song("告白气球", "周杰伦", score=65)

        with patch.object(bot_ins, "_resolve_single", side_effect=fake_resolve), \
             patch("bot.core.suggest_song", side_effect=fake_suggest):
            song = asyncio.run(bot_ins._handle_play("告白求球", "小明"))
        self.assertIsNone(song)
        out = bot_ins.sender.joined()
        self.assertIn("你是不是想点", out)
        self.assertIn("告白气球", out)
        self.assertIn("周杰伦", out)

    def test_no_suggestion_plain_not_found(self):
        bot_ins = _bot()

        async def fake_resolve(query):
            return None

        async def fake_suggest(session, kw):
            return None

        with patch.object(bot_ins, "_resolve_single", side_effect=fake_resolve), \
             patch("bot.core.suggest_song", side_effect=fake_suggest):
            asyncio.run(bot_ins._handle_play("完全不存在歌", "小明"))
        self.assertIn("未找到", bot_ins.sender.joined())
        self.assertNotIn("你是不是想点", bot_ins.sender.joined())


class TestSongStats(unittest.TestCase):
    """点歌统计：记录 / 排行 / 持久化。"""

    def test_record_and_top(self):
        from bot.stats import SongStats
        s = SongStats(None)
        s.record("晴天", "周杰伦", "小明")
        s.record("晴天", "周杰伦", "小红")
        s.record("江南", "林俊杰", "小明")
        top = s.top(5)
        self.assertEqual(top[0], ("晴天 - 周杰伦", 2))
        self.assertEqual(len(top), 2)

    def test_persistence_across_instances(self):
        from bot.stats import SongStats
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stats.json")
            s1 = SongStats(path)
            s1.record("晴天", "周杰伦", "小明")
            s2 = SongStats(path)  # 新实例从文件恢复
            self.assertEqual(s2.top(1), [("晴天 - 周杰伦", 1)])

    def test_corrupt_file_silently_resets(self):
        from bot.stats import SongStats
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stats.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{broken json")
            s = SongStats(path)  # 不抛异常
            s.record("晴天", "周杰伦")
            self.assertEqual(s.top(1), [("晴天 - 周杰伦", 1)])

    def test_top_command_output(self):
        with tempfile.TemporaryDirectory() as d:
            bot_ins = _bot(os.path.join(d, "stats.json"))
            bot_ins.stats.record("晴天", "周杰伦", "小明")
            asyncio.run(bot_ins._handle_top())
            out = bot_ins.sender.joined()
            self.assertIn("TOP", out)
            self.assertIn("晴天 - 周杰伦 x1", out)


class TestReplyTemplates(unittest.TestCase):
    """自定义回复模板：默认文案零变化，config 覆盖即生效。"""

    def test_default_texts_unchanged(self):
        from bot.config import reply
        self.assertEqual(reply("added", song="晴天", pos=2), "已点: 晴天 (队列:2)")
        self.assertEqual(reply("not_found", query="xxx"), "未找到: xxx")
        self.assertEqual(reply("playing", song="晴天"), "正在播放: 晴天")

    def test_override_template(self):
        from bot import config
        old = config.REPLIES.get("added")
        try:
            config.REPLIES["added"] = "点啦: {song}（第{pos}位）"
            self.assertEqual(config.reply("added", song="晴天", pos=3), "点啦: 晴天（第3位）")
        finally:
            if old is None:
                config.REPLIES.pop("added", None)
            else:
                config.REPLIES["added"] = old

    def test_missing_key_falls_back(self):
        from bot.config import reply
        self.assertEqual(reply("not_exist_key"), "not_exist_key")


class TestPublicStageBoundaries(unittest.TestCase):
    """公共点歌台场景边界：自己点的歌随意处置，别人点的歌不能乱搞。"""

    def test_own_song_switch_directly(self):
        # 自己点的歌正在播放：/switch 直接切，无需投票
        bot_ins = _bot()
        bot_ins._current_item = QueueItem(song=_song("晴天"), requester="小明", requester_uid=1001)
        stopped = []
        bot_ins.playback.stop = lambda: stopped.append(1)
        asyncio.run(bot_ins._skip_current(
            __import__("bot.config", fromlist=["reply"]).reply("switch_own")))
        self.assertTrue(stopped)
        self.assertIn("自己点的歌自己做主", bot_ins.sender.joined())

    def test_other_song_goes_to_vote(self):
        # 别人点的歌：发起的是投票，不能直接切
        bot_ins = _bot()
        bot_ins._current_item = QueueItem(song=_song("晴天"), requester="小明", requester_uid=1001)
        called = []
        async def fake_start_vote(on_pass, is_still_playing=None):
            called.append(1)
        bot_ins.vote.start_vote = fake_start_vote
        asyncio.run(bot_ins._handle_switch())
        self.assertEqual(called, [1], "他人歌曲必须走投票，不得直接切歌")

    def test_uid_ownership_beats_same_username(self):
        # 重名用户边界：uid 不同即使同名也不能处置别人的歌
        bot_ins = _bot()
        item = QueueItem(song=_song("晴天"), requester="小明", requester_uid=1001)
        self.assertTrue(bot_ins._is_owned_by(item, "小明", 1001))
        self.assertFalse(bot_ins._is_owned_by(item, "小明", 9999), "重名不同 uid 不能冒充")
        # 无 uid 记录时退回用户名匹配（兼容历史数据）
        legacy = QueueItem(song=_song("晴天"), requester="小明")
        self.assertTrue(bot_ins._is_owned_by(legacy, "小明", 0))

    def test_route_switch_own_vs_vote(self):
        # 路由层：uid 归属正确时直接切；否则走投票任务
        bot_ins = _bot()
        bot_ins._current_item = QueueItem(song=_song("晴天"), requester="小明", requester_uid=1001)
        stopped, voted = [], []

        async def fake_skip(msg=""):
            stopped.append(1)

        async def fake_switch():
            voted.append(1)

        bot_ins._skip_current = fake_skip
        bot_ins._handle_switch = fake_switch
        with patch("bot.player.PlaybackManager.is_playing", new_callable=_PropertyMock) as mp:
            mp.return_value = True
            asyncio.run(bot_ins._on_danmaku("/switch", 1001, "小明"))   # 本人 -> 直接切
            asyncio.run(bot_ins._on_danmaku("/switch", 9999, "路人"))   # 他人 -> 投票
        self.assertEqual(stopped, [1])
        self.assertEqual(voted, [1])


class TestDuplicateGuard(unittest.TestCase):
    """公共台防重复：同一首歌不重复入队（防热门歌被反复插队）。"""

    def test_duplicate_in_queue_rejected(self):
        bot_ins = _bot()
        asyncio.run(bot_ins._handle_play("告白气球 周杰伦", "小明"))  # 直接注入歌曲
        # 用 patch 让 _resolve_single 返回同一首歌
        bot_ins.queue.add(QueueItem(song=_song("晴天", "周杰伦"), requester="小红"))

        async def fake_resolve(q):
            return _song("晴天", "周杰伦")

        with patch.object(bot_ins, "_resolve_single", side_effect=fake_resolve):
            r = asyncio.run(bot_ins._handle_play("晴天 周杰伦", "路人"))
        self.assertIsNone(r, "重复歌不应返回成功")
        self.assertIn("不用重复点", bot_ins.sender.joined())
        self.assertEqual(bot_ins.queue.size, 1, "队列不应重复入队")

    def test_duplicate_playing_rejected(self):
        bot_ins = _bot()
        bot_ins._current_item = QueueItem(song=_song("晴天", "周杰伦"), requester="小红")

        async def fake_resolve(q):
            return _song("晴天", "周杰伦")

        with patch.object(bot_ins, "_resolve_single", side_effect=fake_resolve):
            asyncio.run(bot_ins._handle_play("晴天", "路人"))
        self.assertIn("正在播放中", bot_ins.sender.joined())

    def test_same_name_diff_artist_allowed(self):
        # 同名不同歌（晴天-周杰伦 vs 晴天-林俊杰）不误拦
        bot_ins = _bot()
        bot_ins.queue.add(QueueItem(song=_song("晴天", "周杰伦"), requester="小红"))
        self.assertIsNone(bot_ins._find_duplicate(_song("晴天", "林俊杰")))

    def test_no_singer_result_not_blocked_by_singer(self):
        # 队列项未署歌手时，同名歌视为同一首（防钻空子；解析结果正常都带歌手）
        bot_ins = _bot()
        bot_ins.queue.add(QueueItem(song=_song("晴天", ""), requester="小红"))
        self.assertIsNotNone(bot_ins._find_duplicate(_song("晴天", "周杰伦")))


class TestPlayingCredit(unittest.TestCase):
    """公共台氛围：正在播放附点歌人署名。"""

    def test_playing_text_with_requester(self):
        bot_ins = _bot()
        text = bot_ins._playing_text(_song("晴天"), "小明")
        self.assertIn("晴天", text)
        self.assertIn("小明 点播", text)

    def test_playing_text_without_requester(self):
        bot_ins = _bot()
        text = bot_ins._playing_text(_song("晴天"), "")
        self.assertIn("晴天", text)
        self.assertNotIn("点播", text)

    def test_now_shows_credit(self):
        bot_ins = _bot()
        bot_ins._current_item = QueueItem(song=_song("晴天"), requester="小明")
        bot_ins.playback.current_song = _song("晴天")
        asyncio.run(bot_ins._handle_now())
        self.assertIn("小明 点播", bot_ins.sender.joined())


class TestRouting(unittest.TestCase):
    """/on_danmaku 指令路由：新命令触发对应处理器（不碰网络）。"""

    def _routed(self, text):
        bot_ins = _bot()
        calls = []

        async def fake_handler():
            calls.append("queue")

        bot_ins._handle_queue = fake_handler
        asyncio.run(bot_ins._on_danmaku(text, 1, "小明"))
        return bot_ins, calls

    def test_queue_aliases(self):
        for t in ("/queue", "/队列", "/点歌列表"):
            _, calls = self._routed(t)
            self.assertEqual(calls, ["queue"], t)

    def test_help_replies(self):
        bot_ins = _bot()
        asyncio.run(bot_ins._on_danmaku("/help", 1, "小明"))
        out = bot_ins.sender.joined()
        for cmd in ("/play", "/lyric", "/queue", "/remove", "/top", "/switch"):
            self.assertIn(cmd, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
