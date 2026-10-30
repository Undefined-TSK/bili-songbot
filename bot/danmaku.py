"""弹幕交互层：直播间 WebSocket 弹幕监听（BiliDanmaku）与弹幕发送（BiliSender，含 10030 退避对抗）。"""
import asyncio
import json
import struct
import time
import zlib
from typing import Optional

import aiohttp
import websockets

from . import protocol
from collections import deque
from urllib.parse import urlencode

from .config import (
    BILI_BUVID3_CFG, BILI_JCT, DANMAKU_INTERVAL, DANMAKU_MAX_LEN,
    MAX_MSG_PER_MINUTE, ROOM_ID, log,
)
from .protocol import (
    BILI_API_BASE, BILI_DANMAKU_CONF_OLD, BILI_SEND_MSG, CookieManager, FULL_UA, enc_wbi, get_wbi_keys,
)


class BiliDanmaku:
    """B站直播间弹幕 WebSocket 客户端。"""

    def __init__(self, room_id: int, on_message=None, on_viewer_count=None, session: aiohttp.ClientSession = None):
        self.room_id = room_id
        self.on_message = on_message
        self.on_viewer_count = on_viewer_count
        self.session = session
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False

    async def connect(self):
        """获取弹幕服务器信息，返回 (wss_url, token)。三级回退策略。"""
        session = self.session or aiohttp.ClientSession()
        try:
            # 方案1: 新接口 getDanmuInfo（需 WBI 签名 + buvid3）
            try:
                img_key, sub_key = await get_wbi_keys(session)
                if img_key and sub_key:
                    # 注意：参数名是 id（不是 room_id），值必须是真实房间号
                    params = enc_wbi({"id": protocol.REAL_ROOM_ID, "type": 0}, img_key, sub_key)
                    query_str = urlencode(params)
                    url = f"{BILI_API_BASE}/xlive/web-room/v1/index/getDanmuInfo?{query_str}"
                    log.debug(f"getDanmuInfo 请求: {url}")
                    async with session.get(url, headers=protocol.BILI_HEADERS, timeout=10) as resp:
                        data = await resp.json()
                        if data.get("code") == 0:
                            conf = data.get("data", {})
                            token = conf.get("token", "")
                            host_list = conf.get("host_list", [])
                            if host_list:
                                # 优先选 wss_port=443 的主机，否则用第一个主机但强制端口 443
                                chosen = None
                                for h in host_list:
                                    if h.get('wss_port', 0) == 443:
                                        chosen = h
                                        break
                                if not chosen:
                                    chosen = host_list[0]
                                wss_url = f"wss://{chosen['host']}:443/sub"
                                log.info(f"getDanmuInfo 成功 (WBI 签名): {chosen['host']}:443")
                                log.info(f"token 长度: {len(token)}, token 前16字符: {token[:16]}...")
                                return wss_url, token
                        log.warning(f"getDanmuInfo 返回 code={data.get('code')}: {data.get('message', '')}")
                else:
                    log.warning("无法获取 WBI keys，跳过 getDanmuInfo")
            except Exception as e:
                log.warning(f"getDanmuInfo 请求失败: {e}")

            # 方案2: 旧接口 getConf（不需要 WBI 签名，但 token 可能无效）
            try:
                await protocol._api_limiter.acquire()
                async with session.get(BILI_DANMAKU_CONF_OLD, headers=protocol.BILI_HEADERS, timeout=10) as resp:
                    data = await resp.json()
                    if data.get("code") == 0:
                            conf = data.get("data", {})
                            token = conf.get("token", "")
                            host = conf.get("host", "")
                            if host:
                                wss_url = f"wss://{host}:443/sub"
                                log.warning(f"getConf 回退 (旧接口): host={host}:443, token={'有' if token else '空'}")
                                return wss_url, token
                    log.warning(f"getConf 返回 code={data.get('code')}")
            except Exception as e:
                log.warning(f"getConf 请求失败: {e}")

            # 方案3: 硬编码弹幕服务器地址（最终回退）
            log.warning("所有弹幕 API 均失败，使用硬编码服务器地址")
            fallback_hosts = [
                "hw-bj-live-chat.bilibili.com",
                "hz-hb-live-chat.bilibili.com",
                "tx-bj-live-chat.bilibili.com",
            ]
            wss_url = f"wss://{fallback_hosts[0]}:443/sub"
            return wss_url, ""
        finally:
            if not self.session:
                await session.close()

    async def start(self):
        self._running = True
        while self._running:
            try:
                wss_url, token = await self.connect()
                log.info(f"连接弹幕服务器: {wss_url}")
                ws_headers = {
                    "User-Agent": FULL_UA,
                    "Origin": "https://live.bilibili.com",
                }
                async with websockets.connect(
                    wss_url,
                    ping_interval=None,
                    close_timeout=5,
                    additional_headers=ws_headers,
                    compression=None,
                ) as ws:
                    self.ws = ws
                    # 发送认证
                    await self._send_auth(ws, token)
                    # 等待认证响应后再启动心跳
                    auth_ok = False
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        await self._handle_packet(ws, raw)
                        auth_ok = True
                    except asyncio.TimeoutError:
                        log.warning("5秒内未收到认证响应")
                    except Exception as e:
                        log.warning(f"接收认证响应失败: {e}")
                    if auth_ok:
                        self._heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                    # 接收消息
                    async for raw in ws:
                        await self._handle_packet(ws, raw)
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed as e:
                log.warning(f"弹幕连接断开: code={e.code}, reason={e.reason!r}，3秒后重连...")
                await asyncio.sleep(3)
            except Exception as e:
                log.warning(f"弹幕连接断开: {type(e).__name__}: {e}，3秒后重连...")
                await asyncio.sleep(3)

    async def stop(self):
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self.ws:
            await self.ws.close()

    async def _send_auth(self, ws, token):
        auth_data = {
            "uid": protocol.USER_UID,
            "roomid": protocol.REAL_ROOM_ID,
            "protover": 2,  # 告诉服务器用 zlib 压缩下发消息
            "platform": "web",
            "type": 2,
            "key": token,
        }
        # 添加 buvid 用于解除风控
        if BILI_BUVID3_CFG:
            auth_data["buvid"] = BILI_BUVID3_CFG
        body = json.dumps(auth_data).encode("utf-8")
        packet = self._make_packet(body, 7)
        await ws.send(packet)
        log.info(f"已发送认证包 (uid={protocol.USER_UID}, roomid={protocol.REAL_ROOM_ID}, buvid={'有' if BILI_BUVID3_CFG else '无'}, token长度={len(token)})")

    async def _heartbeat(self, ws):
        while self._running:
            await asyncio.sleep(30)
            try:
                packet = self._make_packet(b"", 2)
                await ws.send(packet)
            except Exception:
                break

    async def _handle_packet(self, ws, raw: bytes):
        if len(raw) < 16:
            return
        packet_len = struct.unpack(">I", raw[0:4])[0]
        header_len = struct.unpack(">H", raw[4:6])[0]
        protover = struct.unpack(">H", raw[6:8])[0]
        op = struct.unpack(">I", raw[8:12])[0]
        body = raw[header_len:packet_len]

        if op == 8:  # AUTH_REPLY
            auth_resp = json.loads(body)
            if auth_resp.get("code") == 0:
                log.info("弹幕认证成功")
            else:
                log.error(f"弹幕认证失败: {auth_resp}")
        elif op == 3:  # HEARTBEAT_REPLY
            viewer_count = struct.unpack(">I", body)[0] if len(body) >= 4 else 0
            if self.on_viewer_count:
                await self.on_viewer_count(viewer_count)
        elif op == 5:  # NORMAL
            if protover == 0 or protover == 1:
                await self._process_message(body)
            elif protover == 2:
                try:
                    decompressed = zlib.decompress(body)
                    # 可能包含多个包
                    offset = 0
                    while offset < len(decompressed):
                        if offset + 16 > len(decompressed):
                            break
                        p_len = struct.unpack(">I", decompressed[offset:offset+4])[0]
                        p_hl = struct.unpack(">H", decompressed[offset+4:offset+6])[0]
                        p_op = struct.unpack(">I", decompressed[offset+8:offset+12])[0]
                        p_body = decompressed[offset+p_hl:offset+p_len]
                        if p_op == 5:
                            await self._process_message(p_body)
                        offset += p_len
                except Exception as e:
                    log.debug(f"解压弹幕包失败: {e}")
        elif op == 2:  # HEARTBEAT (ignore, we send our own)
            pass

    async def _process_message(self, body: bytes):
        try:
            msg = json.loads(body)
            cmd = msg.get("cmd", "")
            if cmd == "DANMU_MSG":
                info = msg.get("info", [])
                if len(info) >= 3:
                    text = info[1]
                    user_info = info[2]
                    uid = user_info[0]
                    username = user_info[1]
                    if self.on_message:
                        await self.on_message(text, uid, username)
            elif cmd == "SUPER_CHAT_MESSAGE":
                data = msg.get("data", {})
                text = data.get("message", "")
                user_info = data.get("user_info", {})
                uid = user_info.get("uid", 0)
                username = user_info.get("uname", "")
                if text and self.on_message:
                    await self.on_message(text, uid, username)
        except Exception as e:
            log.debug(f"解析弹幕消息失败: {e}")

    @staticmethod
    def _make_packet(body: bytes, op: int, protover: int = 1) -> bytes:
        """构造 B站 WebSocket 数据包。protover=1 表示未压缩（认证/心跳用），2=zlib。"""
        header = struct.pack(">IHHII", len(body) + 16, 16, protover, op, 1)
        return header + body


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
