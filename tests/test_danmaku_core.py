#!/usr/bin/env python3
"""手写弹幕层（bot/danmaku.py）单元测试：数据包编解码、三级 connect 回退、发送端频控/退避。

全程无真实网络：HTTP 与 WebSocket 均以假对象替代。
用法: python3 tests/test_danmaku_core.py
"""
import asyncio
import json
import os
import struct
import sys
import time
import unittest
import zlib
from collections import deque
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import bot.danmaku as danmaku
from bot import protocol
from bot.danmaku import BiliDanmaku, BiliSender, _truncate_danmaku


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


def _danmu_msg(text, uid=1001, uname="观众甲"):
    return {
        "cmd": "DANMU_MSG",
        "info": [[0, 1, 25], text, [uid, uname, 0, 0, 0, 0, 1, ""]],
    }


def _pack(body: bytes, op: int, protover: int = 1) -> bytes:
    return BiliDanmaku._make_packet(body, op, protover)


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


class TestMakePacket(unittest.TestCase):

    def test_header_layout(self):
        pkt = BiliDanmaku._make_packet(b"", 2)
        self.assertEqual(struct.unpack(">I", pkt[0:4])[0], 16, "空包总长应为 16")
        self.assertEqual(struct.unpack(">H", pkt[4:6])[0], 16, "header 固定 16")
        self.assertEqual(struct.unpack(">H", pkt[6:8])[0], 1, "默认 protover=1")
        self.assertEqual(struct.unpack(">I", pkt[8:12])[0], 2)
        self.assertEqual(struct.unpack(">I", pkt[12:16])[0], 1, "序列号固定 1")

    def test_protover_override_and_body(self):
        body = b"hello"
        pkt = BiliDanmaku._make_packet(body, 7, protover=2)
        self.assertEqual(struct.unpack(">I", pkt[0:4])[0], 16 + len(body))
        self.assertEqual(struct.unpack(">H", pkt[6:8])[0], 2)
        self.assertEqual(pkt[16:], body)


class TestHandlePacket(IsolatedAsyncioTestCase):

    async def test_short_packet_ignored(self):
        got = []

        async def on_message(text, uid, uname):
            got.append((text, uid, uname))

        dm = BiliDanmaku(1, on_message=on_message)
        await dm._handle_packet(None, b"\x00" * 10)
        self.assertEqual(got, [])

    async def test_auth_reply_code_zero_and_error(self):
        dm = BiliDanmaku(1)
        await dm._handle_packet(None, _pack(json.dumps({"code": 0}).encode(), 8))
        # 认证失败分支只记录日志，不应抛异常
        await dm._handle_packet(None, _pack(json.dumps({"code": -1}).encode(), 8))

    async def test_viewer_count_callback(self):
        seen = []

        async def on_viewer(n):
            seen.append(n)

        dm = BiliDanmaku(1, on_viewer_count=on_viewer)
        await dm._handle_packet(None, _pack(struct.pack(">I", 8888), 3))
        self.assertEqual(seen, [8888])

    async def test_viewer_count_empty_body_is_zero(self):
        seen = []

        async def on_viewer(n):
            seen.append(n)

        dm = BiliDanmaku(1, on_viewer_count=on_viewer)
        await dm._handle_packet(None, _pack(b"", 3))
        self.assertEqual(seen, [0])

    async def test_viewer_count_without_callback(self):
        dm = BiliDanmaku(1)
        await dm._handle_packet(None, _pack(struct.pack(">I", 1), 3))  # 不炸

    async def test_plain_danmu_msg_protover1(self):
        got = []

        async def on_message(text, uid, uname):
            got.append((text, uid, uname))

        dm = BiliDanmaku(1, on_message=on_message)
        await dm._handle_packet(None, _pack(json.dumps(_danmu_msg("点歌 稻香")).encode(), 5, protover=1))
        self.assertEqual(got, [("点歌 稻香", 1001, "观众甲")])

    async def test_compressed_multi_packet(self):
        got = []

        async def on_message(text, uid, uname):
            got.append(text)

        dm = BiliDanmaku(1, on_message=on_message)
        inner = (_pack(json.dumps(_danmu_msg("第一首")).encode(), 5, protover=0)
                 + _pack(json.dumps(_danmu_msg("第二首")).encode(), 5, protover=0))
        await dm._handle_packet(None, _pack(zlib.compress(inner), 5, protover=2))
        self.assertEqual(got, ["第一首", "第二首"])

    async def test_corrupt_compressed_payload_no_raise(self):
        dm = BiliDanmaku(1)
        await dm._handle_packet(None, _pack(b"not-zlib-data", 5, protover=2))

    async def test_heartbeat_op_ignored(self):
        dm = BiliDanmaku(1, on_message=AsyncMock())
        await dm._handle_packet(None, _pack(b"", 2))
        dm.on_message.assert_not_called()


