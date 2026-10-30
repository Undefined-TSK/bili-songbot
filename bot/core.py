"""主控编排：BiliMusicBot——指令路由、队列消费、播放编排、Cookie 热更新循环的粘合层。"""
import asyncio
import re
from typing import List, Optional, Tuple

import aiohttp

from . import protocol
from .artist_alias import canonical_display, canonical_singer as _canon_singer
from .batch import MAX_BATCH_SONGS, batch_plans, cap_batch_queries
from .bridge import get_bridge
from .config import (BILI_BUVID3_CFG, ENABLE_LYRICS_SEARCH, MIN_QUALITY, ROOM_CHECK_INTERVAL,
                     ROOM_ID, STATS_FILE, URL_FETCH_TIMEOUT, log, reply)
from .danmaku import BiliDanmaku, BiliSender
from .lyrics import looks_like_lyric
from .models import PlayQueue, QueueItem, SongInfo
from .player import PlaybackManager, validate_audio_url
from .protocol import BILI_ROOM_INFO, CookieManager, fetch_buvid3, update_buvid3
from .search import (PLATFORM_NAMES, QUALITY_LADDER, VERSION_NOISE, _alias_match,
                     _fuzzy_match, _normalize_text, get_music_url, search_song, suggest_song)
from .stats import SongStats

_VERSION_WORDS_LOWER = {v.lower() for v in VERSION_NOISE}

# 批量点歌结构候选的验证门槛：每组子查询必须以 >= 此评分命中才算批量成立。
# 取"歌名完全匹配"档（100），防单词碎片（u/will/know 之类）靠低分乱命中。
BATCH_VERIFY_SCORE = 100


def _query_has_version(q: str) -> bool:
    """查询是否显式点名了某个版本（翻唱/remix/live...）。

    空格切分 + jieba 拆词后，任一 token 精确等于某版本词即判定为「主动要某版本」。
    用于让整句作为歌名查询、保留版本信号交给 search_song 的显式版本例外处理。
    """
    toks = [t for t in re.split(r"\s+", q) if t]
    toks += _jieba_tokens(q)
    return any(t.lower() in _VERSION_WORDS_LOWER for t in toks)
from .vote import VoteManager


def _fmt_song(song: SongInfo) -> str:
    """格式化歌名用于弹幕显示：只显示歌名，过长则用...省略。"""
    name = song.name or "未知"
    # 歌名超过10个字符则截断
    if len(name) > 10:
        return name[:5] + "..."
    return name


# 显式分隔符：用于把"歌名 歌手"拆开做精确归属。
# 不含纯空格——纯空格留给"整句部分匹配"兜底，以免破坏英文多词歌名（如 "see you again"）。
_QUERY_DELIMS = [
    " - ", " — ", " – ", " _ ", " / ", " | ", " by ", " By ", " BY ",
    "-", "—", "–", "/", "|", ",", "，", "、", ":", "：",
]

# 中日韩正则（用于识别无分隔中文查询）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 中文分词（jieba 已为项目依赖；缺失时退化为整句）
try:
    import jieba as _jieba
    _jieba.setLogLevel(20)
    _HAS_JIEBA = True
except Exception:  # pragma: no cover - 正常环境已安装
    _HAS_JIEBA = False

# URL 获取限时默认值已被 config.URL_FETCH_TIMEOUT 接管（此处保留说明）：
# 慢源/全代理源最多占用这么久，超时即用已到手结果继续，让跨源回退尽快介入
# （响应提速，源自 LXMusic 按音源可靠性调度思想）


def _jieba_tokens(s: str) -> List[str]:
    if not _HAS_JIEBA:
        return [s]
    return [t for t in _jieba.lcut(s) if t.strip()]


