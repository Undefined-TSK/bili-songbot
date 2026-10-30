#!/usr/bin/env python3.12
"""
B站直播间点歌机器人
监听弹幕指令 /play 和 /switch，自动搜索播放音乐并管理投票切歌。
所有提供的音源均会被尝试调用。
"""

import asyncio
import json
import struct
import zlib
import re
import time
import subprocess
import os
import sys
import logging
import hashlib
import base64
import tempfile
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import quote, urlencode

import aiohttp
import websockets

# ======================== 日志配置（控制台） ========================
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
))
logging.basicConfig(level=logging.INFO, handlers=[_console_handler])
log = logging.getLogger("点歌机")

# ======================== 配置加载 ========================
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        log.error("config.json 不存在，请先创建配置文件")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CFG = load_config()

# ======================== 日志增强（文件输出） ========================
def setup_file_logging(cfg: dict):
    """根据 config.json 配置添加文件日志处理器。"""
    level_str = cfg.get("log_level", "INFO").upper()
    level = getattr(logging, level_str, logging.INFO)
    log.setLevel(level)
    _console_handler.setLevel(level)

    log_file = cfg.get("log_file", "logs/bot.log")
    log_dir = os.path.dirname(os.path.join(os.path.dirname(CONFIG_PATH), log_file))
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(os.path.dirname(CONFIG_PATH), log_file)
    max_bytes = cfg.get("log_max_bytes", 5 * 1024 * 1024)
    backup_count = cfg.get("log_backup_count", 3)

    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    file_handler.setLevel(level)
    logging.getLogger().addHandler(file_handler)
    log.info(f"日志文件: {log_path} (级别: {level_str}, 轮转: {max_bytes // 1024}KB x {backup_count + 1})")

setup_file_logging(CFG)


def cleanup_old_logs(cfg: dict):
    """启动前清理过期日志：按年龄删除 + 按总量兜底。永不删除当前 bot.log。"""
    import glob
    log_file = cfg.get("log_file", "logs/bot.log")
    log_dir = os.path.dirname(os.path.join(os.path.dirname(CONFIG_PATH), log_file))
    if not log_dir or not os.path.isdir(log_dir):
        return

    max_age_days = cfg.get("log_max_age_days", 7)
    max_total_mb = cfg.get("log_max_total_mb", 50)
    current_log = os.path.join(os.path.dirname(CONFIG_PATH), log_file)
    current_log_abs = os.path.abspath(current_log)

    # 收集所有日志文件（.log, .log.1, .log.2, ...）
    pattern = os.path.join(log_dir, "bot.log*")
    all_files = []
    for f in glob.glob(pattern):
        abs_f = os.path.abspath(f)
        if abs_f == current_log_abs:
            continue  # 永不删除当前日志
        try:
            stat = os.stat(f)
            all_files.append((f, stat.st_mtime, stat.st_size))
        except Exception:
            pass

    if not all_files:
        return

    now = time.time()
    max_age_seconds = max_age_days * 86400
    deleted = []
    freed_bytes = 0

    # 第一步：按年龄删除
    for f, mtime, size in sorted(all_files, key=lambda x: x[1]):  # 最旧的先处理
        if now - mtime > max_age_seconds:
            try:
                os.unlink(f)
                deleted.append(os.path.basename(f))
                freed_bytes += size
            except Exception:
                pass

    # 第二步：按总量兜底（如果删除过期文件后仍超限）
    remaining = []
    for f in glob.glob(pattern):
        abs_f = os.path.abspath(f)
        if abs_f == current_log_abs:
            continue
        try:
            stat = os.stat(f)
            remaining.append((f, stat.st_mtime, stat.st_size))
        except Exception:
            pass

    total_size = sum(s for _, _, s in remaining)
    max_total_bytes = max_total_mb * 1024 * 1024
    if total_size > max_total_bytes:
        for f, mtime, size in sorted(remaining, key=lambda x: x[1]):  # 最旧的先删
            if total_size <= max_total_bytes:
                break
            try:
                os.unlink(f)
                deleted.append(os.path.basename(f))
                freed_bytes += size
                total_size -= size
            except Exception:
                pass

    # 输出清理结果（有清理时才输出，不打扰用户）
    if deleted:
        freed_mb = freed_bytes / (1024 * 1024)
        msg = f"已清理 {len(deleted)} 个过期日志文件，释放 {freed_mb:.1f} MB"
        print(f"  [日志清理] {msg}")
        log.info(msg)


cleanup_old_logs(CFG)

ROOM_ID = CFG["bili_room_id"]
SESSDATA = CFG["bili_sessdata"]
BILI_JCT = CFG["bili_jct"]
BILI_BUVID3_CFG = CFG.get("bili_buvid3", "")
BILI_BUVID4_CFG = CFG.get("bili_buvid4", "")
DANMAKU_INTERVAL = CFG.get("danmaku_interval", 5)
MAX_MSG_PER_MINUTE = CFG.get("max_msg_per_minute", 20)
DANMAKU_MAX_LEN = CFG.get("danmaku_max_len", 20)  # B站弹幕字数上限
API_RATE_INTERVAL = CFG.get("api_rate_interval", 1.0)  # B站API请求最小间隔(秒)
ROOM_CHECK_INTERVAL = CFG.get("room_check_interval", 60)
VOTE_DURATION = CFG.get("vote_duration", 30)
VOTE_BROADCAST_INTERVAL = CFG.get("vote_broadcast_interval", 5)
FFPLAY_PATH = CFG.get("ffplay_path", "ffplay")
FFPLAY_ARGS = CFG.get("ffplay_args", ["-nodisp", "-autoexit", "-loglevel", "quiet"])
SOURCE_DIR = CFG.get("source_dir", os.path.join(os.path.expanduser("~"), "Downloads", "音源", "文件形式"))
NODE_PATH = CFG.get("node_path", "node")
BRIDGE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source_bridge.js")

# ======================== B站 API 请求频率限制器 ========================
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

    def set_interval(self, interval: float):
        self._min_interval = interval

_api_limiter = ApiRateLimiter(API_RATE_INTERVAL)

# ======================== B站 API ========================
BILI_API_BASE = "https://api.live.bilibili.com"
BILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
BILI_FINGER_URL = "https://api.bilibili.com/x/frontend/finger"
BILI_DANMAKU_CONF_OLD = f"{BILI_API_BASE}/room/v1/Danmu/getConf?room_id={ROOM_ID}"
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
USER_UID = 0  # 用户 UID，启动时从 nav API 获取

