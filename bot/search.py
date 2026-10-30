"""音源搜索与匹配：并行搜索所有 lx 平台、评分择优、获取播放 URL（含音质降级）。"""
import asyncio
import re
import time
from collections import Counter
from typing import Any, Optional

import aiohttp

from .artist_alias import artist_alias_match as _alias_match, canonical_singer as _canon_singer
from .bridge import get_bridge
from .cache import TTLCache
from .config import (CACHE_MAX_ENTRIES, ENABLE_PINYIN_FUZZY, SEARCH_CACHE_TTL,
                     URL_CACHE_TTL, log)
from .fuzzy import PINYIN_AVAILABLE, pinyin_match
from .models import SongInfo

try:
    # rapidfuzz 为项目依赖（C++ 实现，极快）；缺失时退化为字符重叠率
    from rapidfuzz import fuzz as _rfuzz
except Exception:  # pragma: no cover - 正常环境已安装
    _rfuzz = None

import re as _re

_CJK_RE = _re.compile(r"[\u4e00-\u9fff]")

# 模糊匹配阈值：正样本最低 67（周杰→周杰伦 Jaccard），负样本（无关歌曲/歌手）远低于此
FUZZY_THRESHOLD = 60.0

# 置信度门禁：best 候选评分低于此值视为"未找到"，避免勉强匹配错误歌曲。
# 内部实现细节，不暴露给用户配置（大厂惯例：用户永远只关心"搜到了/没搜到"）
MIN_ACCEPT_SCORE = 20

# 智能建议阈值（did-you-mean）：点歌未找到时，以此较低门槛再探一次，
# 把"够得着但不达门禁"的最接近候选作为建议（绝不自动播放，仅提示）
SUGGEST_MIN_SCORE = 60

# 响应提速：最小收集窗口后，若已有高质量候选（歌名+歌手均良好匹配、且非衍生降权）
# 即提前返回，不必等满全部平台（避免慢平台拖慢）；3 秒仍为兜底上限
GATHER_MIN = 0.7
EARLY_RETURN_SCORE = 130

# 跨平台共识加权（源自 Levyra / LX Music 经验）：同一 (歌名,歌手) 被 >=2 个独立音源
# 共同返回为各自最佳，几乎可断定是"规范原版"，给予加权以盖过单音源把翻唱/remix 顶到原版之上的情况
CONSENSUS_BONUS = 35


PLATFORM_NAMES = {
    "wy": "网易云", "tx": "QQ音乐", "kw": "酷我", "kg": "酷狗", "mg": "咪咕",
}


QUALITY_LADDER = ["flac24bit", "flac", "320k", "192k", "128k"]


# ===== 进程内缓存（S 级特性）=====
# 搜索缓存：同一 (歌名,歌手,排除源) 短时内复用结果，连点/复读弹幕不再打满 5 个音源。
# URL 缓存：同一 (平台,歌曲ID,音质) 的取链结果复用，重复播放省一次多音质并发取链。
# 两者均带 TTL 与容量上限，可由 config.json 的 search_cache_ttl / url_cache_ttl / cache_max_entries 调整。
_search_cache = TTLCache(SEARCH_CACHE_TTL, CACHE_MAX_ENTRIES)
_url_cache = TTLCache(URL_CACHE_TTL, CACHE_MAX_ENTRIES)


def _search_cache_key(keyword: str, singer: str, exclude_source: Optional[list]) -> tuple:
    return (keyword, singer or "", tuple(sorted(exclude_source or ())))


def _music_url_key(song: SongInfo, quality: str) -> tuple:
    ident = song.song_id or f"{song.name}|{song.singer}"
    return (song.platform or "", ident, quality)


def cached_music_url(song: SongInfo, quality: str) -> Optional[str]:
    """取已缓存的播放 URL（未命中/过期返回 None）。"""
    return _url_cache.get(_music_url_key(song, quality))


def cache_music_url(song: SongInfo, quality: str, url: str):
    """记录已取到的播放 URL（仅 http 链接）。"""
    if url and url.startswith("http"):
        _url_cache.set(_music_url_key(song, quality), url)


def invalidate_music_url(song: SongInfo, quality: str):
    """作废某个音质的 URL 缓存（链接失效/下载失败时调用，下次重新取链）。"""
    _url_cache.invalidate(_music_url_key(song, quality))