def split_play_query(query: str) -> List[Tuple[str, str]]:
    """把自由格式的 /play 查询拆成若干 (歌名, 歌手) 候选（含两种顺序）。

    规则：
    - 优先用显式分隔符（横杠/斜杠/逗号/by 等）切成两段，返回 (A, B) 与 (B, A) 两种顺序，
      以兼容"歌手 歌名"与"歌名 歌手"任意颠倒。
    - 无显式分隔符时：整句兜底 + （含中文时）用 jieba 拆词，把最长词当作"歌手"、其余拼接当作"歌名"，
      两种顺序都试。因此"告白气球周杰伦""周杰伦告白气球"等无分隔/颠倒写法也能精准拆出歌手。
    - 不按纯空格切，避免破坏英文多词歌名（如 "see you again"）。
    """
    q = (query or "").strip()
    if not q:
        return []
    # 显式版本词（翻唱/remix/live...）：用户主动要某版本，整句作为歌名查询，
    # 让 search_song 的「显式版本词例外」生效（不被误当歌手、也不丢版本信号）
    if _query_has_version(q):
        return [(q, "")]
    for d in _QUERY_DELIMS:
        if d in q:
            a, b = q.split(d, 1)
            a, b = a.strip(), b.strip()
            if a and b:
                return [(a, b), (b, a)]
    # 无显式分隔符：整句兜底 + 无分隔中文用 jieba 拆出"歌名/歌手"
    candidates = [(q, "")]
    if _CJK_RE.search(q):
        toks = _jieba_tokens(q)
        if len(toks) >= 2:
            # 最长 token 作为可能的歌手，其余拼接作为歌名；两种顺序都试
            longest = max(toks, key=len)
            if longest != q:
                rest = "".join(t for t in toks if t is not longest) or q
                candidates.append((rest, longest))
                candidates.append((longest, rest))
    # 混写/别名歌手容错（纯增量，追加在末尾、不改变原有候选顺序）：
    # 歌手段可归一到规范写法时（如 "迈克尔jackson" -> "Michael Jackson"），
    # 追加规范变体，提高各平台的搜索命中率；前面的原候选照旧优先尝试
    expanded = list(candidates)
    for song_name, singer in candidates:
        if singer:
            canon = canonical_display(singer)
            if canon and canon != singer:
                expanded.append((song_name, canon))
    return expanded


