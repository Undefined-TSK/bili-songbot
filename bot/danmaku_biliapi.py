"""弹幕监听（bilibili-api-python 实现）：bot 主路径使用的弹幕客户端。

S1 已完成：用 bilibili-api-python 的 LiveDanmaku 替换了原手写 WebSocket 协议栈
（连接/认证/心跳/重连/WBI签名/buvid 获取，约 230 行），core.py 与 bot/__init__.py
均已导入本模块的 BiliDanmaku；手写协议与 protocol.WBI 已删除。

行为对齐（相对原手写版）：
- DANMU_MSG        -> on_message(弹幕内容, uid, 用户名)
- SUPER_CHAT_MESSAGE -> on_message(留言内容, uid, 用户名)   # 手写版即支持 SC 点歌/投票，保留
- VIEW             -> on_viewer_count(人气值)               # 手写版用心跳回复前4字节，同源
- connect() 阻塞式重连循环 -> start() 内 await，语义与手写版 start() 一致
- disconnect() 优雅关闭   -> stop()

依赖：pip install bilibili-api-python（含 aiohttp/brotli/pure-protobuf）
"""
from typing import Callable, Optional

import aiohttp
from bilibili_api import Credential
from bilibili_api.live import LiveDanmaku
from bilibili_api.utils.network import get_client, select_client, set_session

from . import protocol
from .config import log


class BiliDanmaku:
    """bilibili-api-python 实现的弹幕监听器（与手写版同接口）。"""

    def __init__(self, room_id: int, on_message: Optional[Callable] = None,
                 on_viewer_count: Optional[Callable] = None,
                 session: aiohttp.ClientSession = None):
        self.room_id = room_id
        self.on_message = on_message
        self.on_viewer_count = on_viewer_count
        self.session = session
        self.ws = None  # 兼容属性：手写版持有 websockets 连接，此处置 None
        self._dm: Optional[LiveDanmaku] = None
        # 门控只防 stop() 之后的迟到事件：handler 能收到事件即说明连接已建立，
        # 不需要等 start() 置位（connect 是阻塞循环，事件只在其内部 dispatch）
        self._running = True

    def _build_credential(self) -> Credential:
        """从 protocol 模块实时取 Cookie 配置（热更新后新建/更新均生效）。"""
        return Credential(
            sessdata=protocol.SESSDATA,
            bili_jct=protocol.BILI_JCT,
            buvid3=protocol.BILI_BUVID3_CFG,
            buvid4=protocol.BILI_BUVID4_CFG,
        )

    def _register_handlers(self):
        @self._dm.on("DANMU_MSG")
        async def _on_danmu(event: dict):
            if not self._running or not self.on_message:
                return
            info = event["data"]["info"]
            if len(info) >= 3:
                await self.on_message(info[1], info[2][0], info[2][1])

        @self._dm.on("SUPER_CHAT_MESSAGE")
        async def _on_super_chat(event: dict):
            if not self._running or not self.on_message:
                return
            data = event["data"]["data"]
            text = data.get("message", "")
            user_info = data.get("user_info", {})
            if text:
                await self.on_message(text, user_info.get("uid", 0), user_info.get("uname", ""))

        @self._dm.on("VIEW")
        async def _on_view(event: dict):
            if self.on_viewer_count:
                # 契约与手写版一致：回调为 async（core.py 传 async 方法），必须 await
                await self.on_viewer_count(event["data"])

    async def start(self):
        """阻塞式监听循环（内部断线自动重连），与手写版 start() 语义一致。"""
        self._running = True
        if self.session is not None:
            # 复用我们的 session：cookie 热更新写回 session 后直通生效。
            # 注意库的 set_session 要求 pool 已初始化：先 select + get_client 再注入
            # （不 select 的话默认可能是 curl_cffi/httpx，注入 aiohttp session 会错位）
            select_client("aiohttp")
            get_client()
            set_session(self.session)
        self._dm = LiveDanmaku(self.room_id, credential=self._build_credential())
        self._register_handlers()
        log.info(f"弹幕监听启动（bilibili-api-python）: 房间号 {self.room_id}")
        await self._dm.connect()

    async def stop(self):
        self._running = False
        if self._dm is not None:
            try:
                await self._dm.disconnect()
            except Exception as e:
                log.debug(f"断开弹幕连接异常（忽略）: {e}")