def clear_caches():
    """清空全部缓存（配置热更新/测试隔离用）。"""
    _search_cache.clear()
    _url_cache.clear()


def cache_stats() -> dict:
    """缓存命中统计（命中率观测；日志/排障用）。"""
    return {
        "search": {"hits": _search_cache.hits, "misses": _search_cache.misses, "size": len(_search_cache)},
        "url": {"hits": _url_cache.hits, "misses": _url_cache.misses, "size": len(_url_cache)},
    }


def _normalize_text(text: str) -> str:
    """标准化文本用于模糊匹配：去除空格、标点、括号内容，小写。"""
    # 去除括号及括号内内容
    text = re.sub(r'[（(].*?[)）]', '', text)
    # 去除非字母数字非中日韩字符
    text = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
    return text.lower()


def _jaccard_similarity(a: str, b: str) -> float:
    """按字多重集 Jaccard 相似度：中文乱序重排容错，且不会因共享单字误配。

    - 气球告白 vs 告白气球 = 1.0（字集合相同，顺序不同）
    - 周杰伦 vs 林俊杰 = 0.2（仅共享"杰"一字，正确拒绝）
    比 token_sort_ratio 更准：后者把"周杰伦/林俊杰"都排成"杰+单字"而误判相似。
    """
    ca, cb = Counter(a), Counter(b)
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return inter / union if union else 0.0


def _is_subseq(a: str, b: str) -> bool:
    """a 是否为 b 的子序列（保持相对顺序，容忍任意缺字，如 周杰 是 周杰伦 的子序列）。"""
    it = iter(b)
    return all(ch in it for ch in a)


def _fuzzy_match(keyword: str, target: str) -> bool:
    """模糊匹配（重排 / 缺字 / 大小写 / 空格 / 多语言容错，且对无关字符串严格拒绝）。

    取以下策略的最高分，>= FUZZY_THRESHOLD 即命中：
    1. 标准化后双向子串命中 -> 直接通过（覆盖精确、含标点、大小写、空格差异）。
    2. 短查询（标准化 <=2 字符）严要求：仅接受高 ratio 或中文缺字子序列（长度差<=1），
       避免单字/双字（周/气球/you）过度匹配导致的误配，兼顾准确率。
    3. rapidfuzz ratio（编辑距离归一）+ partial_ratio（长短不一 / 关键词被埋）。
    4. 仅含中日韩时，按字多重集 Jaccard：容错"顺序颠倒/乱序重排"（气球告白≈告白气球），
       且不会因共享单字把不同歌手（周杰伦/林俊杰）误判为相似。
    5. 子序列容错（仅缺字不多、长度差<=2 时启用）：容忍中文缺字（告白气→告白气球）。
    """
    norm_kw = _normalize_text(keyword)
    norm_target = _normalize_text(target)
    if not norm_kw or not norm_target:
        return False
    # 标准化后子串匹配（双向）
    if norm_kw in norm_target or norm_target in norm_kw:
        return True
    # 拼音层：谐音 / 错别字 / 简繁 / 全拼输入容错（仅对含 CJK 的比对生效，且保守放行）
    if ENABLE_PINYIN_FUZZY and pinyin_match(keyword, target):
        return True
    # 短查询：严要求，防单字/双字过度匹配（如 天/大海 等完全无关串）
    # 但保留：高 ratio、中文缺字子序列（长度差<=1）、以及"完全重排"（Jaccard>=0.8，如 天晴≈晴天）
    if len(norm_kw) <= 2:
        ratio_ok = _rfuzz.ratio(norm_kw, norm_target) >= 90.0 if _rfuzz else False
        jaccard_ok = _jaccard_similarity(norm_kw, norm_target) >= 0.8
        if (abs(len(norm_kw) - len(norm_target)) <= 1 and (ratio_ok or _is_subseq(norm_kw, norm_target))) or jaccard_ok:
            return True
        return False
    if _rfuzz is None:  # 退化：字符重叠率（rapidfuzz 缺失时）
        common = sum(1 for c in norm_kw if c in norm_target)
        return common / max(len(norm_kw), len(norm_target)) >= 0.6
    score = max(_rfuzz.ratio(norm_kw, norm_target),
                _rfuzz.partial_ratio(norm_kw, norm_target))
    # 中日韩重排/缺字容错（按字多重集 Jaccard + 受控子序列）
    if _CJK_RE.search(norm_kw + norm_target):
        score = max(score, _jaccard_similarity(norm_kw, norm_target) * 100.0)
        if abs(len(norm_kw) - len(norm_target)) <= 2 and _is_subseq(norm_kw, norm_target):
            score = max(score, 88.0)
    return score >= FUZZY_THRESHOLD


