#!/usr/bin/env python3
"""bilibili-api-python 适配器测试：事件转换 + 协议二进制解包 + 接口对齐。

无网络、无真实 WS：直接 dispatch 真实格式事件 / 喂真实协议二进制包。
用法: python3 tests/test_danmaku_biliapi.py
前置: pip install bilibili-api-python
"""
import asyncio
import json
import os
import struct
import sys
import unittest
from unittest import IsolatedAsyncioTestCase

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bilibili_api.live import LiveDanmaku

from bot.danmaku_biliapi import BiliDanmaku
from bot import protocol

# B站 DANMU_MSG 真实数据格式（info[1]=弹幕内容, info[2][0]=uid, info[2][1]=用户名）
DANMU_INFO = [
    [0, 1, 25, 16777215, 1642377825835, 0, 0, "c9f269af", 0, 0, 0, "", 0, "{}", "{}",
     {"extra": "{}"}],
    "点歌 告白气球",
    [34571330, "测试用户", 0, 0, 0, 10000, 1, ""],
    [], [], [], "", 0, 0, "None", {"ts": 1642377825}, 0, 0, "None", "None", 0, 210,
]


def _make_packet(body: bytes, pack_type: int, proto_ver: int = 1) -> bytes:
    """构造 B站 WS 协议包（与 bilibili-api-python 的 __unpack 期望一致）。"""
    return struct.pack(">IHHII", len(body) + 16, 16, proto_ver, pack_type, 1) + body


class Recorder:
    """记录回调参数。"""

    def __init__(self):
        self.messages = []   # (text, uid, username)
        self.view_counts = []

    async def on_message(self, text, uid, username):
        self.messages.append((text, uid, username))

    async def on_viewer_count(self, count):
        self.view_counts.append(count)


class TestEventConversion(IsolatedAsyncioTestCase):
    """dispatch 真实格式事件 -> 适配器回调参数正确。"""

    async def test_danmu_msg(self):
        rec = Recorder()
        adapter = BiliDanmaku(123456, on_message=rec.on_message, on_viewer_count=rec.on_viewer_count)
        dm = LiveDanmaku(123456)  # 不连接，仅用于注册/分发
        adapter._dm = dm
        adapter._register_handlers()
        dm.dispatch("DANMU_MSG", {
            "room_display_id": 123456, "room_real_id": 123456,
            "type": "DANMU_MSG", "data": {"cmd": "DANMU_MSG", "info": DANMU_INFO},
        })
        await asyncio.sleep(0.05)
        self.assertEqual(rec.messages, [("点歌 告白气球", 34571330, "测试用户")])

    async def test_super_chat(self):
        rec = Recorder()
        adapter = BiliDanmaku(123456, on_message=rec.on_message)
        dm = LiveDanmaku(123456)
        adapter._dm = dm
        adapter._register_handlers()
        dm.dispatch("SUPER_CHAT_MESSAGE", {
            "room_display_id": 123456, "room_real_id": 123456,
            "type": "SUPER_CHAT_MESSAGE",
            "data": {"cmd": "SUPER_CHAT_MESSAGE", "data": {
                "message": "SC点歌 稻香", "user_info": {"uid": 999, "uname": "SC用户"},
            }},
        })
        await asyncio.sleep(0.05)
        self.assertEqual(rec.messages, [("SC点歌 稻香", 999, "SC用户")])

    async def test_view(self):
        rec = Recorder()
        adapter = BiliDanmaku(123456, on_viewer_count=rec.on_viewer_count)
        dm = LiveDanmaku(123456)
        adapter._dm = dm
        adapter._register_handlers()
        dm.dispatch("VIEW", {"type": "VIEW", "data": 4321})
        await asyncio.sleep(0.05)
        self.assertEqual(rec.view_counts, [4321])


class TestProtocolUnpack(IsolatedAsyncioTestCase):
    """喂真实协议二进制 -> 库解包 -> 适配器回调（验证事件格式理解与库一致）。"""

    async def test_auth_heartbeat_danmu(self):
        rec = Recorder()
        adapter = BiliDanmaku(123456, on_message=rec.on_message, on_viewer_count=rec.on_viewer_count)
        dm = LiveDanmaku(123456)
        adapter._dm = dm
        adapter._register_handlers()

        # 1. 认证成功反馈（proto_ver=1, pack_type=8）
        await dm._LiveDanmaku__handle_data(_make_packet(json.dumps({"code": 0}).encode(), 8))
        # 2. 心跳回复（proto_ver=1, pack_type=3）：前 4 字节大端 = 人气值
        await dm._LiveDanmaku__handle_data(_make_packet(struct.pack(">I", 5678), 3))
        # 3. 弹幕（proto_ver=0, pack_type=5, JSON body）
        notice = json.dumps({"cmd": "DANMU_MSG", "info": DANMU_INFO}).encode()
        await dm._LiveDanmaku__handle_data(_make_packet(notice, 5, proto_ver=0))
        await asyncio.sleep(0.05)

        self.assertEqual(rec.view_counts, [5678], '心跳回复人气值应经 VIEW 事件到达')
        self.assertEqual(rec.messages, [("点歌 告白气球", 34571330, "测试用户")])

    async def test_danmu_msg_cmd_with_suffix(self):
        """B站新版 cmd 带后缀（DANMU_MSG:4:0:2:2:2:0），库会归一化——验证我们吃到的仍是弹幕。"""
        rec = Recorder()
        adapter = BiliDanmaku(123456, on_message=rec.on_message)
        dm = LiveDanmaku(123456)
        adapter._dm = dm
        adapter._register_handlers()
        notice = json.dumps({"cmd": "DANMU_MSG:4:0:2:2:2:0", "info": DANMU_INFO}).encode()
        await dm._LiveDanmaku__handle_data(_make_packet(notice, 5, proto_ver=0))
        await asyncio.sleep(0.05)
        self.assertEqual(rec.messages, [("点歌 告白气球", 34571330, "测试用户")])


class TestInterfaceAlignment(unittest.TestCase):
    """与手写版 BiliDanmaku 接口对齐（core.py 切换 import 即可用）。"""

    def test_attributes_and_methods(self):
        adapter = BiliDanmaku(123456)
        self.assertEqual(adapter.room_id, 123456)
        self.assertIsNone(adapter.on_message)
        self.assertIsNone(adapter.on_viewer_count)
        self.assertIsNone(adapter.ws)
        for m in ("start", "stop"):
            self.assertTrue(asyncio.iscoroutinefunction(getattr(adapter, m)), f'{m} 应为 async')

    def test_credential_follows_protocol(self):
        """credential 从 protocol 实时取 Cookie（热更新语义）。"""
        old = protocol.SESSDATA
        try:
            protocol.SESSDATA = "hot_reloaded_sessdata"
            adapter = BiliDanmaku(123456)
            cred = adapter._build_credential()
            self.assertEqual(cred.sessdata, "hot_reloaded_sessdata")
        finally:
            protocol.SESSDATA = old


if __name__ == '__main__':
    unittest.main(verbosity=2)
