#!/usr/bin/env python3
"""VoteManager 单元测试：投票门槛、去重、归一化、完整投票流程（无网络）。

把 VOTE_DURATION 打到 2 秒加速测试；sender 用假实现记录弹幕。
用法: python3 tests/test_vote.py
"""
import asyncio
import os
import sys
import unittest
from unittest import IsolatedAsyncioTestCase

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import bot.vote as vote_mod
from bot.vote import VoteManager


class FakeSender:
    """记录发送的弹幕，不真正发请求。"""

    def __init__(self):
        self.messages = []

    async def send(self, message: str):
        self.messages.append(message)


def _on_pass(called):
    """on_pass 契约（core.py 实际用法）：async 回调。"""
    async def on_pass():
        called.append(1)
    return on_pass


async def _noop():
    pass


class TestThreshold(unittest.TestCase):
    """动态门槛公式逐段验证。"""

    def test_threshold_table(self):
        vm = VoteManager(FakeSender())
        cases = {
            0: 1,   # 无观众信息
            1: 1,   # 独自美丽
            2: 2,
            3: 2, 4: 2,        # 2-4 人：2
            5: 3, 6: 3,        # 5-6 人：3
            7: 4, 8: 4,        # (v+1)//2
            9: 5, 10: 5,
            100: 50,           # 大规模直播间半数
            1000: 500,
        }
        for viewers, expected in cases.items():
            vm.update_viewer_count(viewers)
            self.assertEqual(vm.get_threshold(), expected, f'{viewers} 人在线时期望门槛 {expected}')


class TestHandleVote(IsolatedAsyncioTestCase):
    """投票去重 / 归一化 / 无效票占位。"""

    async def test_dedup_and_normalization(self):
        vm = VoteManager(FakeSender())
        vm.active = True
        # 同一 uid 重复投票只计一次
        vm.handle_vote(1, "Y")
        vm.handle_vote(1, "N")
        self.assertEqual(vm.yes_votes, {1})
        self.assertEqual(vm.no_votes, set())
        # 大小写/同义词归一化
        vm.handle_vote(2, "yes")
        vm.handle_vote(3, "1")
        vm.handle_vote(4, "n")
        vm.handle_vote(5, "NO")
        vm.handle_vote(6, "0")
        self.assertEqual(vm.yes_votes, {1, 2, 3})
        self.assertEqual(vm.no_votes, {4, 5, 6})
        # 非 Y/N 内容占用投票权但不计票（原行为保持）
        vm.handle_vote(7, "aaa")
        self.assertEqual(vm.voters, {1, 2, 3, 4, 5, 6, 7})
        self.assertEqual(vm.yes_votes | vm.no_votes, {1, 2, 3, 4, 5, 6})

    async def test_vote_ignored_when_inactive(self):
        vm = VoteManager(FakeSender())
        vm.active = False
        vm.handle_vote(1, "Y")
        self.assertEqual(vm.yes_votes, set())
        self.assertEqual(vm.voters, set())


class TestVoteFlow(IsolatedAsyncioTestCase):
    """完整投票流程（VOTE_DURATION=2 秒加速）。"""

    def setUp(self):
        self._orig_dur = vote_mod.VOTE_DURATION
        self._orig_bc = vote_mod.VOTE_BROADCAST_INTERVAL
        vote_mod.VOTE_DURATION = 2
        vote_mod.VOTE_BROADCAST_INTERVAL = 1

    def tearDown(self):
        vote_mod.VOTE_DURATION = self._orig_dur
        vote_mod.VOTE_BROADCAST_INTERVAL = self._orig_bc

    async def test_pass_calls_on_pass(self):
        sender = FakeSender()
        vm = VoteManager(sender)
        vm.update_viewer_count(0)  # 门槛 1
        called = []
        await vm.start_vote(_on_pass(called))
        vm.handle_vote(101, "Y")
        await asyncio.wait_for(vm._task, timeout=10)
        self.assertTrue(called, '投票通过应回调 on_pass')
        self.assertFalse(vm.active, '结束后 active 复位')
        self.assertTrue(any('正在切歌' in m for m in sender.messages), sender.messages)

    async def test_fail_keeps_playing(self):
        sender = FakeSender()
        vm = VoteManager(sender)
        vm.update_viewer_count(0)  # 门槛 1：Y=1 N=2 未通过
        called = []
        await vm.start_vote(_on_pass(called))
        vm.handle_vote(101, "Y")
        vm.handle_vote(102, "N")
        vm.handle_vote(103, "N")
        await asyncio.wait_for(vm._task, timeout=10)
        self.assertEqual(called, [])
        self.assertTrue(any('投票未通过' in m for m in sender.messages), sender.messages)

    async def test_threshold_not_met(self):
        sender = FakeSender()
        vm = VoteManager(sender)
        vm.update_viewer_count(10)  # 门槛 5：Y=3 N=0 未达参与门槛
        called = []
        await vm.start_vote(_on_pass(called))
        for uid in (101, 102, 103):
            vm.handle_vote(uid, "Y")
        await asyncio.wait_for(vm._task, timeout=10)
        self.assertEqual(called, [])
        self.assertTrue(any('投票未通过' in m for m in sender.messages), sender.messages)

    async def test_song_ended_after_pass(self):
        """投票通过但歌曲已自然结束：不切歌，仅提示。"""
        sender = FakeSender()
        vm = VoteManager(sender)
        vm.update_viewer_count(0)
        called = []
        await vm.start_vote(lambda: called.append(1), is_still_playing=lambda: False)
        vm.handle_vote(101, "Y")
        await asyncio.wait_for(vm._task, timeout=10)
        self.assertEqual(called, [], '歌曲已结束不应触发切歌回调')
        self.assertTrue(any('原歌曲已播放完毕' in m for m in sender.messages), sender.messages)

    async def test_double_start_ignored(self):
        sender = FakeSender()
        vm = VoteManager(sender)
        vm.update_viewer_count(0)
        await vm.start_vote(_noop)
        first_task = vm._task
        await vm.start_vote(_noop)
        self.assertIs(vm._task, first_task, '重复发起应被忽略')
        await asyncio.wait_for(vm._task, timeout=10)

    async def test_messages_shape(self):
        """发起消息带门槛，结束消息带统计。"""
        sender = FakeSender()
        vm = VoteManager(sender)
        vm.update_viewer_count(4)  # 门槛 2
        await vm.start_vote(_noop)
        vm.handle_vote(101, "Y")
        vm.handle_vote(102, "N")
        await asyncio.wait_for(vm._task, timeout=10)
        self.assertIn('当前门槛: 2', sender.messages[0])
        self.assertTrue(any('Y:1 N:1' in m and '总参与:2' in m for m in sender.messages), sender.messages)


if __name__ == '__main__':
    unittest.main(verbosity=2)
