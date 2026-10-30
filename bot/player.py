"""播放与下载：URL 预验证、音频下载（含 Node 桥兜底）、ffplay 子进程管理。"""
import asyncio
import os
import subprocess
import tempfile
from typing import Optional

import aiohttp

from .bridge import get_bridge
from .config import FFPLAY_ARGS, FFPLAY_PATH, log
from .models import SongInfo
from .protocol import BILI_HEADERS, FULL_UA


def _new_tmp_path() -> str:
    """在 bot/tmp/ 下创建临时 .mp3 文件并返回路径（调用方负责写入/删除）。"""
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=tmp_dir)
    tmp_path = tmp.name
    tmp.close()
    return tmp_path


def _safe_unlink(path: Optional[str]):
    """删除临时文件，忽略一切异常（文件不存在/占用都不应炸掉主流程）。"""
    if not path:
        return
    try:
        os.unlink(path)
    except Exception:
        pass


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
    tmp_path = _new_tmp_path()
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30),
                                allow_redirects=True) as resp:
            if resp.status >= 400 or resp.status < 200:
                log.warning(f"下载音频失败: HTTP {resp.status}")
                _safe_unlink(tmp_path)
                return None
            with open(tmp_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)
        size = os.path.getsize(tmp_path)
        if size < 1024:
            log.warning(f"音频文件过小 ({size} bytes)，可能不是有效音频")
            _safe_unlink(tmp_path)
            return None
        log.info(f"音频下载完成: {size // 1024} KB")
        return tmp_path
    except Exception as e:
        log.warning(f"下载音频异常: {e}")
        _safe_unlink(tmp_path)
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

    tmp_path = _new_tmp_path()
    try:
        size = await get_bridge().download(url, tmp_path)
        if size and size > 0:
            log.info(f"桥接器下载完成: {size // 1024} KB")
            return tmp_path
        else:
            log.warning(f"桥接器下载失败: 无数据")
            _safe_unlink(tmp_path)
            return None
    except Exception as e:
        log.warning(f"桥接器下载异常: {e}")
        _safe_unlink(tmp_path)
        return None


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
                _safe_unlink(tmp_path)
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
            _safe_unlink(self._tmp_file)
            self._tmp_file = None

    @property
    def is_playing(self) -> bool:
        return self.process is not None
