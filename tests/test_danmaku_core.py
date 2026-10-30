#!/usr/bin/env python3
"""弹幕发送层（bot/danmaku.py）单元测试：字数截断、频控、10030 退避、Cookie 失效。

弹幕监听已切换到 bilibili-api-python（见 tests/test_danmaku_biliapi.py 与
tests/test_danmaku_e2e_local.py），本文件只覆盖发送侧 BiliSender。
用法: python3 tests/test_danmaku_core.py
"""
import asyncio
import os
import sys
import time
import unittest
from collections import deque
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import bot.danmaku as danmaku
from bot import protocol
from bot.danmaku import BiliSender, _truncate_danmaku


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class _FakeCtx:
    """模拟 aiohttp 的 async with session.get/post(...) 返回对象。"""

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return _FakeResp(self._payload)

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """按调用顺序依次返回预设响应；记录 post 的请求体。"""

    def __init__(self, get_payloads=None, post_payloads=None):
        self._gets = list(get_payloads or [])
        self._posts = list(post_payloads or [])
        self.posted = []

    def get(self, url, **kwargs):
        payload = self._gets.pop(0) if self._gets else {"code": -1}
        if isinstance(payload, Exception):
            raise payload
        return _FakeCtx(payload)

    def post(self, url, data=None, **kwargs):
        self.posted.append(dict(data or {}))
        payload = self._posts.pop(0) if self._posts else {"code": 0}
        if isinstance(payload, Exception):
            raise payload
        return _FakeCtx(payload)

    async def close(self):
        pass


class _FakeCookie:
    def __init__(self, valid=True, can_reload=False):
        self.valid = valid
        self._can_reload = can_reload
        self.marked = []

    def check_hot_reload(self):
        return self._can_reload

    def reload_config(self):
        self.valid = True

    def mark_invalid(self, reason):
        self.valid = False
        self.marked.append(reason)


class TestTruncateDanmaku(unittest.TestCase):

    def test_ascii_counts_one_unit(self):
        self.assertEqual(_truncate_danmaku("abcdefghij", 10), "abcdefghij")

    def test_cjk_counts_two_units(self):
        # 每个汉字权重 2；上限 4 只能容纳 2 个
        self.assertEqual(_truncate_danmaku("点歌机器", 4), "点歌")

    def test_mixed_units_boundary(self):
        # a(1) + 歌(2) + b(1) = 4，正好；再加 曲(2) 超限
        self.assertEqual(_truncate_danmaku("a歌b曲", 4), "a歌b")

    def test_empty_and_zero_limit(self):
        self.assertEqual(_truncate_danmaku("", 20), "")
        self.assertEqual(_truncate_danmaku("歌", 0), "")

    def test_no_truncation_needed(self):
        self.assertEqual(_truncate_danmaku("短", 40), "短")


