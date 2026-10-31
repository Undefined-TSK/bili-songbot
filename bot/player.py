"""播放与下载：URL 预验证、音频下载（含 Node 桥兜底）、ffplay 子进程管理。"""
import asyncio
import os
import signal
import subprocess
import tempfile
import time
from typing import Optional

import aiohttp

from .bridge import get_bridge
from .config import FFPLAY_ARGS, FFPLAY_PATH, log
from .models import SongInfo
from .protocol import FULL_UA


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
            log.warning("桥接器下载失败: 无数据")
            _safe_unlink(tmp_path)
            return None
    except Exception as e:
        log.warning(f"桥接器下载异常: {e}")
        _safe_unlink(tmp_path)
        return None


def _parse_duration(value) -> float:
    """把歌曲时长（"04:29" / "269" / 269）解析为秒；无法解析返回 0。"""
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    try:
        if ":" in text:
            parts = [float(p) for p in text.split(":")]
            total = 0.0
            for p in parts:
                total = total * 60 + p
            return max(0.0, total)
        return max(0.0, float(text))
    except (TypeError, ValueError):
        return 0.0


def _suspend_process(proc) -> bool:
    """挂起子进程（Windows 用 NtSuspendProcess，POSIX 用 SIGSTOP）。"""
    if proc is None or proc.poll() is not None:
        return False
    try:
        if os.name == "nt":
            import ctypes
            handle = ctypes.c_void_p(int(proc._handle))
            ctypes.windll.ntdll.NtSuspendProcess(handle)
        else:
            os.kill(proc.pid, signal.SIGSTOP)
        return True
    except Exception as e:
        log.debug(f"挂起播放进程失败（忽略）: {e}")
        return False


def _resume_process(proc) -> bool:
    """恢复子进程（Windows 用 NtResumeProcess，POSIX 用 SIGCONT）。"""
    if proc is None or proc.poll() is not None:
        return False
    try:
        if os.name == "nt":
            import ctypes
            handle = ctypes.c_void_p(int(proc._handle))
            ctypes.windll.ntdll.NtResumeProcess(handle)
        else:
            os.kill(proc.pid, signal.SIGCONT)
        return True
    except Exception as e:
        log.debug(f"恢复播放进程失败（忽略）: {e}")
        return False


