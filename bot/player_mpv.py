"""播放管理（python-mpv 实现）：与 player.PlaybackManager 同接口的替代实现。

S2 切片产物：用 libmpv 常驻实例替换"每首歌 spawn 一个 ffplay 子进程"的播放方式。

与 ffplay 版的行为对齐/差异：
- 对齐：下载（aiohttp + Node 桥兜底）→ on_start → 播放 → on_finish(success, intentional)
  的完整流程；下载失败返回 False（调用方重试低音质）；已起播但播放失败返回 True。
- 差异1：播放器实例常驻（idle 模式），切歌不销毁重建——gapless 就绪，
  播放器生命周期与 bot 进程一致而非每首歌一个子进程。
- 差异2：播放成败判定从"ffplay 进程 returncode"变为"mpv END_FILE reason"
  （eof=自然播完/stop=主动停止/error=加载解码失败）。
- 差异3：session 为 None 时 ffplay 版直接用 URL，mpv 版同样 loadfile(url)
  （mpv 原生支持 http(s)/m3u8，此为附带能力）。

切换方式（用户实测 S2 后）：core.py 中
    from .player import PlaybackManager
 改为
    from .player_mpv import PlaybackManager

依赖：pip install python-mpv；系统 libmpv
  - Windows: libmpv.dll（随 mpv 安装或单独放置，需在 PATH/ DLL 搜索路径）
  - Linux:   apt install libmpv2
"""
import asyncio
from typing import Optional

import mpv

from .config import MPV_OPTIONS, log
from .models import SongInfo
from .player import _safe_unlink, download_audio, download_audio_via_bridge

# mpv END_FILE reason(bytes) -> (success, intentional)
# 参考 mpv 文档：EOF=自然结束, STOP=stop命令, QUIT=实例终止, ERROR=加载/解码失败,
# REPLACE/REDIRECT=被新文件替换
_REASON_MAP = {
    b"eof": (True, False),        # 自然播完
    b"stop": (True, True),        # stop() 主动停止（切歌/跳过）
    b"quit": (True, True),        # 实例终止
    b"replace": (True, True),     # 被新文件替换（本适配器串行切歌，防御性映射）
    b"reredirect": (True, True),
    b"redirect": (True, True),
    b"error": (False, False),     # 加载/解码失败
}