class TestBiliSender(IsolatedAsyncioTestCase):

    def _sender(self, session, cookie=None):
        sender = BiliSender(session, cookie_mgr=cookie)
        sender._last_send = 0.0  # 远早于当前时间，跳过发送间隔等待
        return sender

    async def test_success_posts_and_records_timestamp(self):
        session = _FakeSession(post_payloads=[{"code": 0}])
        sender = self._sender(session)
        with patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            await sender.send("点歌成功")
        self.assertEqual(session.posted[0]["msg"], "点歌成功")
        self.assertEqual(len(sender._msg_timestamps), 1)
        self.assertEqual(sender._consecutive_10030, 0)

    async def test_message_truncated_before_posting(self):
        session = _FakeSession(post_payloads=[{"code": 0}])
        sender = self._sender(session)
        long_msg = "一" * 30  # 60 单位
        with patch.object(danmaku, "DANMAKU_MAX_LEN", 20), \
                patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            await sender.send(long_msg)
        self.assertEqual(session.posted[0]["msg"], "一" * 10)

    async def test_empty_after_truncation_skips_post(self):
        session = _FakeSession()
        sender = self._sender(session)
        with patch.object(danmaku, "DANMAKU_MAX_LEN", 0), \
                patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            await sender.send("任意内容")
        self.assertEqual(session.posted, [])

    async def test_cookie_invalid_without_reload_skips_post(self):
        session = _FakeSession()
        cookie = _FakeCookie(valid=False)
        sender = self._sender(session, cookie)
        await sender.send("不该发送")
        self.assertEqual(session.posted, [])

    async def test_cookie_invalid_with_hot_reload_proceeds(self):
        session = _FakeSession(post_payloads=[{"code": 0}])
        cookie = _FakeCookie(valid=False, can_reload=True)
        sender = self._sender(session, cookie)
        with patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            await sender.send("热更新后发送")
        self.assertEqual(session.posted[0]["msg"], "热更新后发送")

    async def test_cookie_error_code_marks_invalid_and_no_retry(self):
        session = _FakeSession(post_payloads=[{"code": -101, "message": "账号未登录"}])
        cookie = _FakeCookie()
        sender = self._sender(session, cookie)
        with patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            await sender.send("鉴权失败")
        self.assertEqual(len(session.posted), 1, "Cookie 失效不应重试")
        self.assertEqual(len(cookie.marked), 1)
        self.assertIn("code=-101", cookie.marked[0])

    async def test_rate_limit_10030_retries_then_succeeds(self):
        session = _FakeSession(post_payloads=[{"code": 10030}, {"code": 0}])
        sender = self._sender(session)
        with patch.object(protocol._api_limiter, "acquire", AsyncMock()), \
                patch.object(asyncio, "sleep", AsyncMock()) as fake_sleep:
            await sender.send("退避重试")
        self.assertEqual(len(session.posted), 2)
        self.assertEqual(sender._consecutive_10030, 0, "成功后应清零连续计数")
        fake_sleep.assert_awaited_once_with(5)

    async def test_10030_exhausted_drops_message_and_caps_counter(self):
        session = _FakeSession(post_payloads=[{"code": 10030}] * 3)
        sender = self._sender(session)
        with patch.object(protocol._api_limiter, "acquire", AsyncMock()), \
                patch.object(asyncio, "sleep", AsyncMock()):
            await sender.send("三次都失败")
        self.assertEqual(len(session.posted), 3)
        self.assertEqual(sender._consecutive_10030, 3, "计数上限为 3，避免间隔无限增长")
        self.assertEqual(len(sender._msg_timestamps), 0, "失败消息不计入频控窗口")

    async def test_unknown_error_code_returns_without_retry(self):
        session = _FakeSession(post_payloads=[{"code": 12345, "message": "未知"}])
        sender = self._sender(session)
        with patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            await sender.send("未知错误")
        self.assertEqual(len(session.posted), 1)

    async def test_post_exception_is_caught(self):
        session = _FakeSession(post_payloads=[RuntimeError("network down")])
        sender = self._sender(session)
        with patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            await sender.send("网络异常")
        self.assertEqual(len(sender._msg_timestamps), 0)

    async def test_minute_limit_waits_before_sending(self):
        session = _FakeSession(post_payloads=[{"code": 0}])
        sender = self._sender(session)
        now = time.time()
        sender._msg_timestamps = deque([now - 1] * 20)
        with patch.object(danmaku, "MAX_MSG_PER_MINUTE", 20), \
                patch.object(protocol._api_limiter, "acquire", AsyncMock()), \
                patch.object(asyncio, "sleep", AsyncMock()) as fake_sleep:
            await sender.send("限流后仍要发")
        self.assertEqual(len(session.posted), 1, "达上限应等待而非丢弃该条消息")
        self.assertTrue(fake_sleep.await_count >= 1)
        wait = fake_sleep.await_args_list[0].args[0]
        self.assertAlmostEqual(wait, 59.0, delta=1.0)

    async def test_send_interval_pacing(self):
        session = _FakeSession(post_payloads=[{"code": 0}])
        sender = self._sender(session)
        sender._last_send = time.time() - 4  # 间隔 5s，还差约 1s
        with patch.object(danmaku, "DANMAKU_INTERVAL", 5), \
                patch.object(protocol._api_limiter, "acquire", AsyncMock()), \
                patch.object(asyncio, "sleep", AsyncMock()) as fake_sleep:
            await sender.send("间隔控制")
        self.assertEqual(len(session.posted), 1)
        self.assertAlmostEqual(fake_sleep.await_args_list[0].args[0], 1.0, delta=1.0)

    def test_prune_timestamps_removes_old_entries(self):
        sender = BiliSender(_FakeSession())
        now = time.time()
        sender._msg_timestamps = deque([now - 120, now - 30, now - 1])
        sender._prune_timestamps()
        self.assertEqual(len(sender._msg_timestamps), 2, "只应清理超过 60s 的记录")


if __name__ == '__main__':
    unittest.main(verbosity=2)
