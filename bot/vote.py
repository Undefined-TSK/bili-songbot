"""直播间投票：切歌投票的动态门槛、uid 去重、超时自动结算。

sender 为 danmaku.BiliSender 实例（依赖注入，不 import 以避免循环依赖）。
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Callable, Optional

from .config import VOTE_BROADCAST_INTERVAL, VOTE_DURATION, log

if TYPE_CHECKING:
    from .danmaku import BiliSender


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