class BiliMusicBot:
    """B站直播间点歌机器人主控。"""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.sender: Optional[BiliSender] = None
        self.danmaku: Optional[BiliDanmaku] = None
        self.playback = PlaybackManager(self.session if self.session else None)
        self.queue = PlayQueue()
        self.stats = SongStats(STATS_FILE or None)  # 点歌统计（/top 数据源，静默降级）
        self._current_item: Optional[QueueItem] = None  # 正在播放的队列项（公共台归属判定）
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
            # 自由格式：顺序可颠倒、分隔符随意甚至无分隔，交给 split_play_query + search_song 兜底；
            # 批量点歌（bot/batch 零词典结构候选）走纯增量批量层，单曲路径不变
            if batch_plans(query):
                asyncio.create_task(self._handle_play_batch(query, username, uid))
            else:
                asyncio.create_task(self._handle_play(query, username, uid))
        # /lyric 或 /歌词 指令：主动用歌词片段点歌（片段→内核解析）
        elif text.lower().startswith("/lyric") or text.lower().startswith("/歌词"):
            if text.lower().startswith("/lyric"):
                fragment = text[6:].strip()
            else:
                fragment = text[3:].strip()
            if not fragment:
                await self.sender.send("格式: /lyric 歌词片段（如 /lyric 天青色等烟雨）")
                return
            asyncio.create_task(self._handle_lyric(fragment, username, uid))
        # /switch 指令（公共台边界：自己点的歌自己做主，直接切；别人的歌走投票）
        elif text.lower().startswith("/switch"):
            cur = self._current_item
            if (cur and self.playback.is_playing
                    and self._is_owned_by(cur, username, uid)):
                asyncio.create_task(self._skip_current(reply("switch_own")))
            elif self.playback.is_playing and not self.vote.active:
                asyncio.create_task(self._handle_switch())
            elif self.vote.active:
                await self.sender.send("投票进行中，请发送 Y 或 N 参与投票")
            else:
                await self.sender.send(reply("no_playing"))
        # ===== 交互层（纯增量）：队列查看 / 正在播放 / 撤回 / 点歌排行 / 帮助 =====
        elif text.lower().startswith("/queue") or text.startswith("/队列") or text.startswith("/点歌列表"):
            asyncio.create_task(self._handle_queue())
        elif text.lower().startswith("/now") or text.startswith("/当前") or text.startswith("/np"):
            await self._handle_now()
        elif text.lower().startswith("/remove") or text.startswith("/撤回"):
            arg = text.split(" ", 1)[1].strip() if " " in text else ""
            asyncio.create_task(self._handle_remove(arg, username))
        elif text.lower().startswith("/top") or text.startswith("/排行") or text.startswith("/点歌排行"):
            asyncio.create_task(self._handle_top())
        elif text.lower().startswith("/help") or text.startswith("/帮助") or text.startswith("/指令"):
            await self.sender.send(reply("help"))

    # ===== 交互层处理器（纯增量：只读队列/播放状态，不触碰单曲/批量/播放链路）=====

    async def _handle_queue(self):
        """查看点歌队列（含点歌人与位置）。"""
        items = self.queue.snapshot()
        if not items:
            await self.sender.send(reply("queue_empty"))
            return
        lines = [f"{i + 1}. {_fmt_song(it.song)}-{it.requester}"
                 for i, it in enumerate(items[:10])]
        await self.sender.send("队列: " + " | ".join(lines))

    async def _handle_now(self):
        """查看正在播放的歌曲（附点歌人署名）。"""
        song = self.playback.current_song
        if song is None:
            await self.sender.send(reply("no_playing"))
        else:
            requester = self._current_item.requester if self._current_item else ""
            await self.sender.send(self._playing_text(song, requester))

    async def _handle_remove(self, arg: str, username: str):
        """撤回自己点的歌（/remove 序号，序号与 /queue 展示一致；只能撤自己的）。"""
        try:
            idx = int(arg)
        except (ValueError, TypeError):
            await self.sender.send(reply("remove_bad_index"))
            return
        items = self.queue.snapshot()
        if not (1 <= idx <= len(items)):
            await self.sender.send(reply("remove_bad_index"))
            return
        item = items[idx - 1]
        if item.requester and item.requester != username:
            await self.sender.send(reply("remove_denied"))
            return
        self.queue.remove_at(idx - 1)
        await self.sender.send(reply("removed", song=_fmt_song(item.song)))

    async def _handle_top(self):
        """查看点歌排行（历史点播次数 TOP5，json 持久化、跨重启保留）。"""
        top = self.stats.top(5)
        if not top:
            await self.sender.send("还没有点歌记录，点一首歌开始攒热度吧~")
            return
        lines = [f"{i + 1}. {name} x{cnt}" for i, (name, cnt) in enumerate(top)]
        await self.sender.send(reply("top_title", n=len(top)) + " " + " | ".join(lines))

    async def _resolve_single(self, query: str) -> Optional[SongInfo]:
        """单曲搜索解析：与 _handle_play 共用的核心搜索逻辑，返回 SongInfo 或 None。

        先 split_play_query 出 (歌名, 歌手) 候选（多种顺序 + 整句兜底），
        逐候选走 search_song（自由模糊 / 别名互认 / 原版优先 / 共识 / 门禁 全部生效），
        在全部候选中取**分数最高**的解析结果（而非第一个命中）——这样「歌手 歌名」整句
        与干净拆分的候选都会参与比较，干净解析（歌名精确匹配）会胜出，避免整句污染
        导致歌名只能算"包含"而丢分。全部候选都失败再去空格整句重试。
        批量验证复用此方法，确保验证与单曲解析完全一致。
        """
        best: Optional[SongInfo] = None
        for song_name, singer in split_play_query(query):
            song = await search_song(self.session, song_name, singer)
            if song and (best is None or song.score > best.score):
                best = song
        if best is None:
            no_space = query.replace(" ", "")
            if no_space != query:
                best = await search_song(self.session, no_space, "")
        # 歌词兜底：正常搜索落空且输入"像歌词"（较长/多词）时，用片段搜索取候选 (name,singer)，
        # 再交内核 search_song 真正解析播放——把"怎么输都能搜到"推进到"凭一句歌词也能点歌"。
        # 仅建议候选，绝不自行搜源；无网络/失败时优雅降级为"未找到"，不污染正常点歌路径。
        if best is None and ENABLE_LYRICS_SEARCH and looks_like_lyric(query):
            from .lyrics import resolve_from_lyric
            try:
                best = await resolve_from_lyric(self.session, query, search_song=search_song)
            except Exception as e:
                log.debug(f"歌词兜底搜索异常: {e}")
        return best

    async def _handle_play(self, query: str, requester: str, uid: int = 0):
        """处理点歌请求。搜索成功后直接入队，URL在播放时按音质降级获取。

        解析高度自由：先按显式分隔符拆出 (歌名, 歌手) 两种顺序，再按空格拆两种顺序，
        最后整句兜底——因此"歌名 歌手 / 歌手 歌名 / 无分隔 / 颠倒"都能命中。

        公共点歌台礼仪：同一首歌已在队列/正在播放时提示不重复入队。

        返回值（纯增量，便于批量层统计，不影响既有行为）：成功返回 SongInfo，失败返回 None。
        """
        await self.sender.send(reply("searching", query=query.strip()))
        try:
            song = await self._resolve_single(query)
            if not song:
                # 智能建议（did-you-mean）：正式门禁未过时，取最接近候选只提示不播放
                try:
                    sug = await suggest_song(self.session, query)
                except Exception as e:
                    log.debug(f"智能建议失败: {e}")
                    sug = None
                if sug:
                    await self.sender.send(reply("suggest", query=query.strip(),
                                                 name=sug.name, singer=sug.singer))
                else:
                    await self.sender.send(reply("not_found", query=query.strip()))
                return None
            # 防重复：公共台同一首歌只排一次，避免热门歌被反复插队
            dup = self._find_duplicate(song)
            if dup == "playing":
                await self.sender.send(reply("duplicate_playing", song=_fmt_song(song)))
                return None
            if isinstance(dup, int):
                await self.sender.send(reply("duplicate", song=_fmt_song(song), pos=dup))
                return None
            item = QueueItem(song=song, requester=requester, requester_uid=uid)
            pos = self.queue.add(item)
            self.stats.record(song.name, song.singer, requester)
            await self.sender.send(reply("added", song=_fmt_song(song), pos=pos))
            return song
        except Exception as e:
            log.error(f"点歌异常: {e}")
            await self.sender.send(f"点歌失败: {e}")
            return None

    async def _handle_lyric(self, fragment: str, requester: str, uid: int = 0):
        """歌词点歌：用歌词片段（如"天青色等烟雨"）识别并点歌。

        片段→候选(name,singer) 由 lyrics 提供方建议，真正解析/原版优先/共识/取链播放
        全部交给内核 search_song，严守"用现有内核"铁律。
        """
        await self.sender.send(f"正在按歌词识别: {fragment}".strip())
        try:
            from .lyrics import resolve_from_lyric
            song = await resolve_from_lyric(self.session, fragment, search_song=search_song)
            if not song:
                await self.sender.send(f"未找到匹配歌曲: {fragment}".strip())
                return None
            item = QueueItem(song=song, requester=requester, requester_uid=uid)
            pos = self.queue.add(item)
            self.stats.record(song.name, song.singer, requester)
            await self.sender.send(reply("added_lyric", song=_fmt_song(song), pos=pos))
            return song
        except Exception as e:
            log.error(f"歌词点歌异常: {e}")
            await self.sender.send(f"歌词点歌失败: {e}")
            return None

    async def _handle_play_batch(self, query: str, requester: str, uid: int = 0):
        """批量点歌（纯增量层，零词典，不改动单曲逻辑）。

        bot/batch.batch_plans 给出结构候选（不依赖任何歌手数据库）：
        - 分号显式分隔：可信，逐段走完整单曲逻辑（_handle_play）；
        - 每段一组 / 两两一组：必须通过 _verify_batch_groups 搜索验证
          （每组高分命中 + 歌手与组内段互认 + 组间不同歌）才成立；
        - 全部候选验证失败 -> 回退单曲（整句走原单曲逻辑）。

        单曲的全部特性（自由格式、模糊、别名互认、原版优先、共识、门禁、
        跨源回退）通过 search_song / _handle_play 自动继承。
        """
        plans = batch_plans(query)
        if not plans:
            return await self._handle_play(query, requester)
        for groups, trusted in plans:
            groups, dropped = cap_batch_queries(groups)
            if dropped:
                await self.sender.send(
                    f"批量点歌最多 {MAX_BATCH_SONGS} 首，已截断 {dropped} 首"
                    f"（可在 config.json 的 max_batch_songs 调整）")
            n = len(groups)
            if trusted:
                # 分号：用户显式表达，每段独立走完整单曲解析（未找到不中断其余）
                log.info(f"批量点歌(分号): 共 {n} 首 -> {groups}")
                ok = 0
                for i, one in enumerate(groups):
                    log.info(f"批量点歌 [{i + 1}/{n}]: {one}")
                    if await self._handle_play(one, requester, uid):
                        ok += 1
                await self.sender.send(reply("batch_done", ok=ok, n=n))
                return
            # 结构候选：搜索验证（并行，复用现有 search_song 的全部特性）
            songs = await self._verify_batch_groups(groups)
            if songs:
                log.info(f"批量点歌(验证通过): {[(s.name, s.singer, s.platform) for s in songs]}")
                seen = set()
                added = 0
                for song in songs:
                    item = QueueItem(song=song, requester=requester, requester_uid=uid)
                    pos = self.queue.add(item)
                    self.stats.record(song.name, song.singer, requester)
                    added += 1
                    await self.sender.send(reply("added", song=_fmt_song(song), pos=pos))
                await self.sender.send(reply("batch_done", ok=added, n=n))
                return
            log.info(f"批量候选验证未通过，尝试下一结构: {groups}")
        # 全部候选都不成立 -> 回退单曲整句（原单曲逻辑，特性全保留）
        await self._handle_play(query, requester, uid)

    async def _verify_batch_groups(self, groups: List[str]) -> Optional[List[SongInfo]]:
        """并行搜索验证结构候选：零词典判断"是不是真批量"。

        每组作为独立单曲查询走现有 search_song（原版优先/共识/门禁全生效），
        成立条件（全部满足，宁缺毋滥）：
        1. 每组都搜到且评分 >= BATCH_VERIFY_SCORE（防单词碎片乱命中）；
        2. 每组结果的歌手与组内至少一个片段互认（本职别名/模糊匹配，
           不引入数据库之外的结构判断）；
        3. 组间不是同一首歌（拆错时常见同歌重复）。
        任一不满足 -> 返回 None（调用方回退下一候选/单曲）。
        """
        # 各组作为独立单曲查询，走与 _handle_play 完全一致的解析路径（复用 _resolve_single），
        # 因此自由格式/模糊/别名/原版优先/共识/门禁 全部生效——零额外逻辑。
        raw = {g: asyncio.create_task(self._resolve_single(g)) for g in groups}
        songs: List[SongInfo] = []
        seen = set()
        for g, task in raw.items():
            try:
                song = await task
            except Exception as e:
                log.debug(f"批量验证搜索异常 {g!r}: {e}")
                song = None
            if song is None or song.score < BATCH_VERIFY_SCORE:
                return None
            # 歌手一致性：组内任一片段与结果歌手互认（别名本职/模糊），说明"猜的歌手"成立
            segs = g.split()
            if segs and not any(
                    _alias_match(seg, song.singer) or _fuzzy_match(seg, song.singer)
                    for seg in segs):
                log.debug(f"批量验证歌手不一致: {g!r} -> {song.singer}")
                return None
            key = (_normalize_text(song.name), _normalize_text(song.singer))
            if key in seen:
                return None
            seen.add(key)
            songs.append(song)
        return songs

    # ===== 公共点歌台边界（场景铁律：自己的歌随意处置，别人的歌不能乱搞）=====

    @staticmethod
    def _is_owned_by(item: QueueItem, username: str, uid: int = 0) -> bool:
        """判断队列项是否属于该用户：uid 优先（防重名），无 uid 时退回用户名。"""
        if item.requester_uid and uid:
            return item.requester_uid == uid
        return bool(item.requester) and item.requester == username

    def _find_duplicate(self, song: SongInfo):
        """公共台防重复：同一首歌正在播放返回 "playing"；已在队列返回其展示位（1 起）；否则 None。

        同名歌按 (歌名, 规范歌手) 判定：歌手一致（或结果未署歌手）才算同一首，
        避免"晴天-周杰伦"拦住"晴天-林俊杰"这类同名不同歌。
        """
        def same(a: SongInfo, b: SongInfo) -> bool:
            if _normalize_text(a.name) != _normalize_text(b.name):
                return False
            na, nb = _normalize_text(a.singer), _normalize_text(b.singer)
            if not na or not nb:
                return True  # 未署歌手时不误拦同名歌
            return (_canon_singer(a.singer) or na) == (_canon_singer(b.singer) or nb)

        if self._current_item and same(self._current_item.song, song):
            return "playing"
        for i, it in enumerate(self.queue.snapshot()):
            if same(it.song, song):
                return i + 1
        return None

    def _playing_text(self, song: SongInfo, requester: str = "") -> str:
        """正在播放文案：公共台氛围，附点歌人署名。"""
        base = reply("playing", song=_fmt_song(song))
        if requester:
            base += reply("credit", user=requester)
        return base

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

    async def _skip_current(self, msg: str = ""):
        """跳过当前歌曲（msg 可自定义文案，默认"已切歌"）。"""
        self.playback.stop()
        await self.sender.send(msg or reply("switched"))

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
            self._current_item = item
            on_start = lambda it=item: self.sender.send(self._playing_text(it.song, it.requester))
            self._playing = True
            if item.url:
                # 有预获取的URL，直接播放
                await self.playback.play(item.url, item.song, on_finish=self._on_song_finish, on_start=on_start)
            else:
                # 无URL，播放时按音质降级获取（下载失败自动重试低音质）
                await self._play_with_fallback(item.song, on_start=on_start)
            self._playing = False
            self._current_item = None

    async def _try_play_song(self, song: SongInfo, on_start=None) -> bool:
        """对单首歌：并行取各音质URL -> 预验证 -> 按音质优先级下载播放（参考Museek波次竞速）。

        返回 True 表示播放已尝试（成功/失败/切歌，on_finish 已调用）；False 表示此源完全不可用
        （无URL / 全代理链接 / 全音质下载失败），供调用方决定跨源回退。
        """
        # 音质下限（强化"音质最好"）：低于 MIN_QUALITY 的音质直接跳过，只取达到下限的最高可用音质
        try:
            min_idx = QUALITY_LADDER.index(MIN_QUALITY)
        except ValueError:
            min_idx = 0
        ladder = QUALITY_LADDER[min_idx:]
        song_info = {
            "name": song.name,
            "singer": song.singer,
            "song_id": song.song_id,
            "album": song.album,
            "interval": song.interval,
        }
        # 并行获取各音质的URL（而非逐个降级，大幅缩短等待时间）；
        # URL 获取限时（响应提速，源自 LXMusic「按音源可靠性动态调度」）：
        # 慢源/挂死源不拖累整体，超时后用已到手的结果继续（可能触发跨源回退）
        log.info(f"并行获取音质: {', '.join(ladder)} [{PLATFORM_NAMES.get(song.platform, song.platform)}]")
        tasks = {}
        for quality in ladder:
            tasks[quality] = asyncio.create_task(
                get_bridge().get_url(song.platform, song_info, quality)
            )

        done, pending = await asyncio.wait(tasks.values(), timeout=URL_FETCH_TIMEOUT)
        for t in pending:
            t.cancel()
        # task -> 音质映射（asyncio.wait 的 done 集合无序，需按映射取结果）
        task_quality = {t: q for q, t in tasks.items()}
        quality_results = {}  # quality -> result 或 None
        for t in done:
            try:
                quality_results[task_quality[t]] = t.result()
            except Exception:
                quality_results[task_quality[t]] = None
        results = [quality_results.get(q) for q in ladder]

        # 按音质优先级整理有效URL
        url_map = {}
        for quality, result in zip(ladder, results):
            if isinstance(result, Exception):
                log.debug(f"获取链接异常 ({quality}): {result}")
                continue
            if result and isinstance(result, str) and result.startswith("http"):
                url_map[quality] = result
                log.info(f"获取成功 ({quality}): {result[:80]}...")

        if not url_map:
            log.error(f"所有音质均无法获取URL: {song.name} - {song.singer} [{PLATFORM_NAMES.get(song.platform, song.platform)}]")
            return False

        # 说明：不预拒绝 .php 代理链接——实测此类链接现可返回真实音频（ID3 魔数）。
        # 有效性交由预验证（GET Range 检查实际内容）与下载层（<1KB 判废）兜底，
        # 避免"过时假设"错杀可用源导致白白跨源回退。

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
        if await self._try_play_by_quality(
            song, url_map, validated, on_start,
            "音质 {quality} 下载失败，尝试下一音质...", qualities=ladder,
        ):
            return True  # 播放已尝试（成功/失败/切歌），on_finish已调用

        # 回退：如果所有预验证都失败，直接尝试下载最高音质（不经验证）
        # 某些代理 URL 可能无法通过 GET Range 验证但实际可正常下载
        if not any(validated.values()):
            log.info(f"所有URL预验证均失败，尝试直接下载（跳过验证）...")
            no_filter = {q: True for q in ladder}
            if await self._try_play_by_quality(
                song, url_map, no_filter, on_start,
                "音质 {quality} 直接下载失败，尝试下一音质...", qualities=ladder,
            ):
                return True

        # 所有音质都失败
        log.error(f"所有音质均无法播放: {song.name} - {song.singer} [{PLATFORM_NAMES.get(song.platform, song.platform)}]")
        return False

    async def _play_with_fallback(self, song: SongInfo, on_start=None):
        """播放一首歌：主源优先；主源不可用（全代理/全失败）时跨源回退同歌候选。

        跨源回退严守「只在同歌时换源」——只回退到 search_song 附带的同 (歌名,歌手)
        其他平台候选，或在末路在线重搜同歌的其他源（源自 Levyra「音质提升不能以播错歌为代价」
        与 Q-music「失败后回退跨源匹配」）。绝不因换源而播错歌。
        """
        if await self._try_play_song(song, on_start):
            return  # 主源已尝试播放（成功/失败/切歌），on_finish 已调用
        # 主源不可播 -> 先尝试同歌跨源候选
        alternates = getattr(song, "alternates", None) or []
        if alternates:
            for alt in alternates:
                log.warning(f"主源不可播，跨源回退同歌: {song.name} - {song.singer} [{alt.platform}]")
                if await self._try_play_song(alt, on_start):
                    return
        # 同歌候选均不可用 -> 在线重搜同歌的其他源（排除已失败主源），再试一次
        try:
            retry = await search_song(self.session, song.name, song.singer,
                                     exclude_source=[song.platform])
        except Exception as e:
            log.debug(f"重搜同歌其他源失败: {e}")
            retry = None
        if retry and retry.platform != song.platform:
            log.warning(f"主源与同歌候选均不可播，重搜同歌回退: {retry.name} - {retry.singer} [{retry.platform}]")
            if await self._try_play_song(retry, on_start):
                return
        # 主源与所有回退源均无法播放
        await self._on_song_finish(False)

    async def _try_play_by_quality(self, song: SongInfo, url_map: dict,
                                   validated: dict, on_start, warn_msg: str,
                                   qualities=None) -> bool:
        """按音质优先级依次尝试下载播放。返回 True 表示已尝试过（成功/失败/切歌，on_finish 已调用）。

        url_map: 音质 -> URL；validated: 音质 -> 是否通过预验证（缺失视为通过）。
        warn_msg: 单个音质失败时的警告模板（{quality} 占位）。
        qualities: 音质优先级列表（受 MIN_QUALITY 下限约束），默认 QUALITY_LADDER。
        """
        if qualities is None:
            qualities = QUALITY_LADDER
        for quality in qualities:
            url = url_map.get(quality)
            if not url or not validated.get(quality, True):
                continue
            played = await self.playback.play(url, song, on_finish=self._on_song_finish, on_start=on_start)
            if played:
                return True
            log.warning(warn_msg.format(quality=quality))
        return False

    async def _on_song_finish(self, success: bool = True, intentional: bool = False):
        """歌曲播放完成回调。intentional=True表示被切歌（"已切歌"已发送，不重复通知）。"""
        self._current_item = None
        item = self.queue.peek()
        if not self.queue.is_empty:
            self.queue.remove_current()
        if item:
            if intentional:
                pass  # 切歌时"已切歌"已发送，不重复通知
            elif success:
                await self.sender.send(reply("played", song=_fmt_song(item.song)))
            else:
                await self.sender.send(f"播放失败: {_fmt_song(item.song)}")

    async def _room_check_loop(self):
        """定期检查直播间状态。"""
        while True:
            await asyncio.sleep(ROOM_CHECK_INTERVAL)
            try:
                await protocol._api_limiter.acquire()
                async with self.session.get(BILI_ROOM_INFO, headers=protocol.BILI_HEADERS) as resp:
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