# 衍生 / 非原版版本标记：命中则大幅降权，确保「原版」排在第一选择
VERSION_NOISE = [
    "lofi", "Lofi", "LOFI", "remix", "Remix", "REMIX",
    "cover", "Cover", "COVER", "翻唱", "DJ", "Dj", "混音", "伴奏", "现场",
    "live", "Live", "LIVE", "acoustic", "instrumental", "radio edit", "extended",
    "试听", "片段", "铃声", "纯音乐", "钢琴", "高燃", "电音", "舞曲", "卡点",
    "抖音", "快手", "车载", "怀旧", "慢四", "重制", "重新", "新版", "改编", "串烧", "VIP",
    "母带", "臻品", "慢摇", "伤感", "对唱", "合唱", "国风", "彩铃",
]


def _strip_version_noise(text: str) -> str:
    """去掉衍生版本标记，得到「核心歌名」，用于原版加权与歧义消解。"""
    t = text
    for p in VERSION_NOISE:
        t = t.replace(p, "")
    return t


def _detect_query_version(query: str) -> Optional[str]:
    """检测用户查询是否明确点名了某个版本（翻唱/remix/live...）。

    返回命中的（小写）版本词；无则 None。用于「显式版本词例外」：用户主动要某版本时不对其降权。
    """
    q = (query or "").lower()
    for v in VERSION_NOISE:
        if v.lower() in q:
            return v.lower()
    return None


def _score_search_result(name: str, result_singer: str, keyword: str, singer: str,
                         fuzzy_match=None, query_version: Optional[str] = None) -> int:
    """评分搜索结果质量，分数越高越匹配。

    设计目标：① 高自由度（模糊/缺字/乱序匹配仍给分）；② **原版优先**——
    衍生版本（翻唱/remix/live/lofi/DJ/伴奏/现场等）大幅降权，确保干净原版排在第一选择；
    ③ **显式版本词例外**（源自 Q-music）：当用户查询本身点名了某版本（翻白"翻唱/remix"），
    该版本精准命中时反而不降权并加分，让用户主动要的版本胜出；④ 准确率（置信度门禁在 search_song）。
    """
    fuzzy = fuzzy_match or _fuzzy_match
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
    elif fuzzy(keyword, name):
        score += 30   # 模糊匹配
    elif ENABLE_PINYIN_FUZZY and pinyin_match(keyword, name):
        score += 30   # 拼音/谐音匹配（错别字/繁体/全拼输入，拼音键相等）
    else:
        score += 10   # 低匹配度
    # 版本处理：用户明确点名某版本 -> 命中该版本的候选不降权并加分（精准满足请求）；
    # 否则衍生/非原版版本（翻唱/remix/live/lofi/DJ/伴奏/现场/新版等）大幅降权，确保原版优先
    low = name.lower()
    if query_version and query_version in low:
        score += 25   # 命中用户点名的版本
    else:
        for p in VERSION_NOISE:
            if p.lower() in low:
                score -= 55
                break
    # 歌手匹配（标准化后比较）
    if singer:
        norm_singer = _normalize_text(singer)
        norm_rsinger = _normalize_text(result_singer)
        if norm_singer == norm_rsinger:
            score += 50  # 完全匹配
        elif norm_singer in norm_rsinger or norm_rsinger in norm_singer:
            score += 30  # 包含歌手
        elif _alias_match(singer, result_singer):
            score += 30  # 艺名别名等价（中/英文/混写/昵称互认）
        elif fuzzy(singer, result_singer):
            score += 15  # 模糊匹配
        elif ENABLE_PINYIN_FUZZY and pinyin_match(singer, result_singer):
            score += 15  # 拼音/谐音匹配（错别字/繁体/全拼歌手名）
        else:
            score -= 10  # 歌手不匹配
    return score