class PlaybackManager:
    """mpv 播放管理（与 player.PlaybackManager 同接口，core.py 无需改调用代码）。"""

    BACKEND = "mpv"

    def __init__(self, session=None, mpv_options: dict = None):
        self.process = None  # 兼容属性：手写/ffplay 版持有子进程，此处置 None
        self.current_song: Optional[SongInfo] = None
        self.session = session
        self._mpv: Optional[mpv.MPV] = None
        self._mpv_options = mpv_options if mpv_options is not None else MPV_OPTIONS
        self._intentional_stop = False  # 标记主动停止（切歌/停止）
        self._playing = False
        self._tmp_file: Optional[str] = None
        self._end_future: Optional[asyncio.Future] = None  # 当前歌的 END_FILE 等待

    def set_session(self, session):
        self.session = session

    # ------------------------------------------------------------------ mpv 实例

    def _ensure_mpv(self) -> mpv.MPV:
        """懒加载常驻 mpv 实例（播完驻留 idle，切歌复用）。"""
        if self._mpv is None:
            self._mpv = mpv.MPV(
                video=False,                   # 纯音频播放
                idle=True,                     # 播放列表空时驻留，不退出
                input_default_bindings=False,  # 不抢全局快捷键
                input_vo_keyboard=False,
                **self._mpv_options,           # 测试可注入 ao=null 等
            )

            @self._mpv.event_callback("end-file")
            def _on_end_file(event):
                # 此回调在 mpv 事件线程执行，需桥回 play() 所在 asyncio loop
                fut = self._end_future
                if fut is None or fut.done():
                    return
                reason = event.as_dict().get("reason")
                try:
                    fut.get_loop().call_soon_threadsafe(_resolve_if_pending, fut, reason)
                except RuntimeError:
                    pass  # loop 已关闭（进程退出中）
        return self._mpv

    # ------------------------------------------------------------------ 播放

    async def play(self, url: str, song: SongInfo, on_finish=None, on_start=None) -> bool:
        """下载并播放音频。返回 False 表示下载失败（未调用 on_finish，可重试），True 表示已尝试。
        on_start: 下载完成、起播前的回调（用于发送"正在播放"弹幕）。"""
        await self._settle_previous()  # 结算上一首（对齐 ffplay 版 play 开头的 stop()）
        self._intentional_stop = False  # 重置：上一首的标记已随其 on_finish 消费
        self.current_song = song
        success = False
        tmp_path = None
        try:
            # 1. 下载音频到临时文件（与 ffplay 版同一套下载/兜底逻辑）
            if self.session:
                log.info(f"正在下载: {song.name} - {song.singer}")
                tmp_path = await download_audio(self.session, url, song.platform)
                if not tmp_path:
                    log.info("aiohttp 下载失败，尝试桥接器下载...")
                    tmp_path = await download_audio_via_bridge(url)
                if not tmp_path:
                    log.error(f"音频下载失败: {song.name} - {song.singer}")
                    return False  # 下载失败，不调用 on_finish，让调用方重试
                self._tmp_file = tmp_path
                play_target = tmp_path
            else:
                # 回退：直接用 URL（mpv 原生支持 http(s)/hls）
                play_target = url

            # 2. 起播（常驻实例 loadfile）
            if on_start:
                await on_start()
            log.info(f"开始播放: {song.name} - {song.singer}")
            await self._play_until_end(play_target, song)
            success = self._last_success
        except Exception as e:
            log.error(f"播放异常: {e}")
            success = False
        finally:
            intentional = self._intentional_stop  # 捕获，下面会重置
            self.current_song = None
            self._playing = False
            self._intentional_stop = False
            if tmp_path:
                _safe_unlink(tmp_path)
                self._tmp_file = None
        if on_finish:
            await on_finish(success, intentional=intentional)
        return True

    async def _play_until_end(self, target: str, song: SongInfo):
        """loadfile 起播并等待 END_FILE，按 reason 分类记录成败（对齐 ffplay returncode 语义）。"""
        m = self._ensure_mpv()
        loop = asyncio.get_event_loop()
        self._end_future = loop.create_future()
        self._last_success = False
        self._playing = True
        m.loadfile(target)
        reason = await self._end_future
        success, intentional = _REASON_MAP.get(reason, (False, False))
        self._last_success = success
        if intentional:
            log.info(f"主动停止播放: {song.name} - {song.singer}")
        elif success:
            log.info(f"播放完成: {song.name} - {song.singer}")
        else:
            log.error(f"播放失败 (END_FILE reason={reason}): {song.name} - {song.singer}")

    async def _settle_previous(self):
        """停掉上一首并等待其 END_FILE 结算（对齐 ffplay 版 play() 开头的 self.stop()）。"""
        if self._end_future is None:
            return
        self._intentional_stop = True
        self._request_stop()
        try:
            await asyncio.shield(self._end_future)
        except Exception:
            pass
        self._end_future = None

    # ------------------------------------------------------------------ 停止

    def stop(self):
        """停止当前播放（同步，对齐 ffplay 版）。END_FILE 结算与 on_finish 在 play() 协程内完成。"""
        self._intentional_stop = True
        self._request_stop()

    def _request_stop(self):
        if self._playing and self._mpv is not None:
            try:
                self._mpv.command("stop")
            except Exception as e:
                log.debug(f"mpv stop 异常（忽略）: {e}")

    # ------------------------------------------------------------------ 播放控制

    def pause(self) -> bool:
        if self._mpv is None or not self.is_playing:
            return False
        try:
            self._mpv.pause = True
            return True
        except Exception as e:
            log.debug(f"mpv 暂停失败（忽略）: {e}")
            return False

    def resume(self) -> bool:
        if self._mpv is None or not self.is_playing:
            return False
        try:
            self._mpv.pause = False
            return True
        except Exception as e:
            log.debug(f"mpv 恢复失败（忽略）: {e}")
            return False

    def set_volume(self, volume: int) -> int:
        try:
            volume = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            return 100
        if self._mpv is not None:
            try:
                self._mpv.volume = volume
            except Exception as e:
                log.debug(f"mpv 音量设置失败（忽略）: {e}")
        return volume

    def _prop(self, name: str, default=None):
        if self._mpv is None:
            return default
        try:
            value = getattr(self._mpv, name)
            return default if value is None else value
        except Exception:
            return default

    def state(self) -> dict:
        paused = bool(self._prop("pause", False))
        return {
            "backend": self.BACKEND,
            "playing": self.is_playing,
            "paused": paused,
            "position": round(float(self._prop("time_pos", 0.0) or 0.0), 2),
            "duration": round(float(self._prop("duration", 0.0) or 0.0), 2),
            "volume": int(self._prop("volume", 100) or 100),
            "capabilities": {"pause": True, "resume": True, "volume": True, "seek": True},
        }

    # ------------------------------------------------------------------ 状态

    @property
    def is_playing(self) -> bool:
        return self._playing and self._end_future is not None and not self._end_future.done()

    @property
    def is_paused(self) -> bool:
        return bool(self._prop("pause", False))

    def close(self):
        """销毁常驻实例（bot 退出时调用；stop() 只停当前歌，不销毁实例）。"""
        self._request_stop()
        if self._mpv is not None:
            try:
                self._mpv.terminate()
            except Exception as e:
                log.debug(f"mpv terminate 异常（忽略）: {e}")
            self._mpv = None


def _resolve_if_pending(fut: asyncio.Future, reason):
    """事件线程 -> asyncio loop 的安全兑现（future 可能已被取消）。"""
    if not fut.done():
        fut.set_result(reason)
