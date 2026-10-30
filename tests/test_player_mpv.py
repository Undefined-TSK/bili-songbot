#!/usr/bin/env python3
"""S2 播放层适配器测试：真 libmpv 本地播放（ao=null，无声卡走真实解码链路）。

覆盖：接口对齐 / 本地文件播放全流程 / stop 切歌 intentional 语义 /
下载失败返回 False / mpv 加载失败 / 连播两首实例复用（gaeless 就绪）。
用法: python3 tests/test_player_mpv.py
前置: pip install python-mpv + 系统 libmpv（apt install libmpv2）
"""
import asyncio
import os
import struct
import sys
import tempfile
import unittest
import wave
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import mpv  # noqa: F401
    from bot.models import SongInfo
    from bot import player_mpv
    from bot.player_mpv import PlaybackManager
    MPV_AVAILABLE = True
except Exception:
    MPV_AVAILABLE = False


def _make_wav(path: str, seconds: float, freq: int = 440):
    """生成正弦波测试音频（零外部依赖，不依赖 ffmpeg）。"""
    rate = 44100
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(12000 * ((i * freq // rate) % 2 * 2 - 1)))
                          for i in range(int(rate * seconds)))
        w.writeframes(frames)


class _Recorder:
    """记录 on_start/on_finish 调用（async 回调，与 core.py 用法一致）。"""

    def __init__(self):
        self.started = 0
        self.finished = []  # (success, intentional)

    async def on_start(self):
        self.started += 1

    async def on_finish(self, success, intentional=False):
        self.finished.append((success, intentional))


@unittest.skipUnless(MPV_AVAILABLE, "python-mpv/libmpv 不可用")
class TestMpvPlayback(IsolatedAsyncioTestCase):
    """真 mpv 播放（ao=null）验证完整播放流程。"""

    NULL_AO = {"ao": "null", "vo": "null"}

    async def asyncSetUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="bot_mpv_test_")
        self.wav1 = os.path.join(self.tmp_dir, "song1.wav")   # 1 秒
        self.wav2 = os.path.join(self.tmp_dir, "song2.wav")   # 2 秒
        _make_wav(self.wav1, 1.0)
        _make_wav(self.wav2, 2.0)
        self.song = SongInfo(name="测试歌", singer="测试歌手", song_id="T001")

    async def asyncTearDown(self):
        # 清掉各用例可能残留的播放器实例
        for attr in ("pm",):
            pm = getattr(self, attr, None)
            if pm is not None:
                pm.close()

    def _new_pm(self):
        pm = PlaybackManager(session=None, mpv_options=self.NULL_AO)
        self.pm = pm  # tearDown 统一 close
        return pm

    async def test_interface_alignment(self):
        """与 ffplay 版 PlaybackManager 同接口（core.py 改 import 即可用）。"""
        pm = PlaybackManager()
        self.assertIsNone(pm.process)          # 兼容属性
        self.assertIsNone(pm.current_song)
        self.assertIsNone(pm.session)
        self.assertFalse(pm.is_playing)
        # play 为 async；stop/set_session/close 为同步（与 ffplay 版一致）
        self.assertTrue(asyncio.iscoroutinefunction(pm.play))
        self.assertFalse(asyncio.iscoroutinefunction(pm.stop))
        self.assertFalse(asyncio.iscoroutinefunction(pm.set_session))
        self.assertFalse(asyncio.iscoroutinefunction(pm.close))

    async def test_play_local_file_full_chain(self):
        """播放本地文件：on_start 先于播放 -> 播完 -> on_finish(success=True)。"""
        pm = self._new_pm()
        rec = _Recorder()
        ok = await pm.play(self.wav1, self.song, on_finish=rec.on_finish, on_start=rec.on_start)
        self.assertTrue(ok, "play 应返回 True（已尝试）")
        self.assertEqual(rec.started, 1, "on_start 应被调用一次")
        self.assertEqual(rec.finished, [(True, False)], "自然播完应为 (success=True, intentional=False)")
        self.assertFalse(pm.is_playing, "播完后 is_playing 应为 False")
        self.assertIsNone(pm.current_song, "播完后 current_song 应清空")

    async def test_current_song_visible_during_playback(self):
        """播放中 current_song 可读（core.py 切歌投票依赖）。"""
        pm = self._new_pm()
        rec = _Recorder()
        task = asyncio.create_task(
            pm.play(self.wav2, self.song, on_finish=rec.on_finish, on_start=rec.on_start))
        await asyncio.sleep(0.4)
        self.assertTrue(pm.is_playing, "2 秒歌播放 0.4s 时应仍在播放")
        self.assertIs(pm.current_song, self.song, "播放中 current_song 应为当前歌")
        pm.stop()
        await task
        self.assertEqual(rec.finished, [(True, True)], "stop 切歌应为 (True, intentional=True)")
        self.assertIsNone(pm.current_song)

    async def test_stop_is_intentional(self):
        """stop() 中途停止：on_finish(intentional=True)，不误报播放失败。"""
        pm = self._new_pm()
        rec = _Recorder()
        task = asyncio.create_task(
            pm.play(self.wav2, self.song, on_finish=rec.on_finish, on_start=rec.on_start))
        await asyncio.sleep(0.4)
        pm.stop()
        ok = await task
        self.assertTrue(ok)
        self.assertEqual(rec.finished, [(True, True)])

    async def test_download_failure_returns_false(self):
        """下载失败：返回 False 且不调用 on_finish（调用方重试低音质，对齐 ffplay 版）。"""
        pm = self._new_pm()
        pm.session = object()  # 占位 session：走下载分支（download 被 patch，不发起真实请求）
        rec = _Recorder()

        async def _none_dl(*a, **k):
            return None

        old_dl, old_bridge = player_mpv.download_audio, player_mpv.download_audio_via_bridge
        player_mpv.download_audio = _none_dl
        player_mpv.download_audio_via_bridge = _none_dl
        try:
            ok = await pm.play("http://fake.invalid/x.mp3", self.song,
                               on_finish=rec.on_finish, on_start=rec.on_start)
        finally:
            player_mpv.download_audio = old_dl
            player_mpv.download_audio_via_bridge = old_bridge
        self.assertFalse(ok, "下载失败应返回 False")
        self.assertEqual(rec.finished, [], "下载失败不应调用 on_finish")
        self.assertEqual(rec.started, 0, "下载失败不应调用 on_start")

    async def test_mpv_load_failure(self):
        """mpv 加载失败（文件不存在）：END_FILE reason=error -> on_finish(success=False)。"""
        pm = self._new_pm()
        rec = _Recorder()
        missing = os.path.join(self.tmp_dir, "no_such_file.mp3")
        ok = await pm.play(missing, self.song, on_finish=rec.on_finish, on_start=rec.on_start)
        self.assertTrue(ok, "已起播尝试应返回 True")
        self.assertEqual(rec.finished, [(False, False)], "加载失败应为 (False, False)")

    async def test_consecutive_songs_reuse_instance(self):
        """连播两首：mpv 常驻实例复用（gapless 就绪），两次 on_finish 均正确。"""
        pm = self._new_pm()
        rec = _Recorder()
        song2 = SongInfo(name="测试歌2", singer="测试歌手", song_id="T002")

        await pm.play(self.wav1, self.song, on_finish=rec.on_finish, on_start=rec.on_start)
        first_instance = pm._mpv
        self.assertIsNotNone(first_instance)

        await pm.play(self.wav2, song2, on_finish=rec.on_finish, on_start=rec.on_start)
        self.assertIs(pm._mpv, first_instance, "第二首应复用同一 mpv 实例（常驻设计）")

        self.assertEqual(rec.started, 2)
        self.assertEqual(rec.finished, [(True, False), (True, False)])

    async def test_stop_then_play_next(self):
        """切歌流（对齐 core.py _skip_current -> stop -> 播下一首）：先 intentional 结算再播新歌。"""
        pm = self._new_pm()
        rec = _Recorder()
        song2 = SongInfo(name="测试歌2", singer="测试歌手", song_id="T002")

        task = asyncio.create_task(
            pm.play(self.wav2, self.song, on_finish=rec.on_finish, on_start=rec.on_start))
        await asyncio.sleep(0.4)
        pm.stop()
        await task
        self.assertEqual(rec.finished, [(True, True)], "旧歌应 intentional 结算")

        await pm.play(self.wav1, song2, on_finish=rec.on_finish, on_start=rec.on_start)
        self.assertEqual(rec.finished, [(True, True), (True, False)], "新歌应自然播完结算")


class TestBackendSelection(unittest.TestCase):
    """core._make_playback_manager 按 config.player_backend 选择后端。"""

    def test_default_ffplay(self):
        from bot import core
        from bot.player import PlaybackManager as Ffplay
        with patch.object(core, "PLAYER_BACKEND", "ffplay"):
            pm = core._make_playback_manager(None)
        self.assertIsInstance(pm, Ffplay)

    @unittest.skipUnless(MPV_AVAILABLE, "python-mpv/libmpv 不可用")
    def test_mpv_selected_when_configured(self):
        from bot import core
        with patch.object(core, "PLAYER_BACKEND", "mpv"):
            pm = core._make_playback_manager(None)
        self.assertIsInstance(pm, PlaybackManager)


if __name__ == '__main__':
    unittest.main(verbosity=2)