class TestProcessMessage(IsolatedAsyncioTestCase):

    async def test_danmu_msg(self):
        got = []

        async def on_message(text, uid, uname):
            got.append((text, uid, uname))

        dm = BiliDanmaku(1, on_message=on_message)
        await dm._process_message(json.dumps(_danmu_msg("hello", 42, "张三")).encode())
        self.assertEqual(got, [("hello", 42, "张三")])

    async def test_danmu_msg_too_short_info_ignored(self):
        dm = BiliDanmaku(1, on_message=AsyncMock())
        await dm._process_message(json.dumps({"cmd": "DANMU_MSG", "info": ["只", "两"]}).encode())
        dm.on_message.assert_not_called()

    async def test_super_chat_message(self):
        got = []

        async def on_message(text, uid, uname):
            got.append((text, uid, uname))

        dm = BiliDanmaku(1, on_message=on_message)
        msg = {"cmd": "SUPER_CHAT_MESSAGE",
               "data": {"message": "上舰", "user_info": {"uid": 7, "uname": "舰长"}}}
        await dm._process_message(json.dumps(msg).encode())
        self.assertEqual(got, [("上舰", 7, "舰长")])

    async def test_super_chat_empty_text_ignored(self):
        dm = BiliDanmaku(1, on_message=AsyncMock())
        msg = {"cmd": "SUPER_CHAT_MESSAGE", "data": {"message": "", "user_info": {"uid": 7}}}
        await dm._process_message(json.dumps(msg).encode())
        dm.on_message.assert_not_called()

    async def test_unknown_cmd_and_bad_json_ignored(self):
        dm = BiliDanmaku(1, on_message=AsyncMock())
        await dm._process_message(json.dumps({"cmd": "SEND_GIFT"}).encode())
        await dm._process_message(b"{not json")
        dm.on_message.assert_not_called()

    async def test_without_callback_does_not_raise(self):
        dm = BiliDanmaku(1)
        await dm._process_message(json.dumps(_danmu_msg("x")).encode())


class TestConnect(IsolatedAsyncioTestCase):

    def _patch_wbi(self, keys=("i" * 32, "s" * 32)):
        # WBI 混入表按 64 字符密钥索引，真实 img/sub key 各 32 位
        async def fake_keys(session):
            return keys

        return patch.object(danmaku, "get_wbi_keys", fake_keys)

    async def test_tier1_wbi_prefers_443_host(self):
        payload = {"code": 0, "data": {"token": "tok",
                   "host_list": [{"host": "a.example", "wss_port": 80},
                                 {"host": "b.example", "wss_port": 443}]}}
        session = _FakeSession(get_payloads=[payload])
        dm = BiliDanmaku(1, session=session)
        with self._patch_wbi():
            url, token = await dm.connect()
        self.assertEqual(url, "wss://b.example:443/sub")
        self.assertEqual(token, "tok")

    async def test_tier1_forces_port_443_when_no_443_host(self):
        payload = {"code": 0, "data": {"token": "t",
                   "host_list": [{"host": "only.example", "wss_port": 8080}]}}
        session = _FakeSession(get_payloads=[payload])
        dm = BiliDanmaku(1, session=session)
        with self._patch_wbi():
            url, token = await dm.connect()
        self.assertEqual(url, "wss://only.example:443/sub")

    async def test_tier1_failure_falls_back_to_old_conf(self):
        old = {"code": 0, "data": {"token": "t2", "host": "legacy.example"}}
        session = _FakeSession(get_payloads=[{"code": -352}, old])
        dm = BiliDanmaku(1, session=session)
        with self._patch_wbi(), patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            url, token = await dm.connect()
        self.assertEqual(url, "wss://legacy.example:443/sub")
        self.assertEqual(token, "t2")

    async def test_missing_wbi_keys_goes_to_tier2(self):
        old = {"code": 0, "data": {"token": "t2", "host": "legacy.example"}}
        session = _FakeSession(get_payloads=[old])
        dm = BiliDanmaku(1, session=session)
        with self._patch_wbi(keys=(None, None)), patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            url, token = await dm.connect()
        self.assertEqual(url, "wss://legacy.example:443/sub")

    async def test_all_tiers_fail_uses_hardcoded_fallback(self):
        session = _FakeSession(get_payloads=[{"code": -1}, {"code": -2}])
        dm = BiliDanmaku(1, session=session)
        with self._patch_wbi(), patch.object(protocol._api_limiter, "acquire", AsyncMock()):
            url, token = await dm.connect()
        self.assertEqual(url, "wss://hw-bj-live-chat.bilibili.com:443/sub")
        self.assertEqual(token, "")

    async def test_exceptions_in_http_total_fallback(self):
        session = _FakeSession(get_payloads=[RuntimeError("boom"), RuntimeError("boom2")])
        dm = BiliDanmaku(1, session=session)
        with self._patch_wbi():
            url, token = await dm.connect()
        self.assertEqual(token, "")
        self.assertTrue(url.startswith("wss://"), "异常时必须回退到硬编码地址而非抛出")


class TestStop(IsolatedAsyncioTestCase):

    async def test_stop_cancels_heartbeat_and_closes_ws(self):
        closed = []

        class _Ws:
            async def close(self):
                closed.append(True)

        dm = BiliDanmaku(1)
        dm.ws = _Ws()
        task = asyncio.create_task(asyncio.sleep(3600))
        dm._heartbeat_task = task
        await dm.stop()
        self.assertFalse(dm._running)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())
        self.assertEqual(closed, [True])

    async def test_stop_without_resources_is_safe(self):
        dm = BiliDanmaku(1)
        await dm.stop()


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