BILI_COOKIE_PARTS = [f"SESSDATA={SESSDATA}", f"bili_jct={BILI_JCT}"]
if BILI_BUVID3_CFG:
    BILI_COOKIE_PARTS.append(f"buvid3={BILI_BUVID3_CFG}")
if BILI_BUVID4_CFG:
    BILI_COOKIE_PARTS.append(f"buvid4={BILI_BUVID4_CFG}")
BILI_COOKIE = "; ".join(BILI_COOKIE_PARTS)
BILI_HEADERS = {
    "Cookie": BILI_COOKIE,
    "User-Agent": FULL_UA,
    "Referer": f"https://live.bilibili.com/{ROOM_ID}",
    "Origin": "https://live.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# ======================== 真实房间号 ========================
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


# ======================== WBI 签名 ========================
# B站风控要求 getDanmuInfo 等接口必须携带 WBI 签名参数 (w_rid + wts)
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
_wbi_cache = {"img_key": "", "sub_key": "", "ts": 0}


def _get_mixin_key(orig: str) -> str:
    """对 imgKey+subKey 进行字符顺序打乱编码，取前32位。"""
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """为请求参数进行 WBI 签名，返回添加了 w_rid 和 wts 的参数。"""
    mixin_key = _get_mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    # 过滤 value 中的特殊字符
    params = {
        k: "".join(filter(lambda c: c not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urlencode(params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


async def get_wbi_keys(session: aiohttp.ClientSession) -> tuple:
    """从 nav 接口获取 img_key 和 sub_key（缓存1小时）。同时捕获用户 UID。"""
    global USER_UID
    now = time.time()
    if _wbi_cache["img_key"] and now - _wbi_cache["ts"] < 3600:
        return _wbi_cache["img_key"], _wbi_cache["sub_key"]
    try:
        await _api_limiter.acquire()
        async with session.get(BILI_NAV_URL, headers=BILI_HEADERS, timeout=10) as resp:
            data = await resp.json()
            if data.get("code") == 0:
                # 捕获用户 UID
                mid = data.get("data", {}).get("mid", 0)
                if mid:
                    USER_UID = mid
                    log.info(f"获取到用户 UID: {USER_UID}")
                wbi_img = data.get("data", {}).get("wbi_img", {})
                img_url = wbi_img.get("img_url", "")
                sub_url = wbi_img.get("sub_url", "")
                img_key = img_url.rsplit("/", 1)[-1].split(".")[0] if img_url else ""
                sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0] if sub_url else ""
                if img_key and sub_key:
                    _wbi_cache["img_key"] = img_key
                    _wbi_cache["sub_key"] = sub_key
                    _wbi_cache["ts"] = now
                    log.debug(f"WBI keys 已获取: img={img_key[:8]}... sub={sub_key[:8]}...")
                    return img_key, sub_key
    except Exception as e:
        log.warning(f"获取 WBI keys 失败: {e}")
    return "", ""


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


# ======================== Cookie 管理 ========================
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
        import datetime
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
        """重新加载 config.json 中的 Cookie 配置。"""
        global SESSDATA, BILI_JCT, BILI_COOKIE, BILI_HEADERS, BILI_BUVID3_CFG, BILI_BUVID4_CFG
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
                return True
        except Exception as e:
            log.warning(f"热更新 config.json 失败: {e}")
        return False

# ======================== 音源 API 端点 ========================
# --- 星海主 API ---

# --- CHKSZ (网易云) ---

# --- 溯音系列 ---

# --- 长青/念心 SVIP 模板 ---

# --- HUIBQ ---

# --- fish-music ---

# --- HYWmusic ---

# --- 听音音源 ---

# --- 收集の聚合 ---

# --- 汽水VIP ---

# --- 聚合API (lerd) ---

# --- 忆音 ---

# --- 玉宁熙 ---

# --- 平台映射 ---
PLATFORM_NAMES = {
    "wy": "网易云", "tx": "QQ音乐", "kw": "酷我", "kg": "酷狗", "mg": "咪咕",
}
QUALITY_LADDER = ["flac24bit", "flac", "320k", "192k", "128k"]

# --- 平台 CDN 请求头映射 ---
# 通用头（无法确定平台时使用）


def get_headers_for_url(url: str) -> dict:
    """根据 URL 域名选择 CDN 防盗链 headers（参考 Museek cdnHeadersForUrl）。

    代理 URL（如 mcp.nianxinxz.com）不匹配任何已知平台 CDN，
    只返回默认 User-Agent，避免错误的 Referer 导致代理服务器拒绝请求。
    """
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if not host:
        return {"User-Agent": FULL_UA}
    if "music.126.net" in host or "music.163.com" in host:
        return {"Referer": "https://music.163.com/", "User-Agent": FULL_UA}
    if "qq.com" in host or "gtimg.cn" in host:
        return {"Referer": "https://y.qq.com/", "User-Agent": FULL_UA}
    if "kuwo" in host or "koowo" in host:
        return {"Referer": "https://www.kuwo.cn/", "User-Agent": FULL_UA}
    if "kugou" in host or "kgimg" in host:
        return {"Referer": "https://www.kugou.com/", "User-Agent": FULL_UA}
    if "migu" in host or "nf.migu" in host:
        return {"Referer": "https://music.migu.cn/", "User-Agent": FULL_UA}
    # 代理 URL 和未知域名：只带 User-Agent（不加 Referer）
    return {"User-Agent": FULL_UA}

# --- GitHub 链接源 ---


# ======================== HTTP 工具 ========================

async def validate_audio_url(session: aiohttp.ClientSession, url: str, platform: str = "") -> bool:
    """验证 URL 是否指向有效音频。使用 GET Range 请求检查实际数据内容（比 HEAD 更可靠）。

    代理 URL（如咪咕 mg.php）对 HEAD 请求可能返回 text/html，
    但 GET 请求实际返回音频数据，因此用 GET Range 下载前 2KB 检查。
    Headers 按 URL 域名选择（参考 Museek cdnHeadersForUrl），而非平台名。
    """
    headers = get_headers_for_url(url)
    headers["Range"] = "bytes=0-2047"
    try:
        async with session.get(url, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=8),
                                allow_redirects=True) as resp:
            if resp.status >= 400:
                log.info(f"URL 验证失败（HTTP {resp.status}）: {url[:80]}")
                return False
            data = await resp.content.read(2048)
            if not data or len(data) < 4:
                log.info(f"URL 验证失败（数据过小 {len(data) if data else 0}B）: {url[:80]}")
                return False
            # JSON / HTML 错误页
            if data[:1] in (b'{', b'<'):
                log.info(f"URL 验证失败（JSON/HTML内容）: {url[:80]}")
                return False
            # 纯文本错误信息
            try:
                text = data[:200].decode('ascii').strip().lower()
                if text.startswith(('error', 'err', 'failed', 'not found', '404', '403')):
                    log.info(f"URL 验证失败（错误文本）: {url[:80]}")
                    return False
            except UnicodeDecodeError:
                pass  # 二进制数据，很可能是音频
            log.debug(f"URL 验证通过: {url[:80]}")
            return True
    except Exception as e:
        log.info(f"URL 验证异常（fallback通过）: {e} - {url[:80]}")
        # 请求失败时 fallback 为 True（让下载流程自行判断）
        return True


async def download_audio(session: aiohttp.ClientSession, url: str, platform: str = "") -> Optional[str]:
    """下载音频到临时文件，返回文件路径。失败返回 None。
    Headers 按 URL 域名选择（参考 Museek cdnHeadersForUrl），而非平台名。
    """
    headers = get_headers_for_url(url)
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=tmp_dir)
    tmp_path = tmp.name
    tmp.close()
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30),
                                allow_redirects=True) as resp:
            if resp.status >= 400 or resp.status < 200:
                log.warning(f"下载音频失败: HTTP {resp.status}")
                os.unlink(tmp_path)
                return None
            with open(tmp_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)
        size = os.path.getsize(tmp_path)
        if size < 1024:
            log.warning(f"音频文件过小 ({size} bytes)，可能不是有效音频")
            os.unlink(tmp_path)
            return None
        log.info(f"音频下载完成: {size // 1024} KB")
        return tmp_path
    except Exception as e:
        log.warning(f"下载音频异常: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None


async def download_audio_via_bridge(url: str) -> Optional[str]:
    """通过 Node.js 桥接器下载音频（回退方案）。

    当 Python aiohttp 下载失败时（如代理 URL 返回 0 字节），
    使用 Node.js 原生 http/https 模块下载（参考 lx-music-desktop request.ts）。
    跳过已知无法下载的代理 URL（.php?id= 模式），避免浪费时间。
    """
    if ".php?" in url and "id=" in url:
        log.debug(f"跳过代理 URL（.php?id= 模式）: {url[:80]}")
        return None

    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=tmp_dir)
    tmp_path = tmp.name
    tmp.close()
    try:
        size = await _bridge.download(url, tmp_path)
        if size and size > 0:
            log.info(f"桥接器下载完成: {size // 1024} KB")
            return tmp_path
        else:
            log.warning(f"桥接器下载失败: 无数据")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return None
    except Exception as e:
        log.warning(f"桥接器下载异常: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None


# ======================== 工具函数 ========================
def extract_artist(raw: Any) -> str:
    """规范化歌手字段：处理列表、嵌套字典等多种格式，返回 '/' 分隔的字符串。"""
    if not raw:
        return ""
    if isinstance(raw, str):
        # 字符串形式：可能是 "周杰伦&费玉清" 或 "周杰伦,费玉清"
        # 统一分隔符为 /
        for sep in ["&", ",", "、", "，", " feat ", " ft ", " feat. ", " ft. "]:
            raw = raw.replace(sep, "/")
        # 去掉多余空格
        parts = [p.strip() for p in raw.split("/") if p.strip()]
        return "/".join(parts) if parts else raw.strip()
    if isinstance(raw, list):
        # 列表形式：可能是 ["周杰伦", "费玉清"] 或 [{"name": "周杰伦", "id": 1}, ...]
        names = []
        for item in raw:
            if isinstance(item, str):
                names.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("singer") or item.get("artist") or ""
                if name:
                    names.append(str(name).strip())
            elif isinstance(item, (int, float)):
                names.append(str(item))
        return "/".join(names) if names else ""
    if isinstance(raw, dict):
        return extract_artist(raw.get("name") or raw.get("singer") or raw.get("artist") or "")
    return str(raw)


# ======================== 歌曲信息 ========================
@dataclass
class SongInfo:
    """搜索结果中的一首歌曲。"""
    name: str = ""
    singer: str = ""
    album: str = ""
    song_id: str = ""
    platform: str = ""  # wy, tx, kw, kg, mg
    interval: str = ""

    @property
    def search_keyword(self) -> str:
        """用于关键词搜索的字符串。"""
        if self.singer:
            return f"{self.name} {self.singer}"
        return self.name


# ======================== 音源模块 ========================
# 每个音源对应一个异步函数: async fn(session, song, quality) -> Optional[str]
# song: SongInfo, quality: str (如 "flac24bit")
# 返回音频直链 URL 或 None

# --- 6 GitHub 链接源 ---



# ======================== Node.js 音源桥接器 ========================
class SourceBridge:
    """通过 Node.js 子进程执行 lx-music 音源 JS 脚本。"""

    def __init__(self):
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._ready = False

    async def start(self):
        """启动 Node.js 桥接器子进程。"""
        if not os.path.exists(BRIDGE_SCRIPT):
            raise FileNotFoundError(f"桥接器脚本不存在: {BRIDGE_SCRIPT}")
        if not os.path.exists(SOURCE_DIR):
            raise FileNotFoundError(f"音源目录不存在: {SOURCE_DIR}")

        self.process = await asyncio.create_subprocess_exec(
            NODE_PATH, BRIDGE_SCRIPT, SOURCE_DIR,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        # 等待 ready 信号
        await asyncio.wait_for(self._wait_ready(), timeout=30)
        log.info(f"音源桥接器已就绪 (PID: {self.process.pid})")

    async def _wait_ready(self):
        while not self._ready:
            await asyncio.sleep(0.1)

    async def _read_loop(self):
        """读取 stdout JSON 响应。"""
        while self.process and self.process.returncode is None:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                data = json.loads(line.decode("utf-8").strip())
                if data.get("type") == "ready":
                    log.info(f"桥接器加载了 {data.get('scripts', 0)} 个音源脚本")
                    self._ready = True
                    continue
                req_id = data.get("id")
                if req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if data.get("success"):
                        fut.set_result(data)
                    else:
                        fut.set_exception(Exception(data.get("error", "未知错误")))
            except Exception as e:
                log.debug(f"桥接器读取异常: {e}")

    async def _read_stderr(self):
        """读取 stderr 调试日志。"""
        while self.process and self.process.returncode is None:
            try:
                line = await self.process.stderr.readline()
                if not line:
                    break
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    log.debug(f"[bridge] {msg}")
            except Exception:
                pass

    async def _request(self, payload: dict, timeout: int = 15) -> dict:
        """发送请求并等待响应。"""
        if not self.process or self.process.returncode is not None:
            raise Exception("桥接器未运行")
        self._request_id += 1
        req_id = self._request_id
        payload["id"] = req_id
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        line = json.dumps(payload) + "\n"
        self.process.stdin.write(line.encode("utf-8"))
        await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise Exception(f"桥接器请求超时 ({timeout}s)")

    async def search(self, source: str, keyword: str, limit: int = 5) -> Optional[list]:
        """搜索歌曲，返回结果列表。"""
        try:
            resp = await self._request({
                "action": "search",
                "source": source,
                "keyword": keyword,
                "limit": limit,
            }, timeout=10)
            data = resp.get("data")
            if isinstance(data, list) and data:
                return data  # 返回完整结果列表
            if isinstance(data, dict):
                return [data]
            return None
        except Exception as e:
            log.debug(f"桥接器搜索 {source} 失败: {e}")
            return None

    async def get_url(self, source: str, song_info: dict, quality: str) -> Optional[str]:
        """获取播放 URL。"""
        try:
            resp = await self._request({
                "action": "musicUrl",
                "source": source,
                "info": song_info,
                "type": quality,
            }, timeout=20)
            url = resp.get("url")
            if url and url.startswith("http"):
                return url
            return None
        except Exception as e:
            log.debug(f"桥接器获取URL {source} {quality} 失败: {e}")
            return None

    async def download(self, url: str, output_path: str, headers: dict = None) -> Optional[int]:
        """通过 Node.js 原生 http/https 模块下载音频到文件（参考 lx-music-desktop）。

        某些代理 URL 对 Python aiohttp 返回 0 字节，
        但 Node.js http/https 模块可正常下载。
        返回文件大小(bytes)，失败返回 None。
        """
        try:
            resp = await self._request({
                "action": "download",
                "url": url,
                "outputPath": output_path,
                "headers": headers or {},
            }, timeout=20)
            if resp.get("success"):
                data = resp.get("data", {})
                size = data.get("size", 0)
                if size > 0:
                    return size
            return None
        except Exception as e:
            log.debug(f"桥接器下载失败: {e}")
            return None

    async def stop(self):
        """停止桥接器。"""
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass


# 全局桥接器实例
_bridge: Optional[SourceBridge] = None


# ======================== 搜索 ========================

def _normalize_text(text: str) -> str:
    """标准化文本用于模糊匹配：去除空格、标点、括号内容，小写。"""
    # 去除括号及括号内内容
    text = re.sub(r'[（(].*?[)）]', '', text)
    # 去除非字母数字非中日韩字符
    text = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
    return text.lower()


def _fuzzy_match(keyword: str, target: str) -> bool:
    """模糊匹配：标准化后子串匹配或字符重叠率 >= 60%。"""
    norm_kw = _normalize_text(keyword)
    norm_target = _normalize_text(target)
    if not norm_kw or not norm_target:
        return False
    # 标准化后子串匹配（双向）
    if norm_kw in norm_target or norm_target in norm_kw:
        return True
    # 字符重叠率
    common = sum(1 for c in norm_kw if c in norm_target)
    ratio = common / max(len(norm_kw), len(norm_target))
    return ratio >= 0.6


def _score_search_result(name: str, result_singer: str, keyword: str, singer: str) -> int:
    """评分搜索结果质量，分数越高越匹配。"""
    score = 0
    norm_name = _normalize_text(name)
    norm_kw = _normalize_text(keyword)
    # 歌名匹配（标准化后比较）
    if norm_name == norm_kw:
        score += 100  # 完全匹配
    elif norm_name.startswith(norm_kw):
        score += 80   # 以关键词开头
    elif norm_kw in norm_name:
        score += 50   # 包含关键词
    elif _fuzzy_match(keyword, name):
        score += 30   # 模糊匹配
    else:
        score += 10   # 低匹配度
    # 惩罚带后缀的版本（翻唱/混音/伴奏等）
    suffix_patterns = ['正式版', 'remix', 'cover', '翻唱', 'dj', 'DJ', '混音',
                       '伴奏', 'live', 'Live', '版', 'cover', '重新', '改编']
    for p in suffix_patterns:
        if p.lower() in name.lower():
            score -= 30
            break
    # 歌手匹配（标准化后比较）
    if singer:
        norm_singer = _normalize_text(singer)
        norm_rsinger = _normalize_text(result_singer)
        if norm_singer == norm_rsinger:
            score += 50  # 完全匹配
        elif norm_singer in norm_rsinger or norm_rsinger in norm_singer:
            score += 30  # 包含歌手
        elif _fuzzy_match(singer, result_singer):
            score += 15  # 模糊匹配
        else:
            score -= 10  # 歌手不匹配
    return score


async def search_song(session: aiohttp.ClientSession, keyword: str, singer: str = "") -> Optional[SongInfo]:
    """通过 Node.js 桥接器搜索歌曲。并行搜索所有平台，3秒窗口收集结果择优返回。"""
    search_kw = f"{keyword} {singer}" if singer else keyword
    sources = ["kw", "wy", "tx", "kg", "mg"]

    # 并行发起所有平台搜索
    tasks = {}
    for s in sources:
        tasks[s] = asyncio.create_task(_bridge.search(s, search_kw, limit=5))

    # 收集 3 秒内的结果，按评分择优（完美匹配立即返回）
    pending = set(tasks.values())
    source_by_task = {t: s for s, t in tasks.items()}
    best_result = None  # (score, source, result)
    collect_deadline = time.time() + 3  # 3秒收集窗口

    try:
        while pending:
            remaining = max(0.1, collect_deadline - time.time())
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED, timeout=remaining
            )
            for task in done:
                source = source_by_task.get(task, "")
                if task.cancelled():
                    continue
                try:
                    result = task.result()
                except Exception as e:
                    log.debug(f"搜索 {source} 失败: {e}")
                    continue
                if not result:
                    continue
                # 兼容 list/dict 格式
                if isinstance(result, list):
                    results_list = result
                else:
                    results_list = [result]
                for r in results_list:
                    if not isinstance(r, dict):
                        continue
                    name = r.get("name", "")
                    result_singer = r.get("singer", "") or r.get("artist", "")
                    # 垃圾结果过滤：标准化歌名=标准化关键词且歌手为空（API原样返回搜索词）
                    if not result_singer and _normalize_text(name) == _normalize_text(search_kw):
                        log.debug(f"跳过垃圾结果（歌名=搜索词原样）: {name}")
                        continue
                    # 模糊匹配歌名：先尝试歌名关键词，再尝试完整搜索词
                    if not _fuzzy_match(keyword, name) and not _fuzzy_match(search_kw, name):
                        log.debug(f"歌名不匹配: {name} vs {keyword}")
                        continue
                    # 如果提供了歌手名，模糊匹配歌手
                    if singer:
                        if not _fuzzy_match(singer, result_singer):
                            log.debug(f"歌手不匹配: {result_singer} vs {singer}")
                            continue
                    # 评分
                    score = _score_search_result(name, result_singer, keyword, singer)
                    log.debug(f"搜索候选: {name} - {result_singer} [{source}] 评分={score}")
                    # 完美匹配（歌名完全一致 + 歌手完全一致）立即返回
                    if score >= 150:
                        song_id = str(r.get("id", r.get("songmid", r.get("song_id", ""))))
                        if song_id.startswith("MUSIC_"):
                            song_id = song_id[6:]
                        log.info(f"搜索成功: {name} - {result_singer} [{PLATFORM_NAMES.get(source, source)}] ID={song_id} (完美匹配)")
                        return SongInfo(
                            name=name, singer=result_singer,
                            album=r.get("albumName", r.get("album", "")),
                            song_id=song_id, platform=source,
                            interval=r.get("interval", ""),
                        )
                    # 记录最佳候选
                    if best_result is None or score > best_result[0]:
                        best_result = (score, source, r)
            # 收集窗口结束
            if time.time() >= collect_deadline:
                break

        # 返回最佳候选
        if best_result:
            score, source, r = best_result
            name = r.get("name", "")
            result_singer = r.get("singer", "") or r.get("artist", "")
            song_id = str(r.get("id", r.get("songmid", r.get("song_id", ""))))
            if song_id.startswith("MUSIC_"):
                song_id = song_id[6:]
            log.info(f"搜索成功: {name} - {result_singer} [{PLATFORM_NAMES.get(source, source)}] ID={song_id} (评分={score})")
            return SongInfo(
                name=name, singer=result_singer,
                album=r.get("albumName", r.get("album", "")),
                song_id=song_id, platform=source,
                interval=r.get("interval", ""),
            )
        log.info(f"搜索失败: {search_kw}")
        return None
    finally:
        # 取消未完成的搜索任务
        for task in tasks.values():
            if not task.done():
                task.cancel()


# ======================== 获取播放链接 ========================
async def get_music_url(session: aiohttp.ClientSession, song: SongInfo) -> Optional[str]:
    """通过 Node.js 桥接器获取播放 URL，按音质降级。"""
    song_info = {
        "name": song.name,
        "singer": song.singer,
        "song_id": song.song_id,
        "album": song.album,
        "interval": song.interval,
    }
    for quality in QUALITY_LADDER:
        log.info(f"尝试音质: {quality}")
        url = await _bridge.get_url(song.platform, song_info, quality)
        if url and url.startswith("http"):
            log.info(f"获取成功 ({quality}): {url[:80]}...")
            return url
    return None


# ======================== B站弹幕 WebSocket ========================
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
                    params = enc_wbi({"id": REAL_ROOM_ID, "type": 0}, img_key, sub_key)
                    query_str = urlencode(params)
                    url = f"{BILI_API_BASE}/xlive/web-room/v1/index/getDanmuInfo?{query_str}"
                    log.debug(f"getDanmuInfo 请求: {url}")
                    async with session.get(url, headers=BILI_HEADERS, timeout=10) as resp:
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
                await _api_limiter.acquire()
                async with session.get(BILI_DANMAKU_CONF_OLD, headers=BILI_HEADERS, timeout=10) as resp:
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
            "uid": USER_UID,
            "roomid": REAL_ROOM_ID,
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
        log.info(f"已发送认证包 (uid={USER_UID}, roomid={REAL_ROOM_ID}, buvid={'有' if BILI_BUVID3_CFG else '无'}, token长度={len(token)})")

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


# ======================== B站弹幕发送 ========================
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


def _fmt_song(song: SongInfo) -> str:
    """格式化歌名用于弹幕显示：只显示歌名，过长则用...省略。"""
    name = song.name or "未知"
    # 歌名超过10个字符则截断
    if len(name) > 10:
        return name[:5] + "..."
    return name


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
                    await _api_limiter.acquire()
                    async with self.session.post(BILI_SEND_MSG, data=data, headers=BILI_HEADERS) as resp:
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


# ======================== 播放管理 ========================
class PlaybackManager:
    """音频播放管理。先下载到临时文件，再用 ffplay 播放。"""

    def __init__(self, session: aiohttp.ClientSession = None):
        self.process: Optional[subprocess.Popen] = None
        self.current_song: Optional[SongInfo] = None
        self.session = session
        self._tmp_file: Optional[str] = None
        self._intentional_stop = False  # 标记主动停止（切歌/停止）

    def set_session(self, session: aiohttp.ClientSession):
        self.session = session

    async def play(self, url: str, song: SongInfo, on_finish=None, on_start=None) -> bool:
        """下载并播放音频。返回 False 表示下载失败（未调用on_finish，可重试），True 表示已播放。
        on_start: 下载完成、ffplay启动前的回调（用于发送"正在播放"弹幕）。"""
        self.stop()
        self._intentional_stop = False  # 重置：stop()的标记是给上一首歌的
        self.current_song = song
        success = False
        tmp_path = None
        try:
            # 1. 下载音频到临时文件
            if self.session:
                log.info(f"正在下载: {song.name} - {song.singer}")
                tmp_path = await download_audio(self.session, url, song.platform)
                if not tmp_path:
                    # aiohttp 下载失败，尝试通过 Node.js 桥接器下载（解决代理 URL 0 字节问题）
                    log.info(f"aiohttp 下载失败，尝试桥接器下载...")
                    tmp_path = await download_audio_via_bridge(url)
                if not tmp_path:
                    log.error(f"音频下载失败: {song.name} - {song.singer}")
                    return False  # 下载失败，不调用on_finish，让调用方重试
                self._tmp_file = tmp_path
                play_target = tmp_path
            else:
                # 回退：直接用 URL（可能因缺少 headers 失败）
                play_target = url

            # 2. 用 ffplay 播放本地文件
            cmd = [FFPLAY_PATH] + FFPLAY_ARGS + [play_target]
            # 下载完成，即将开始播放 → 发送"正在播放"弹幕（确保弹幕状态与实际一致）
            if on_start:
                await on_start()
            log.info(f"开始播放: {song.name} - {song.singer}")
            # 捕获 stderr 用于调试
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._wait_process)
            # 先检查主动停止（stop()可能已将self.process置为None）
            if self._intentional_stop:
                log.info(f"主动停止播放: {song.name} - {song.singer}")
                success = True
            elif self.process and self.process.returncode == 0:
                success = True
                log.info(f"播放完成: {song.name} - {song.singer}")
            else:
                rc = self.process.returncode if self.process else "N/A"
                stderr = ""
                if self.process and self.process.stderr:
                    stderr = self.process.stderr.read().decode("utf-8", errors="replace")
                log.error(f"播放失败 (returncode={rc}): {stderr[:500]}")
        except FileNotFoundError:
            log.error("ffplay 未找到，请安装 FFmpeg 或在 config.json 中配置 ffplay_path")
        except Exception as e:
            log.error(f"播放异常: {e}")
        finally:
            intentional = self._intentional_stop  # 捕获，下面会重置
            self.process = None
            self.current_song = None
            self._intentional_stop = False  # 重置标记
            # 清理临时文件
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                self._tmp_file = None
        # on_finish 在 finally 之外，仅在实际播放过（非下载失败）时调用
        if on_finish:
            await on_finish(success, intentional=intentional)
        return True

    def _wait_process(self):
        # 使用局部引用，防止 stop() 将 self.process 置 None 后此处访问崩溃
        proc = self.process
        if proc:
            proc.wait()

    def stop(self):
        """停止当前播放（标记为主动停止，不触发播放失败回调）。"""
        self._intentional_stop = True
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            log.info("已停止当前播放")
        # 清理临时文件
        if self._tmp_file:
            try:
                os.unlink(self._tmp_file)
            except Exception:
                pass
            self._tmp_file = None

    @property
    def is_playing(self) -> bool:
        return self.process is not None


