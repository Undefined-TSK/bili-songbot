#!/usr/bin/env python3
"""S1 适配器本地全链路测试：假 WS 服务器 + patch 掉 HTTP 探测，无任何真实网络。

覆盖：connect -> 认证 -> 心跳(VIEW) -> DANMU_MSG -> stop 全流程，
验证 bilibili-api-python 适配器与手写版行为对齐。
用法: python3 tests/test_danmaku_e2e_local.py
前置: pip install bilibili-api-python websockets
"""
import asyncio
import json
import os
import struct
import sys
import threading
import unittest
from unittest import IsolatedAsyncioTestCase

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import aiohttp
import websockets

import bilibili_api.live as live_mod
from bilibili_api.clients.AioHTTPClient import AioHTTPClient
from bilibili_api.live import LiveDanmaku

from bot import protocol
from bot.danmaku_biliapi import BiliDanmaku

DANMU_INFO = [
    [0, 1, 25, 16777215, 1642377825835, 0, 0, "c9f269af", 0, 0, 0, "", 0, "{}", "{}",
     {"extra": "{}"}],
    "全链路测试弹幕",
    [34571330, "全链路用户", 0, 0, 0, 10000, 1, ""],
    [], [], [], "", 0, 0, "None", {"ts": 1642377825}, 0, 0, "None", "None", 0, 210,
]


def _pack(body: bytes, op: int, protover: int = 1) -> bytes:
    return struct.pack(">IHHII", len(body) + 16, 16, protover, op, 1) + body


class FakeLiveServer:
    """假 B站直播弹幕服务器：期待认证包，回认证成功/心跳回复，主动发一条弹幕。"""

    def __init__(self, port: int):
        self.port = port
        self.auth_received = False
        self.heartbeat_received = False
        self.sent_danmu = False
        self._ready = threading.Event()
        self.start_error = None

    async def handler(self, ws):
        async for raw in ws:
            if len(raw) < 16:
                continue
            op = struct.unpack(">I", raw[8:12])[0]
            if op == 7:  # 认证包
                self.auth_received = True
                await ws.send(_pack(json.dumps({"code": 0}).encode(), 8))
            elif op == 2:  # 心跳
                self.heartbeat_received = True
                await ws.send(_pack(struct.pack(">I", 4242), 3))  # 人气值
                if not self.sent_danmu:
                    self.sent_danmu = True
                    await ws.send(_pack(json.dumps(
                        {"cmd": "DANMU_MSG", "info": DANMU_INFO}).encode(), 5, protover=0))

    def start(self):
        loop = asyncio.new_event_loop()

        async def serve():
            try:
                self._server = await websockets.serve(self.handler, "127.0.0.1", self.port)
            except Exception as e:
                # bind 失败（如端口被抢）时记录并放行，由 wait_ready 断言暴露
                self.start_error = e
                self._ready.set()
                return
            self._ready.set()
            await asyncio.Future()  # 永久运行

        self._thread = threading.Thread(target=loop.run_until_complete, args=(serve(),), daemon=True)
        self._thread.start()
        return loop

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """等待 serve bind 完成（消除 connect 与 bind 的竞态）。"""
        return self._ready.wait(timeout)

    def stop(self):
        if hasattr(self, "_server"):
            self._server.close()


class _LocalWsClient(AioHTTPClient):
    """把 wss:// 改写为本地 ws://（自签/无 TLS 的假服务器）。"""

    async def ws_create(self, url: str = "", params: dict = {}, headers: dict = {}) -> int:
        url = url.replace("wss://", "ws://").replace(":443/", f":{_TEST_PORT}/")
        self._AioHTTPClient__ws_cnt += 1
        cnt = self._AioHTTPClient__ws_cnt
        self._AioHTTPClient__wss[cnt] = await self.get_wrapped_session().ws_connect(url)
        return cnt


_TEST_PORT = 0  # 由 setUp 分配真实端口后写入


class TestDanmakuE2ELocal(IsolatedAsyncioTestCase):
    """本地全链路：认证 -> VIEW -> DANMU_MSG -> stop。"""

    async def asyncSetUp(self):
        global _TEST_PORT
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        _TEST_PORT = sock.getsockname()[1]
        sock.close()

        # 匿名身份：假 sessdata 会触发库的 get_self_info 重试（真实请求 B站），
        # 预置 buvid3 避免 get_buvid 真实请求——两者都与本测试无关
        self._old_sessdata = protocol.SESSDATA
        protocol.SESSDATA = ""

        self.server = FakeLiveServer(_TEST_PORT)
        self.server.start()
        self.assertTrue(self.server.wait_ready(5.0),
                        f"假 WS 服务器未在 5s 内就绪: {self.server.start_error}")
        self.messages = []
        self.view_counts = []
        self.session = aiohttp.ClientSession()

        # patch HTTP 探测：房间号 + 弹幕服务器配置
        async def fake_get_room_id(self):
            return 999

        async def fake_get_danmu_info(self):
            return {"host_list": [{"host": "127.0.0.1", "wss_port": _TEST_PORT}], "token": "fake-token"}

        self._patches = [
            (live_mod.LiveRoom, "get_room_id", fake_get_room_id),
            (live_mod.LiveRoom, "get_danmu_info", fake_get_danmu_info),
            (live_mod, "get_client", lambda: _LocalWsClient(session=self.session)),
        ]
        for obj, name, val in self._patches:
            setattr(obj, name, val)

    async def asyncTearDown(self):
        for obj, name, _ in self._patches:
            delattr(obj, name)  # 恢复被 patch 的实例/类属性
        protocol.SESSDATA = self._old_sessdata
        self.server.stop()
        await self.session.close()

    async def test_full_chain(self):
        # 契约与手写版一致：回调为 async（core.py 传 async 方法）
        async def _on_message(text, uid, username):
            self.messages.append((text, uid, username))

        async def _on_viewer_count(count):
            self.view_counts.append(count)

        adapter = BiliDanmaku(
            123456,
            on_message=_on_message,
            on_viewer_count=_on_viewer_count,
            session=self.session,
        )
        # start() 内部是阻塞重连循环，放后台任务
        task = asyncio.create_task(adapter.start())
        # 等待认证+心跳+弹幕到达
        for _ in range(100):
            await asyncio.sleep(0.1)
            if self.messages and self.view_counts:
                break
        await adapter.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        self.assertTrue(self.server.auth_received, "假服务器应收到认证包")
        self.assertEqual(self.view_counts, [4242], "人气值应经 VIEW 事件到达")
        self.assertEqual(self.messages, [("全链路测试弹幕", 34571330, "全链路用户")])


if __name__ == '__main__':
    unittest.main(verbosity=2)
