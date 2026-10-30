#!/usr/bin/env python3
"""队列持久化单元测试：重启恢复、增删同步落盘、损坏文件兜底。

差异点：进程重启后未播完的点歌队列自动恢复（同类项目多为纯内存队列）。
用法: python3 tests/test_queue_persist.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.models import PlayQueue, QueueItem, SongInfo


def _item(name, singer="周杰伦", requester="小明", uid=0, url="", alternates=None):
    return QueueItem(
        song=SongInfo(name=name, singer=singer, song_id=f"ID-{name}", platform="kw",
                      alternates=alternates or []),
        url=url, requester=requester, requester_uid=uid,
    )


class TestQueuePersistence(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "queue.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_restores_order_and_fields(self):
        q = PlayQueue(self.path)
        q.add(_item("A", requester="甲", uid=11))
        q.add(_item("B", requester="乙", uid=22))
        # 模拟进程重启：新实例从同一文件恢复
        restored = PlayQueue(self.path)
        self.assertEqual(restored.size, 2)
        names = [it.song.name for it in restored.snapshot()]
        self.assertEqual(names, ["A", "B"], "恢复后保持原队列顺序")
        self.assertEqual(restored.peek().requester, "甲")
        self.assertEqual(restored.peek().requester_uid, 11)

    def test_next_is_persisted(self):
        q = PlayQueue(self.path)
        q.add(_item("A"))
        q.add(_item("B"))
        self.assertEqual(q.next().song.name, "A")
        restored = PlayQueue(self.path)
        self.assertEqual([it.song.name for it in restored.snapshot()], ["B"])

    def test_remove_current_is_persisted(self):
        q = PlayQueue(self.path)
        q.add(_item("A"))
        q.add(_item("B"))
        q.remove_current()
        restored = PlayQueue(self.path)
        self.assertEqual([it.song.name for it in restored.snapshot()], ["B"])

    def test_remove_at_is_persisted(self):
        q = PlayQueue(self.path)
        for n in ("A", "B", "C"):
            q.add(_item(n))
        removed = q.remove_at(1)
        self.assertEqual(removed.song.name, "B")
        restored = PlayQueue(self.path)
        self.assertEqual([it.song.name for it in restored.snapshot()], ["A", "C"])
        self.assertIsNone(q.remove_at(9), "越界撤回返回 None 且不改动队列")
        self.assertEqual(PlayQueue(self.path).size, 2)

    def test_alternates_and_unicode_preserved(self):
        alt = SongInfo(name="晴天", singer="周杰伦", song_id="ALT", platform="wy")
        q = PlayQueue(self.path)
        q.add(_item("晴天.", singer="周杰伦", alternates=[alt]))
        restored = PlayQueue(self.path)
        item = restored.peek()
        self.assertEqual(item.song.name, "晴天.")
        self.assertEqual(len(item.song.alternates), 1)
        self.assertEqual(item.song.alternates[0].platform, "wy")

    def test_url_not_persisted(self):
        q = PlayQueue(self.path)
        q.add(_item("A", url="http://cdn.example/expired.mp3"))
        restored = PlayQueue(self.path)
        self.assertEqual(restored.peek().url, "", "CDN 链接过期失效，恢复时应重新取链")

    def test_memory_only_queue_writes_nothing(self):
        q = PlayQueue(None)
        q.add(_item("A"))
        q.next()
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(q.is_empty)

    def test_missing_file_starts_empty(self):
        q = PlayQueue(self.path)
        self.assertEqual(q.size, 0)

    def test_corrupt_file_resets_silently(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken json")
        q = PlayQueue(self.path)  # 不应抛异常
        self.assertEqual(q.size, 0)
        q.add(_item("A"))
        restored = PlayQueue(self.path)
        self.assertEqual(restored.size, 1, "损坏文件应被新写入覆盖")

    def test_non_list_json_ignored(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"not": "a list"}, f)
        self.assertEqual(PlayQueue(self.path).size, 0)

    def test_entries_without_song_skipped(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([{"requester": "无歌"}, {"song": {"name": "好歌", "singer": "x"}}], f)
        q = PlayQueue(self.path)
        self.assertEqual(q.size, 1)
        self.assertEqual(q.peek().song.name, "好歌")

    def test_unwritable_path_degrades_to_memory(self):
        bad_path = os.path.join(self._tmp.name, "no-such-dir", "queue.json")
        q = PlayQueue(bad_path)  # 加载失败静默
        q.add(_item("A"))
        self.assertEqual(q.size, 1, "落盘失败不应影响内存队列")
        self.assertEqual(q.next().song.name, "A")


class TestCoreQueueWiring(unittest.TestCase):

    def test_bot_uses_configured_queue_file(self):
        from bot import core
        bot_ins = core.BiliMusicBot()
        self.assertEqual(bot_ins.queue.persist_path, core.QUEUE_FILE)
        self.assertEqual(bot_ins.queue.size, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