# ===== 评分器实现钩子（S3 切换点；已默认启用健壮实现）=====
# search_song 通过这两个钩子调用评分实现。默认即健壮实现（rapidfuzz 多策略评分，
# 见上方 _fuzzy_match/_score_search_result）；rapidfuzz 为项目依赖，缺失时退化为字符重叠率。
# 兼容钩子：search_rapidfuzz.enable()/disable() 均指向同一实现，历史调用方式不受影响。
FUZZY_MATCH = _fuzzy_match
SCORE_RESULT = _score_search_result


def _select_best_with_consensus(top_by_source: dict, fallback: Optional[tuple]) -> Optional[tuple]:
    """从各音源最佳候选中结合「跨平台共识」选出最终最佳（原版优先）。

    top_by_source: source -> (score, name, result_singer, r)
    返回 (score, source, r, name, result_singer) 或 None。

    共识原则（源自 Levyra / LX Music）：同一 (歌名,歌手) 被 >=2 个独立音源共同返回为各自最佳，
    几乎可断定是「规范原版」，给予 CONSENSUS_BONUS 加权，盖过单音源把翻唱/remix 顶到原版之上的情况；
    无共识（各音源结果互不相同或仅 1 源）时退回全局最高分（fallback）。
    """
    if not top_by_source:
        return fallback
    def _key(nm: str, rs: str):
        # 歌手侧先做艺名归一：中/英文写法互认后才能形成跨语言共识
        canon = _canon_singer(rs)
        return (_normalize_text(nm), canon if canon else _normalize_text(rs))
    key_counter: Counter = Counter()
    for _src, (sc, nm, rs, _r) in top_by_source.items():
        key_counter[_key(nm, rs)] += 1
    best_key = key_counter.most_common(1)[0][0] if key_counter else None
    best_n = key_counter.get(best_key, 0) if best_key else 0
    candidates = []
    for src, (sc, nm, rs, r) in top_by_source.items():
        key = _key(nm, rs)
        bonus = CONSENSUS_BONUS if (best_n >= 2 and key == best_key) else 0
        candidates.append((sc + bonus, src, r, nm, rs))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0]


