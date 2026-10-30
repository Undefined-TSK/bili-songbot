"""B站 HTTP 协议层：API 限流、WBI 签名、buvid3 获取、Cookie 管理与热重载。

可变状态（SESSDATA/BILI_JCT/BUVID3/BILI_HEADERS/REAL_ROOM_ID 等）均为本模块全局，
其他模块必须 `from . import protocol` 后以 `protocol.X` 动态访问，禁止 from-import 快照。
"""
import asyncio
import json
import os
import time
from typing import Optional, Tuple

import aiohttp

from . import config
from .config import (
    API_RATE_INTERVAL, BILI_BUVID4_CFG, BILI_JCT, CONFIG_PATH, ROOM_ID, SESSDATA, log,
)


class ApiRateLimiter:
    """全局 B站 API 请求频率限制器，防止短时间大量请求导致封 IP。"""
    def __init__(self, min_interval: float = 1.0):
        self._min_interval = min_interval
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取发送许可，必要时等待。"""
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                log.debug(f"API限速: 等待{wait:.1f}秒")
                await asyncio.sleep(wait)
            self._last_request = time.time()


_api_limiter = ApiRateLimiter(API_RATE_INTERVAL)


BILI_API_BASE = "https://api.live.bilibili.com"


BILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


BILI_FINGER_URL = "https://api.bilibili.com/x/frontend/finger"


BILI_ROOM_INIT = f"{BILI_API_BASE}/room/v1/Room/room_init?id={ROOM_ID}"


BILI_ROOM_INFO = f"{BILI_API_BASE}/room/v1/Room/get_info?room_id={ROOM_ID}"


BILI_SEND_MSG = f"{BILI_API_BASE}/msg/send"


FULL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


BUVID3 = ""


REAL_ROOM_ID = ROOM_ID  # 真实房间号，启动时通过 room_init 获取


# buvid3 配置初值（Cookie 热更新时由 reload_config 覆盖）。
# 其他模块必须以 protocol.BILI_BUVID3_CFG 动态读取，禁止 from-import 快照。
BILI_BUVID3_CFG = config.BILI_BUVID3_CFG


BILI_COOKIE_PARTS = [f"SESSDATA={SESSDATA}", f"bili_jct={BILI_JCT}"]


BILI_COOKIE = "; ".join(BILI_COOKIE_PARTS)


BILI_HEADERS = {
    "Cookie": BILI_COOKIE,
    "User-Agent": FULL_UA,
    "Referer": f"https://live.bilibili.com/{ROOM_ID}",
    "Origin": "https://live.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


async def get_real_room_id(session: aiohttp.ClientSession) -> int:
    """通过 room_init API 获取直播间真实 ID（短号→长号）。"""
    try:
        await _api_limiter.acquire()
        async with session.get(BILI_ROOM_INIT, headers=BILI_HEADERS, timeout=10) as resp:
            data = await resp.json()
            if data.get("code") == 0:
                real_id = data.get("data", {}).get("room_id", ROOM_ID)
                log.info(f"真实房间号: {real_id}（短号: {ROOM_ID}）")
                return real_id
            else:
                log.warning(f"room_init 返回 code={data.get('code')}: {data.get('message', '')}")
    except Exception as e:
        log.warning(f"获取真实房间号失败: {e}")
    return ROOM_ID


def update_buvid3(buvid3: str):
    """更新全局 Cookie 以包含 buvid3（和 buvid4）。"""
    global BUVID3, BILI_COOKIE
    BUVID3 = buvid3
    parts = [f"SESSDATA={SESSDATA}", f"bili_jct={BILI_JCT}"]
    if buvid3:
        parts.append(f"buvid3={buvid3}")
    if BILI_BUVID4_CFG:
        parts.append(f"buvid4={BILI_BUVID4_CFG}")
    BILI_COOKIE = "; ".join(parts)
    BILI_HEADERS["Cookie"] = BILI_COOKIE


async def fetch_buvid3(session: aiohttp.ClientSession) -> str:
    """获取 buvid3 用于风控校验，依次尝试三种方式。"""
    # 方法1: 调用 fingerprint API，从响应体获取
    try:
        async with session.get(
            BILI_FINGER_URL,
            headers={"User-Agent": FULL_UA, "Accept": "application/json"},
            timeout=10,
        ) as resp:
            data = await resp.json()
            if data.get("code") == 0:
                b3 = data.get("data", {}).get("buvid3", "")
                if b3:
                    log.info(f"从 fingerprint API 获取 buvid3: {b3[:16]}...")
                    return b3
            # 也检查 Set-Cookie
            b3_cookie = resp.cookies.get("buvid3")
            if b3_cookie:
                b3 = b3_cookie.value
                log.info(f"从 fingerprint 响应 Cookie 获取 buvid3: {b3[:16]}...")
                return b3
            log.warning(f"fingerprint API 未返回 buvid3 (code={data.get('code')}, msg={data.get('message', '')})")
    except Exception as e:
        log.warning(f"fingerprint API 获取 buvid3 失败: {e}")

    # 方法2: 访问直播间页面，从 Set-Cookie 获取
    try:
        async with session.get(
            f"https://live.bilibili.com/{ROOM_ID}",
            headers={"User-Agent": FULL_UA},
            timeout=10,
            allow_redirects=True,
        ) as resp:
            b3_cookie = resp.cookies.get("buvid3")
            if b3_cookie:
                b3 = b3_cookie.value
                log.info(f"从直播间页面获取 buvid3: {b3[:16]}...")
                return b3
            log.warning(f"直播间页面未返回 buvid3 Cookie (status={resp.status})")
    except Exception as e:
        log.warning(f"从直播间页面获取 buvid3 失败: {e}")

    # 方法3: 生成随机 UUID 作为 buvid3（可能无法通过风控）
    import uuid
    b3 = str(uuid.uuid4()).upper()
    log.warning(f"无法从 API 获取 buvid3，使用随机值（可能无法通过风控）: {b3[:16]}...")
    log.warning("请在 config.json 中填写 bili_buvid3 字段（从浏览器获取）")
    return b3


def decode_sessdata_expiry(sessdata: str) -> Optional[int]:
    """从 SESSDATA 中解码过期时间戳。SESSDATA 格式: hash,timestamp,hash2"""
    try:
        from urllib.parse import unquote
        decoded = unquote(sessdata)
        parts = decoded.split(",")
        if len(parts) >= 2:
            ts = int(parts[1])
            return ts
    except (ValueError, IndexError):
        pass
    return None


async def check_cookie_validity(session: aiohttp.ClientSession) -> Tuple[bool, str]:
    """调用 nav API 检查 Cookie 有效性，返回 (是否有效, 用户名)。"""
    try:
        await _api_limiter.acquire()
        async with session.get(BILI_NAV_URL, headers=BILI_HEADERS, timeout=10) as resp:
            data = await resp.json()
            if data.get("code") == 0 and data.get("data", {}).get("isLogin"):
                uname = data["data"].get("uname", "未知")
                return True, uname
            return False, data.get("message", "未登录")
    except Exception as e:
        return False, f"检查异常: {e}"


class CookieManager:
    """Cookie 有效性管理与热更新。"""

    def __init__(self):
        self.valid: bool = True
        self.username: str = ""
        self.expiry_ts: Optional[int] = decode_sessdata_expiry(SESSDATA)
        self._config_mtime: float = 0
        self._update_mtime()

    def _update_mtime(self):
        try:
            self._config_mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            pass

    def check_expiry(self) -> str:
        """检查过期时间，返回提示信息。"""
        if not self.expiry_ts:
            return "无法解析 SESSDATA 过期时间"
        now = time.time()
        remaining = self.expiry_ts - now
        if remaining <= 0:
            return "SESSDATA 已过期！请更新 config.json"
        days = int(remaining // 86400)
        if days <= 7:
            return f"⚠ SESSDATA 将在 {days} 天后过期，请尽快更新"
        return f"SESSDATA 剩余有效期: {days} 天"

    async def validate(self, session: aiohttp.ClientSession) -> bool:
        """启动时校验 Cookie 有效性。"""
        self.valid, self.username = await check_cookie_validity(session)
        if self.valid:
            log.info(f"Cookie 有效，当前用户: {self.username}")
        else:
            log.error(f"Cookie 无效: {self.username}")
        # 打印过期信息
        log.info(self.check_expiry())
        return self.valid

    def mark_invalid(self, reason: str = ""):
        """标记 Cookie 失效。"""
        if self.valid:
            self.valid = False
            log.error(f"Cookie 已失效: {reason}。请更新 config.json 中的 SESSDATA 和 bili_jct")

    def check_hot_reload(self) -> bool:
        """检查 config.json 是否被修改，返回是否重新加载了。"""
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
            if mtime != self._config_mtime:
                self._config_mtime = mtime
                return True
        except OSError:
            pass
        return False

    def reload_config(self) -> bool:
        """重新加载 config.json 中的 Cookie 配置（同步写回 config 模块）。"""
        global SESSDATA, BILI_JCT, BILI_COOKIE, BILI_BUVID3_CFG, BILI_BUVID4_CFG
        try:
            new_cfg = json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
            new_sessdata = new_cfg.get("bili_sessdata", "")
            new_jct = new_cfg.get("bili_jct", "")
            new_buvid3 = new_cfg.get("bili_buvid3", "")
            new_buvid4 = new_cfg.get("bili_buvid4", "")
            changed = False
            if new_sessdata and new_jct and (new_sessdata != SESSDATA or new_jct != BILI_JCT):
                SESSDATA = new_sessdata
                BILI_JCT = new_jct
                changed = True
            if new_buvid3 and new_buvid3 != BILI_BUVID3_CFG:
                BILI_BUVID3_CFG = new_buvid3
                changed = True
            if new_buvid4 and new_buvid4 != BILI_BUVID4_CFG:
                BILI_BUVID4_CFG = new_buvid4
                changed = True
            if changed:
                cookie_parts = [f"SESSDATA={SESSDATA}", f"bili_jct={BILI_JCT}"]
                buvid3_to_use = BILI_BUVID3_CFG or BUVID3
                if buvid3_to_use:
                    cookie_parts.append(f"buvid3={buvid3_to_use}")
                if BILI_BUVID4_CFG:
                    cookie_parts.append(f"buvid4={BILI_BUVID4_CFG}")
                BILI_COOKIE = "; ".join(cookie_parts)
                BILI_HEADERS["Cookie"] = BILI_COOKIE
                self.expiry_ts = decode_sessdata_expiry(SESSDATA)
                self.valid = True
                log.info("config.json 已热更新，Cookie 已重新加载")
                log.info(self.check_expiry())
                # 拆包后：config 模块持有配置初值副本，热更新成功时双向同步
                config.SESSDATA = SESSDATA
                config.BILI_JCT = BILI_JCT
                config.BILI_BUVID3_CFG = BILI_BUVID3_CFG
                config.BILI_BUVID4_CFG = BILI_BUVID4_CFG
            return True
        except Exception as e:
            log.warning(f"热更新 config.json 失败: {e}")
        return False