class PlaybackManager:
    """音频播放管理。先下载到临时文件，再用 ffplay 播放。

    额外提供暂停/恢复/音量/进度（ffplay 无原生接口，用进程挂起 + 带 `-ss`
    重启实现；所有操作失败都只影响对应能力，不影响正常播放）。
    """

    BACKEND = "ffplay"

    def __init__(self, session: aiohttp.ClientSession = None):
        self.process: Optional[subprocess.Popen] = None
        self.current_song: Optional[SongInfo] = None
        self.session = session
        self._tmp_file: Optional[str] = None
        self._intentional_stop = False  # 标记主动停止（切歌/停止）
        # 进度：已累计秒数 + 本次起播时间（monotonic），暂停时冻结
        self._position_base = 0.0
        self._run_started: Optional[float] = None
        self._paused = False
        self._duration = 0.0
        self._volume = 100
        self._target: Optional[str] = None
        self._volume_restart = False

    def set_session(self, session: aiohttp.ClientSession):
        self.session = session

    async def play(self, url: str, song: SongInfo, on_finish=None, on_start=None) -> bool:
        """下载并播放音频。返回 False 表示下载失败（未调用on_finish，可重试），True 表示已播放。
        on_start: 下载完成、ffplay启动前的回调（用于发送"正在播放"弹幕）。"""
        self.stop()
        self._intentional_stop = False  # 重置：stop()的标记是给上一首歌的
        self.current_song = song
        self._duration = _parse_duration(song.interval)
        self._position_base = 0.0
        self._run_started = None
        self._paused = False
        self._target = None
        success = False
        tmp_path = None
        try:
            # 1. 下载音频到临时文件
            if self.session:
                log.info(f"正在下载: {song.name} - {song.singer}")
                tmp_path = await download_audio(self.session, url, song.platform)
                if not tmp_path:
                    # aiohttp 下载失败，尝试通过 Node.js 桥接器下载（解决代理 URL 0 字节问题）
                    log.info("aiohttp 下载失败，尝试桥接器下载...")
                    tmp_path = await download_audio_via_bridge(url)
                if not tmp_path:
                    log.error(f"音频下载失败: {song.name} - {song.singer}")
                    return False  # 下载失败，不调用on_finish，让调用方重试
                self._tmp_file = tmp_path
                play_target = tmp_path
            else:
                # 回退：直接用 URL（可能因缺少 headers 失败）
                play_target = url

            self._target = play_target
            # 2. 用 ffplay 播放本地文件
            self._spawn(play_target, 0.0)
            # 下载完成，即将开始播放 → 发送"正在播放"弹幕（确保弹幕状态与实际一致）
            if on_start:
                await on_start()
            log.info(f"开始播放: {song.name} - {song.singer}")
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
            self._paused = False
            self._run_started = None
            self._target = None
            # 清理临时文件
            if tmp_path:
                _safe_unlink(tmp_path)
                self._tmp_file = None
        # on_finish 在 finally 之外，仅在实际播放过（非下载失败）时调用
        if on_finish:
            await on_finish(success, intentional=intentional)
        return True

    def _build_cmd(self, target: str, start_at: float) -> list:
        """构造 ffplay 命令行：过滤已有 -volume，带上当前音量与起播位置。"""
        args = list(FFPLAY_ARGS)
        cleaned = []
        skip_next = False
        for a in args:
            if skip_next:
                skip_next = False
                continue
            if a == "-volume":
                skip_next = True
                continue
            cleaned.append(a)
        cmd = [FFPLAY_PATH] + cleaned + ["-volume", str(self._volume)]
        if start_at > 0.05:
            cmd += ["-ss", f"{start_at:.3f}"]
        cmd.append(target)
        return cmd

    def _spawn(self, target: str, start_at: float):
        self.process = subprocess.Popen(self._build_cmd(target, start_at),
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE)
        self._position_base = start_at
        self._run_started = time.monotonic()
        self._paused = False

    def _wait_process(self):
        """等待子进程退出；音量调整导致的换进程不视为播放结束，继续等待新进程。"""
        while True:
            proc = self.process
            if proc is None:
                return
            proc.wait()
            if self._volume_restart:
                self._volume_restart = False
                continue
            return

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
        self._paused = False
        self._run_started = None
        # 清理临时文件
        if self._tmp_file:
            _safe_unlink(self._tmp_file)
            self._tmp_file = None

    # ---------------------------------------------------------------- 播放控制

    def pause(self) -> bool:
        if not self.process or self._paused:
            return False
        self._position_base = self.position
        self._run_started = None
        self._paused = True
        _suspend_process(self.process)
        return True

    def resume(self) -> bool:
        if not self.process or not self._paused:
            return False
        self._paused = False
        self._run_started = time.monotonic()
        _resume_process(self.process)
        return True

    def set_volume(self, volume: int) -> int:
        """设置音量（0-100）。播放中会带 `-ss` 重启进程，保持当前进度。"""
        try:
            volume = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            return self._volume
        if volume == self._volume:
            return self._volume
        self._volume = volume
        if self.process and self._target and not self._paused:
            pos = self.position
            old = self.process
            self._volume_restart = True
            try:
                old.terminate()
            except Exception:
                try:
                    old.kill()
                except Exception:
                    pass
            self._spawn(self._target, pos)
        return self._volume

    @property
    def position(self) -> float:
        pos = self._position_base
        if self._run_started is not None:
            pos += time.monotonic() - self._run_started
        if self._duration > 0:
            pos = min(pos, self._duration)
        return max(0.0, pos)

    @property
    def is_playing(self) -> bool:
        return self.process is not None

    @property
    def is_paused(self) -> bool:
        return self._paused

    def state(self) -> dict:
        """播放器状态快照（供本地 API 渲染进度/控制按钮）。"""
        return {
            "backend": self.BACKEND,
            "playing": self.is_playing,
            "paused": self._paused,
            "position": round(self.position, 2),
            "duration": round(self._duration, 2),
            "volume": self._volume,
            "capabilities": {"pause": True, "resume": True, "volume": True, "seek": False},
        }