async def search_song(session: aiohttp.ClientSession, keyword: str, singer: str = "",
                     exclude_source: Optional[list] = None) -> Optional[SongInfo]:
    """通过 Node.js 桥接器搜索歌曲。并行搜索所有平台，3秒窗口收集结果择优返回。

    exclude_source: 可选，排除某些音源（用于主源不可播时在线重搜同歌的其他源）。
    """
    search_kw = f"{keyword} {singer}" if singer else keyword
    query_version = _detect_query_version(search_kw)  # 显式版本词例外
    cache_key = _search_cache_key(keyword, singer, exclude_source)
    cached = _search_cache.get(cache_key)
    if cached is not None:
        log.debug(f"搜索缓存命中: {search_kw}")
        return cached
    sources = ["kw", "wy", "tx", "kg", "mg"]
    if exclude_source:
        sources = [s for s in sources if s not in exclude_source]

    # 并行发起所有平台搜索
    tasks = {}
    for s in sources:
        tasks[s] = asyncio.create_task(get_bridge().search(s, search_kw, limit=5))

    # 收集 3 秒内的结果，按评分择优（完美匹配立即返回）
    pending = set(tasks.values())
    source_by_task = {t: s for s, t in tasks.items()}
    best_result = None  # (score, source, result)
    top_by_source = {}  # 各音源最佳候选（用于跨平台共识加权）
    collect_deadline = time.time() + 3  # 3秒收集窗口（兜底上限）
    gather_start = time.time()

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
                    # 垃圾结果过滤：API 把「含空格的完整搜索词」原样当歌名回显（无论歌手
                    # 字段是否为空/伪造，如 "Firework Katy Perry"/草莓波波）-> 跳过。
                    # 纯歌名搜索（关键词无空格）返回同名歌是正常精确匹配，不在此列
                    if (" " in search_kw.strip()
                            and _normalize_text(name) == _normalize_text(search_kw)):
                        log.debug(f"跳过垃圾结果（歌名=搜索词原样回显）: {name}/{result_singer}")
                        continue
                    # 模糊匹配歌名：先尝试歌名关键词，再尝试完整搜索词
                    name_ok = FUZZY_MATCH(keyword, name) or FUZZY_MATCH(search_kw, name)
                    # 纯歌手搜索兜底：关键词命中歌手名也算候选（如只搜"林俊杰"）；
                    # 艺名别名等价让纯中文艺名也能命中英文写法的歌手（如 贾斯汀比伯 -> Justin Bieber）
                    singer_kw_ok = FUZZY_MATCH(keyword, result_singer) or _alias_match(keyword, result_singer)
                    if not name_ok and not singer_kw_ok:
                        log.debug(f"歌名/歌手均不匹配: {name}/{result_singer} vs {keyword}")
                        continue
                    # 如果提供了歌手名，模糊匹配歌手（艺名别名等价：中/英文/混写/昵称互认）
                    if singer:
                        if not (FUZZY_MATCH(singer, result_singer) or _alias_match(singer, result_singer)):
                            log.debug(f"歌手不匹配: {result_singer} vs {singer}")
                            continue
                    # 评分：纯歌手搜索（keyword 命中了歌手名、但未提供 singer 参数）时，
                    # 把 keyword 当作歌手参与评分，让歌手命中体现在分数上（否则仅歌名低分易被门禁拒）
                    if singer_kw_ok and not singer:
                        score = SCORE_RESULT(name, result_singer, keyword, keyword,
                                            query_version=query_version)
                    else:
                        score = SCORE_RESULT(name, result_singer, keyword, singer,
                                            query_version=query_version)
                    log.debug(f"搜索候选: {name} - {result_singer} [{source}] 评分={score}")
                    # 完美匹配（歌名完全一致 + 歌手完全一致）立即返回
                    if score >= 150:
                        song_id = str(r.get("id", r.get("songmid", r.get("song_id", ""))))
                        if song_id.startswith("MUSIC_"):
                            song_id = song_id[6:]
                        log.info(f"搜索成功: {name} - {result_singer} [{PLATFORM_NAMES.get(source, source)}] ID={song_id} (完美匹配)")
                        song = SongInfo(
                            name=name, singer=result_singer,
                            album=r.get("albumName", r.get("album", "")),
                            song_id=song_id, platform=source,
                            interval=r.get("interval", ""),
                            score=score,
                        )
                        _search_cache.set(cache_key, song)
                        return song
                    # 记录最佳候选 与 各音源最佳（用于跨平台共识）
                    if best_result is None or score > best_result[0]:
                        best_result = (score, source, r)
                    cur = top_by_source.get(source)
                    if cur is None or score > cur[0]:
                        top_by_source[source] = (score, name, result_singer, r)
            # 提速：最小收集窗口后，已有高质量候选（原版、歌名+歌手均良好匹配）即提前返回，
            # 不再等待慢平台，保证响应速度
            if (best_result and (time.time() - gather_start) >= GATHER_MIN
                    and best_result[0] >= EARLY_RETURN_SCORE):
                break
            # 收集窗口结束
            if time.time() >= collect_deadline:
                break

        # 跨平台共识加权后选最佳（原版优先）；无共识时退回全局最高分
        chosen = _select_best_with_consensus(top_by_source, best_result)
        if chosen:
            score, source, r, name, result_singer = chosen
            # 置信度门禁：分数过低视为未找到，避免勉强匹配错误歌曲（准确率保障）
            if score < MIN_ACCEPT_SCORE:
                log.info(f"搜索失败(置信度不足 {score}<{MIN_ACCEPT_SCORE}): {search_kw}")
                return None
            song_id = str(r.get("id", r.get("songmid", r.get("song_id", ""))))
            if song_id.startswith("MUSIC_"):
                song_id = song_id[6:]
            # 跨源回退候选：与主候选同 (歌名,歌手)、但来自其他平台的版本。
            # 主源 URL 不可用（如全为代理链接）时，播放层可回退到这些同歌候选，
            # 既保证"能播"，又严守"只在同歌时换源"（绝不播错歌，源自 Levyra/Q-music）。
            chosen_key = (_normalize_text(name),
                          _canon_singer(result_singer) or _normalize_text(result_singer))
            alternates = []
            for alt_src, (sc, nm, rs, alt_r) in top_by_source.items():
                if alt_src == source:
                    continue
                if (_normalize_text(nm),
                        _canon_singer(rs) or _normalize_text(rs)) != chosen_key:
                    continue
                alt_id = str(alt_r.get("id", alt_r.get("songmid", alt_r.get("song_id", ""))))
                if alt_id.startswith("MUSIC_"):
                    alt_id = alt_id[6:]
                alternates.append(SongInfo(
                    name=nm, singer=rs,
                    album=alt_r.get("albumName", alt_r.get("album", "")),
                    song_id=alt_id, platform=alt_src,
                    interval=alt_r.get("interval", ""),
                ))
            log.info(f"搜索成功: {name} - {result_singer} [{PLATFORM_NAMES.get(source, source)}] ID={song_id} "
                     f"(评分={score}, 共识候选={len(top_by_source)}源, 跨源回退候选={len(alternates)})")
            song = SongInfo(
                name=name, singer=result_singer,
                album=r.get("albumName", r.get("album", "")),
                song_id=song_id, platform=source,
                interval=r.get("interval", ""),
                alternates=alternates,
                score=score,
            )
            _search_cache.set(cache_key, song)
            return song
        log.info(f"搜索失败: {search_kw}")
        return None
    finally:
        # 取消未完成的搜索任务
        for task in tasks.values():
            if not task.done():
                task.cancel()


