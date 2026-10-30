#!/usr/bin/env python3
"""PlayQueue / SongInfo 单元测试：FIFO 行为与数据模型。
用法: python3 tests/test_queue.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.models import PlayQueue, QueueItem, SongInfo


def _item(name):
    return QueueItem(song=SongInfo(name=name, singer='周杰伦'), requester='测试')


class TestPlayQueue(unittest.TestCase):

    def test_fifo_order(self):
        q = PlayQueue()
        self.assertTrue(q.is_empty)
        self.assertEqual(q.size, 0)
        self.assertIsNone(q.next(), '空队列 next 返回 None')
        self.assertIsNone(q.peek(), '空队列 peek 返回 None')

        self.assertEqual(q.add(_item('A')), 1)
        self.assertEqual(q.add(_item('B')), 2)
        self.assertEqual(q.size, 2)
        self.assertFalse(q.is_empty)

        self.assertEqual(q.peek().song.name, 'A', 'peek 不移除队首')
        self.assertEqual(q.size, 2, 'peek 后长度不变')
        self.assertEqual(q.next().song.name, 'A', 'next 按 FIFO 出队')
        self.assertEqual(q.next().song.name, 'B')
        self.assertTrue(q.is_empty)
        self.assertIsNone(q.next())

    def test_remove_current(self):
        q = PlayQueue()
        q.add(_item('A'))
        q.add(_item('B'))
        q.remove_current()
        self.assertEqual(q.size, 1)
        self.assertEqual(q.peek().song.name, 'B')
        q.remove_current()
        self.assertTrue(q.is_empty)
        q.remove_current()  # 空队列调用不炸
        self.assertTrue(q.is_empty)

    def test_requester_preserved(self):
        q = PlayQueue()
        q.add(QueueItem(song=SongInfo(name='稻香'), requester='用户123'))
        item = q.next()
        self.assertEqual(item.requester, '用户123')
        self.assertEqual(item.song.name, '稻香')


class TestSongInfo(unittest.TestCase):

    def test_search_keyword(self):
        self.assertEqual(SongInfo(name='告白气球', singer='周杰伦').search_keyword, '告白气球 周杰伦')
        self.assertEqual(SongInfo(name='告白气球').search_keyword, '告白气球', '无歌手时只返回歌名')

    def test_defaults(self):
        s = SongInfo()
        self.assertEqual((s.name, s.singer, s.album, s.song_id, s.platform, s.interval), ('', '', '', '', '', ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