# ======================== 投票管理 ========================
class VoteManager:
    """切歌投票管理。"""

    def __init__(self, sender: BiliSender):
        self.sender = sender
        self.active = False
        self.yes_votes: set = set()
        self.no_votes: set = set()
        self.voters: set = set()
        self._task: Optional[asyncio.Task] = None
        self._viewer_count = 0
        self._is_still_playing = None  # 回调：投票目标歌曲是否仍在播放

    def update_viewer_count(self, count: int):
        self._viewer_count = count

    def get_threshold(self) -> int:
        """根据在线人数动态计算投票门槛。"""
        v = self._viewer_count
        if v <= 0:
            return 1
        elif v == 1:
            return 1
        elif v == 2:
            return 2
        elif v <= 4:
            return 2
        elif v <= 6:
            return 3
        else:
            return max(3, (v + 1) // 2)

    async def start_vote(self, on_pass, is_still_playing=None):
        """发起切歌投票。is_still_playing 回调返回 True 表示投票目标歌曲仍在播放。"""
        if self.active:
            return
        self.active = True
        self.yes_votes.clear()
        self.no_votes.clear()
        self.voters.clear()
        self._is_still_playing = is_still_playing
        threshold = self.get_threshold()
        await self.sender.send(f"[切歌投票] Y/N 同意切歌 (当前门槛: {threshold}人参与, 需Y>N)")
        self._task = asyncio.create_task(self._vote_loop(on_pass))

    async def _vote_loop(self, on_pass):
        """投票循环：定期广播，到期统计。"""
        start = time.time()
        last_broadcast = 0
        while time.time() - start < VOTE_DURATION:
            elapsed = time.time() - start
            if elapsed - last_broadcast >= VOTE_BROADCAST_INTERVAL:
                last_broadcast = elapsed
                remaining = int(VOTE_DURATION - elapsed)
                await self.sender.send(
                    f"[切歌投票] Y/N 剩余{remaining}s | 当前 Y:{len(self.yes_votes)} N:{len(self.no_votes)}"
                )
            await asyncio.sleep(1)
        # 投票结束，统计
        self.active = False
        yes_count = len(self.yes_votes)
        no_count = len(self.no_votes)
        total = yes_count + no_count
        threshold = self.get_threshold()
        await self.sender.send(
            f"[投票结束] Y:{yes_count} N:{no_count} 总参与:{total} 门槛:{threshold}"
        )
        if yes_count > no_count and total >= threshold:
            # 检查投票目标歌曲是否仍在播放（防止歌曲自然结束后切到下一首）
            if self._is_still_playing and not self._is_still_playing():
                await self.sender.send("[投票通过] 但原歌曲已播放完毕，继续播放下一首")
            else:
                await self.sender.send("[投票通过] 正在切歌...")
                await on_pass()
        else:
            await self.sender.send("[投票未通过] 继续播放当前歌曲")

    def handle_vote(self, uid: int, vote: str):
        """处理观众的投票。"""
        if not self.active:
            return
        if uid in self.voters:
            return
        self.voters.add(uid)
        vote = vote.strip().upper()
        if vote in ("Y", "YES", "1"):
            self.yes_votes.add(uid)
        elif vote in ("N", "NO", "0"):
            self.no_votes.add(uid)


# ======================== 播放队列 ========================
@dataclass
class QueueItem:
    song: SongInfo
    url: str = ""  # 可选：为空时在播放时按音质降级获取
    requester: str = ""

class PlayQueue:
    """FIFO 播放队列。"""
    def __init__(self):
        self._items: deque = deque()

    def add(self, item: QueueItem):
        self._items.append(item)
        return len(self._items)

    def next(self) -> Optional[QueueItem]:
        if self._items:
            return self._items.popleft()
        return None

    def peek(self) -> Optional[QueueItem]:
        if self._items:
            return self._items[0]
        return None

    def remove_current(self):
        """移除当前播放的歌曲（队首）。"""
        if self._items:
            self._items.popleft()

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def is_empty(self) -> bool:
        return len(self._items) == 0


# ======================== 主机器人 ========================
class BiliMusicBot:
    """B站直播间点歌机器人主控。"""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.sender: Optional[BiliSender] = None
        self.danmaku: Optional[BiliDanmaku] = None
        self.playback = PlaybackManager(self.session if self.session else None)
        self.queue = PlayQueue()
        self.cookie_mgr = CookieManager()
        self.vote = VoteManager(self.sender)  # 会被更新
        self._playing = False
        self._room_live = True

    async def run(self):
        self.session = aiohttp.ClientSession()
        self.playback.set_session(self.session)
        # 获取 buvid3（B站风控必需）：优先使用 config 配置
        if BILI_BUVID3_CFG:
            log.info(f"使用 config.json 中的 bili_buvid3: {BILI_BUVID3_CFG[:16]}...")
            update_buvid3(BILI_BUVID3_CFG)
        else:
            log.info("config.json 未配置 bili_buvid3，尝试自动获取...")
            buvid3 = await fetch_buvid3(self.session)
            update_buvid3(buvid3)
        # 启动时校验 Cookie
        cookie_ok = await self.cookie_mgr.validate(self.session)
        if not cookie_ok:
            log.error("Cookie 无效，弹幕发送功能不可用。机器人仍可监听弹幕和播放音乐")
            log.error("请更新 config.json 中的 bili_sessdata 和 bili_jct，保存后自动热更新")
        self.sender = BiliSender(self.session, self.cookie_mgr)
        self.vote = VoteManager(self.sender)
        self.danmaku = BiliDanmaku(
            ROOM_ID,
            on_message=self._on_danmaku,
            on_viewer_count=self._on_viewer_count,
            session=self.session,
        )
        # 启动 Cookie 热更新检查
        cookie_task = asyncio.create_task(self._cookie_check_loop())
        # 启动房间状态检查
        room_task = asyncio.create_task(self._room_check_loop())
        # 启动队列消费
        queue_task = asyncio.create_task(self._queue_loop())
        # 启动弹幕监听
        danmaku_task = asyncio.create_task(self.danmaku.start())
        if cookie_ok:
            await self.sender.send("点歌机器人已上线！发送 /play 歌名 歌手 来点歌")
        try:
            await asyncio.gather(room_task, queue_task, danmaku_task, cookie_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            room_task.cancel()
            queue_task.cancel()
            danmaku_task.cancel()
            cookie_task.cancel()
            await self.session.close()

    async def _on_viewer_count(self, count: int):
        self.vote.update_viewer_count(count)

    async def _on_danmaku(self, text: str, uid: int, username: str):
        """处理弹幕消息。"""
        text = text.strip()
        if not text:
            return
        # 投票处理
        if self.vote.active and text.upper() in ("Y", "N", "YES", "NO"):
            self.vote.handle_vote(uid, text[0])
            return
        # /play 指令
        if text.lower().startswith("/play"):
            query = text[5:].strip()
            if not query:
                await self.sender.send("格式: /play 歌名 (可选歌手，如 /play 告白气球 周杰伦)")
                return
            # 尝试用分隔符拆分歌名和歌手
            song_name, singer = "", ""
            for sep in [" - ", " — ", " – ", " by "]:
                if sep in query:
                    parts = query.split(sep, 1)
                    song_name = parts[0].strip()
                    singer = parts[1].strip()
                    break
            if not song_name:
                # 无分隔符：整个查询作为歌名（支持多词英文歌名如 "see you again"）
                song_name = query
                singer = ""
            asyncio.create_task(self._handle_play(song_name, singer, username))
        # /switch 指令
        elif text.lower().startswith("/switch"):
            if self.playback.is_playing and not self.vote.active:
                asyncio.create_task(self._handle_switch())
            elif self.vote.active:
                await self.sender.send("投票进行中，请发送 Y 或 N 参与投票")
            else:
                await self.sender.send("当前没有在播放的歌曲")

    async def _handle_play(self, song_name: str, singer: str, requester: str):
        """处理点歌请求。搜索成功后直接入队，URL在播放时按音质降级获取。"""
        await self.sender.send(f"正在搜索: {song_name} {singer}".strip())
        try:
            song = await search_song(self.session, song_name, singer)
            if not song:
                search_kw = f"{song_name} {singer}".strip() if singer else song_name
                if " " in search_kw:
                    no_space_kw = search_kw.replace(" ", "")
                    log.info(f"搜索失败，尝试无空格关键词: {no_space_kw}")
                    song = await search_song(self.session, no_space_kw, "")
            if not song:
                await self.sender.send(f"未找到: {song_name} {singer}".strip())
                return
            item = QueueItem(song=song, requester=requester)
            pos = self.queue.add(item)
            platform_name = PLATFORM_NAMES.get(song.platform, song.platform)
            await self.sender.send(f"已点: {_fmt_song(song)} (队列:{pos})")
        except Exception as e:
            log.error(f"点歌异常: {e}")
            await self.sender.send(f"点歌失败: {e}")

    async def _handle_switch(self):
        """处理切歌投票。"""
        target = self.playback.current_song
        target_id = target.song_id if target else None
        if target_id:
            is_still_playing = lambda: (
                self.playback.current_song is not None
                and self.playback.current_song.song_id == target_id
            )
        else:
            is_still_playing = lambda: self.playback.current_song is not None
        await self.vote.start_vote(on_pass=self._skip_current, is_still_playing=is_still_playing)

    async def _skip_current(self):
        """跳过当前歌曲。"""
        self.playback.stop()
        await self.sender.send("已切歌")

    async def _queue_loop(self):
        """队列消费循环：播放队列中的歌曲。"""
        while True:
            if self.queue.is_empty:
                await asyncio.sleep(1)
                continue
            item = self.queue.peek()
            if item is None:
                await asyncio.sleep(1)
                continue
            # "正在播放"弹幕通过 on_start 回调在下载完成后发送，避免弹幕说"正在播放"但终端还在"正在下载"
            on_start = lambda song=item.song: self.sender.send(f"正在播放: {_fmt_song(song)}")
            self._playing = True
            if item.url:
                # 有预获取的URL，直接播放
                await self.playback.play(item.url, item.song, on_finish=self._on_song_finish, on_start=on_start)
            else:
                # 无URL，播放时按音质降级获取（下载失败自动重试低音质）
                await self._play_with_fallback(item.song, on_start=on_start)
            self._playing = False

    async def _play_with_fallback(self, song: SongInfo, on_start=None):
        """并行获取所有音质URL，预验证后按音质优先级依次尝试下载播放（参考Museek波次竞速）。"""
        song_info = {
            "name": song.name,
            "singer": song.singer,
            "song_id": song.song_id,
            "album": song.album,
            "interval": song.interval,
        }
        # 并行获取所有音质的URL（而非逐个降级，大幅缩短等待时间）
        log.info(f"并行获取音质: {', '.join(QUALITY_LADDER)}")
        tasks = {}
        for quality in QUALITY_LADDER:
            tasks[quality] = asyncio.create_task(
                _bridge.get_url(song.platform, song_info, quality)
            )

        # 等待所有URL获取完成
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        # 按音质优先级整理有效URL
        url_map = {}
        for quality, result in zip(QUALITY_LADDER, results):
            if isinstance(result, Exception):
                log.debug(f"获取链接异常 ({quality}): {result}")
                continue
            if result and isinstance(result, str) and result.startswith("http"):
                url_map[quality] = result
                log.info(f"获取成功 ({quality}): {result[:80]}...")

        if not url_map:
            log.error(f"所有音质均无法获取URL: {song.name} - {song.singer}")
            await self._on_song_finish(False)
            return

        # 检测是否全部为代理 URL（.php?id= 模式），代理 URL 已知返回 0 字节
        all_proxy = all(".php?" in url and "id=" in url for url in url_map.values())
        if all_proxy:
            log.error(f"所有URL均为代理URL（无法下载）: {song.name} - {song.singer} [{PLATFORM_NAMES.get(song.platform, song.platform)}]")
            await self._on_song_finish(False)
            return

        # 并行预验证所有URL（GET Range 请求检查实际数据内容，参考Museek looksLikeRealAudio）
        validate_tasks = {}
        for quality, url in url_map.items():
            validate_tasks[quality] = asyncio.create_task(
                validate_audio_url(self.session, url, song.platform)
            )
        validate_results = await asyncio.gather(*validate_tasks.values(), return_exceptions=True)
        validated = {}
        for quality, result in zip(validate_tasks.keys(), validate_results):
            if isinstance(result, Exception):
                validated[quality] = True  # 验证异常时 fallback 为通过
            else:
                validated[quality] = result
            if not validated[quality]:
                log.info(f"URL 预验证失败 ({quality})，跳过该音质")

        # 按音质优先级依次尝试下载播放（跳过预验证失败的URL）
        for quality in QUALITY_LADDER:
            url = url_map.get(quality)
            if not url or not validated.get(quality, True):
                continue
            played = await self.playback.play(url, song, on_finish=self._on_song_finish, on_start=on_start)
            if played:
                return  # 播放已尝试（成功/失败/切歌），on_finish已调用
            log.warning(f"音质 {quality} 下载失败，尝试下一音质...")

        # 回退：如果所有预验证都失败，直接尝试下载最高音质（不经验证）
        # 某些代理 URL 可能无法通过 GET Range 验证但实际可正常下载
        if not any(validated.values()):
            log.info(f"所有URL预验证均失败，尝试直接下载（跳过验证）...")
            for quality in QUALITY_LADDER:
                url = url_map.get(quality)
                if not url:
                    continue
                played = await self.playback.play(url, song, on_finish=self._on_song_finish, on_start=on_start)
                if played:
                    return
                log.warning(f"音质 {quality} 直接下载失败，尝试下一音质...")

        # 所有音质都失败
        log.error(f"所有音质均无法播放: {song.name} - {song.singer}")
        await self._on_song_finish(False)

    async def _on_song_finish(self, success: bool = True, intentional: bool = False):
        """歌曲播放完成回调。intentional=True表示被切歌（"已切歌"已发送，不重复通知）。"""
        item = self.queue.peek()
        if not self.queue.is_empty:
            self.queue.remove_current()
        if item:
            if intentional:
                pass  # 切歌时"已切歌"已发送，不重复通知
            elif success:
                await self.sender.send(f"播放完毕: {_fmt_song(item.song)}")
            else:
                await self.sender.send(f"播放失败: {_fmt_song(item.song)}")

    async def _room_check_loop(self):
        """定期检查直播间状态。"""
        while True:
            await asyncio.sleep(ROOM_CHECK_INTERVAL)
            try:
                await _api_limiter.acquire()
                async with self.session.get(BILI_ROOM_INFO, headers=BILI_HEADERS) as resp:
                    data = await resp.json()
                    if data.get("code") == 0:
                        live_status = data["data"].get("live_status", 0)
                        if live_status == 0:
                            log.warning("直播间已关闭，程序退出")
                            self._room_live = False
                            # 停止所有任务
                            if self.danmaku:
                                await self.danmaku.stop()
                            self.playback.stop()
                            return
                        else:
                            log.debug(f"直播间状态正常 (live_status={live_status})")
                    else:
                        log.warning(f"获取房间信息失败: {data.get('message', '')}")
            except Exception as e:
                log.warning(f"房间状态检查异常: {e}")

    async def _cookie_check_loop(self):
        """定期检查 config.json 变更（热更新 Cookie）。"""
        while True:
            await asyncio.sleep(10)  # 每10秒检查一次
            try:
                if self.cookie_mgr.check_hot_reload():
                    self.cookie_mgr.reload_config()
                    if self.cookie_mgr.valid:
                        # Cookie 恢复后发送一条测试弹幕
                        await self.sender.send("Cookie 已更新，弹幕发送恢复")
            except Exception as e:
                log.debug(f"Cookie 热更新检查异常: {e}")


# ======================== 入口 ========================
async def main():
    global REAL_ROOM_ID, _bridge
    log.info("=" * 50)
    log.info("B站直播间点歌机器人 启动")
    log.info(f"房间号: {ROOM_ID}")
    # 打印 Cookie 过期信息
    cm = CookieManager()
    log.info(cm.check_expiry())
    log.info("=" * 50)
    # 启动 Node.js 音源桥接器
    _bridge = SourceBridge()
    await _bridge.start()
    # 获取真实房间号
    async with aiohttp.ClientSession() as session:
        REAL_ROOM_ID = await get_real_room_id(session)
    bot = BiliMusicBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        log.info("收到中断信号，正在退出...")
    except Exception as e:
        log.error(f"运行异常: {e}")
        raise
    finally:
        if _bridge:
            await _bridge.stop()
        if bot.session:
            await bot.session.close()
        log.info("程序已退出")


if __name__ == "__main__":
    asyncio.run(main())