async def suggest_song(session: aiohttp.ClientSession, keyword: str, singer: str = "") -> Optional[SongInfo]:
    """点歌未找到时的智能建议（did-you-mean）。

    正式搜索失败 = 所有候选被「歌名/歌手预过滤」拒绝或评分 < MIN_ACCEPT_SCORE，
    因此建议层必须**绕过预过滤**：并行搜索各平台原始结果，仅用评分函数排序，
    取「最接近」的候选（歌名完全/开头匹配等，>= SUGGEST_MIN_SCORE）。
    只提示不播放，绝不影响正常搜索的准确性。
    """
    search_kw = f"{keyword} {singer}" if singer else keyword
    sources = ["kw", "wy", "tx", "kg", "mg"]
    tasks = {s: asyncio.create_task(get_bridge().search(s, search_kw, limit=3))
             for s in sources}
    best: Optional[tuple] = None  # (score, name, singer)
    try:
        done, pending = await asyncio.wait(tasks.values(), timeout=3)
        for t in pending:
            t.cancel()
        for t in done:
            try:
                result = t.result()
            except Exception:
                continue
            results_list = result if isinstance(result, list) else ([result] if result else [])
            for r in results_list:
                if not isinstance(r, dict):
                    continue
                name = r.get("name", "")
                rs = r.get("singer", "") or r.get("artist", "")
                if not name:
                    continue
                # 垃圾回显过滤（与 search_song 同规则）
                if (" " in search_kw.strip()
                        and _normalize_text(name) == _normalize_text(search_kw)):
                    continue
                if singer:
                    score = SCORE_RESULT(name, rs, keyword, singer)
                else:
                    # 无显式歌手：整句 / 单 token 当歌名 / token 两两配对(歌名,歌手假设)
                    # 中取最高分——整句污染不再埋掉「歌名精确命中」的最接近候选
                    # （典型收益：批量验证失败回退单曲后的长查询，如 "gee 少女时代 a妹 ..."）
                    toks = [t for t in keyword.split() if t]
                    cands = [SCORE_RESULT(name, rs, keyword, "")]
                    cands += [SCORE_RESULT(name, rs, t, "") for t in toks]
                    for i, tn in enumerate(toks):
                        for j, ts in enumerate(toks):
                            if i != j and tn and ts:
                                cands.append(SCORE_RESULT(name, rs, tn, ts))
                    score = max(cands)
                if best is None or score > best[0]:
                    best = (score, name, rs)
    finally:
        for t in tasks.values():
            if not t.done():
                t.cancel()
    if best and best[0] >= SUGGEST_MIN_SCORE:
        return SongInfo(name=best[1], singer=best[2], score=best[0])
    return None


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
        hit = cached_music_url(song, quality)
        if hit:
            log.debug(f"URL 缓存命中 ({quality}): {hit[:80]}...")
            return hit
        log.info(f"尝试音质: {quality}")
        url = await get_bridge().get_url(song.platform, song_info, quality)
        if url and url.startswith("http"):
            log.info(f"获取成功 ({quality}): {url[:80]}...")
            cache_music_url(song, quality, url)
            return url
    return None


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
