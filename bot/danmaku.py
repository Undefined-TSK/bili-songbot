"""弹幕发送层：以主播身份发送弹幕（BiliSender，含 10030 退避对抗）。

弹幕监听已切换到 bilibili-api-python 实现（连接/认证/心跳/重连/WBI 均由库处理），
见 bot/danmaku_biliapi.py；本模块只保留发送侧，它带 B 站特有的每分钟上限、
发送间隔与 10030 指数退避，是项目的差异化能力。
"""
import asyncio
import time
from collections import deque

import aiohttp

from . import protocol
from .config import (
    BILI_JCT, DANMAKU_INTERVAL, DANMAKU_MAX_LEN, MAX_MSG_PER_MINUTE, ROOM_ID, log,
)
from .protocol import BILI_SEND_MSG, CookieManager


def _truncate_danmaku(message: str, max_units: int) -> str:
    """按B站规则截断弹幕：中文/全角字符算2单位，ASCII算1单位。
    B站弹幕限制约20个汉字，即40单位。"""
    result = []
    total = 0
    for ch in message:
        weight = 2 if ord(ch) > 127 else 1
        if total + weight > max_units:
            break
        result.append(ch)
        total += weight
    return ''.join(result)


class BiliSender:
    """以主播身份发送弹幕，带 Cookie 失效检测和频率控制。"""

    def __init__(self, session: aiohttp.ClientSession, cookie_mgr: CookieManager = None):
        self.session = session
        self._last_send = 0.0
        self.cookie_mgr = cookie_mgr
        self._lock = asyncio.Lock()
        self._msg_timestamps: deque = deque()  # 最近1分钟内的发送时间戳
        self._consecutive_10030 = 0  # 连续10030计数

    def _prune_timestamps(self):
        """清理超过1分钟的时间戳。"""
        cutoff = time.time() - 60
        while self._msg_timestamps and self._msg_timestamps[0] < cutoff:
            self._msg_timestamps.popleft()

    async def send(self, message: str):
        """发送弹幕，自动控制频率。Cookie 失效时跳过发送并提示。"""
        # B站弹幕字数限制：按权重截断（中文算2，ASCII算1，总计≤DANMAKU_MAX_LEN单位）
        truncated = _truncate_danmaku(message, DANMAKU_MAX_LEN)
        if len(truncated) < len(message):
            log.debug(f"弹幕超长，截断: {message[:30]}...")
        message = truncated
        if not message:
            return
        if self.cookie_mgr and not self.cookie_mgr.valid:
            # 尝试热更新
            if self.cookie_mgr.check_hot_reload():
                self.cookie_mgr.reload_config()
            if not self.cookie_mgr.valid:
                log.warning("Cookie 失效，弹幕发送暂停。请更新 config.json 后自动恢复")
                return

        async with self._lock:
            # 1. 检查每分钟发送上限
            self._prune_timestamps()
            if len(self._msg_timestamps) >= MAX_MSG_PER_MINUTE:
                wait = 60 - (time.time() - self._msg_timestamps[0])
                if wait > 0:
                    log.warning(f"弹幕发送达每分钟上限({MAX_MSG_PER_MINUTE}条)，等待{wait:.0f}秒...")
                    await asyncio.sleep(wait)
                self._prune_timestamps()

            # 2. 检查发送间隔
            now = time.time()
            elapsed = now - self._last_send
            # 如果之前触发过10030，动态增加间隔
            effective_interval = DANMAKU_INTERVAL + (self._consecutive_10030 * 2)
            if elapsed < effective_interval:
                await asyncio.sleep(effective_interval - elapsed)
            self._last_send = time.time()

            data = {
                "bubble": 0,
                "msg": message,
                "color": 16777215,
                "mode": 1,
                "fontsize": 25,
                "rnd": int(time.time()),
                "roomid": ROOM_ID,
                "csrf_token": BILI_JCT,
                "csrf": BILI_JCT,
            }

            # 3. 发送，带10030重试
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await protocol._api_limiter.acquire()
                    async with self.session.post(BILI_SEND_MSG, data=data, headers=protocol.BILI_HEADERS) as resp:
                        result = await resp.json()
                        code = result.get("code", 0)
                        if code == 0:
                            self._consecutive_10030 = 0
                            self._msg_timestamps.append(time.time())
                            log.info(f"弹幕发送: {message}")
                            return
                        elif code in (-101, -111, -400):
                            # Cookie 失效或 CSRF 错误
                            reason = result.get("message", result.get("msg", f"code={code}"))
                            log.error(f"Cookie 失效 (code={code}): {reason}")
                            if self.cookie_mgr:
                                self.cookie_mgr.mark_invalid(f"code={code} {reason}")
                            return
                        elif code == 10030:
                            # 频率过快，不是 Cookie 失效，等待后重试
                            self._consecutive_10030 += 1
                            wait_time = min(5 * self._consecutive_10030, 15)
                            log.info(
                                f"弹幕发送过快 (code=10030), "
                                f"第{self._consecutive_10030}次连续触发, "
                                f"等待{wait_time}秒后重试({attempt+1}/{max_retries})..."
                            )
                            await asyncio.sleep(wait_time)
                            self._last_send = time.time()
                            continue  # 重试
                        else:
                            log.warning(f"弹幕发送失败 (code={code}): {result.get('message', result.get('msg', ''))}")
                            return
                except Exception as e:
                    log.error(f"弹幕发送异常: {e}")
                    return
            # 重试耗尽
            log.error(f"弹幕发送失败: 重试{max_retries}次后仍触发10030，消息已丢弃: {message[:50]}")
            self._consecutive_10030 = min(self._consecutive_10030, 3)  # 上限3，避免间隔无限增长
