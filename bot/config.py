"""配置与日志：config.json 加载、日志系统初始化、集中管理全部配置常量。

本模块是唯一允许定义"配置初值"的地方；运行时可变的协议状态在 protocol.py，
通过 CookieManager.reload_config() 热更新（会写回本模块的对应常量）。
"""
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Dict


_console_handler = logging.StreamHandler()


log = logging.getLogger("点歌机")


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        log.error("config.json 不存在，请先创建配置文件")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()


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

# 取链限时（秒）：并行获取各音质 URL 的最长等待，超时用已到手结果继续（跨源回退尽快介入）
URL_FETCH_TIMEOUT = CFG.get("url_fetch_timeout", 15)

# 拼音/谐音/错别字/简繁 鲁棒模糊匹配（pypinyin 为项目依赖；缺失时自动禁用，优雅降级）
# 让 "周杰轮→周杰伦"、"zhoujielun→周杰伦"、"繁体→简体" 也能命中，把自由输入推进到"语音容错"
ENABLE_PINYIN_FUZZY = CFG.get("enable_pinyin_fuzzy", True)

# 歌词点歌（片段→歌曲）：正常搜索落空且输入像歌词时，用网易云片段搜索取候选 (name,singer)，
# 再交内核 search_song 真正解析播放（仅建议候选，不自行搜源）。无网络/失败时优雅降级为"未找到"
ENABLE_LYRICS_SEARCH = CFG.get("enable_lyrics_search", True)

# 歌词/片段搜索 HTTP 超时（秒）：慢源不拖累整体
LYRICS_TIMEOUT = CFG.get("lyrics_timeout", 6)

# 搜索结果缓存（秒）：同一查询短时内重复点歌不再打满 5 个音源；0 = 关闭。
# 60 秒兼顾「新鲜度」与「连点/复读弹幕」的重复请求。
SEARCH_CACHE_TTL = CFG.get("search_cache_ttl", 60)

# 播放 URL 缓存（秒）：同一首歌再次播放时直接复用已取链接，省一次多音质并发取链；
# 音乐平台 CDN 链接通常有分钟级有效期，过长会拿到失效链接，默认 4 分钟。
URL_CACHE_TTL = CFG.get("url_cache_ttl", 240)

# 缓存条目上限（每个缓存独立），防长时间运行内存无界增长
CACHE_MAX_ENTRIES = CFG.get("cache_max_entries", 256)

# 音质下限（强化"音质最好"）：低于此音质的链接直接跳过，不下载。
# 默认 flac24bit = 不限制下限（沿用原有"逐级试到最低音质"行为）；用户可调高下限，
# 例如设为 320k 即只取 320k/flac/flac24bit，跳过 192k/128k，确保不播低音质。
MIN_QUALITY = CFG.get("min_quality", "flac24bit")

# S2 播放层（python-mpv）可选参数，透传给 mpv.MPV（如 {"ao": "null", "vo": "null"}）；
# 默认空 = mpv 使用系统默认音频输出
MPV_OPTIONS = CFG.get("mpv_options", {})

# 点歌统计文件（/top 点歌排行数据源；置空字符串可关闭统计）
STATS_FILE = CFG.get("stats_file", "song_stats.json")

# 队列持久化文件：进程重启后恢复未播完的队列（置空字符串可关闭，退回纯内存队列）
QUEUE_FILE = CFG.get("queue_file", "queue.json")

# ===== 自定义回复模板（高度自定义）=====
# config.json 的 "replies" 可按键覆盖；缺失用默认值（= 内置文案，零行为变化）。
# 可用占位符：{user}点歌人 {query}搜索词 {song}歌曲 {pos}队列位置 {n}数量
_DEFAULT_REPLIES = {
    "searching": "正在搜索: {query}",
    "not_found": "未找到: {query}",
    "suggest": "未找到: {query}，你是不是想点《{name}》-{singer}？回复 /play {name} {singer} 可直接点播",
    "added": "已点: {song} (队列:{pos})",
    "added_lyric": "已点(歌词): {song} (队列:{pos})",
    "playing": "正在播放: {song}",
    "credit": "（{user} 点播）",
    "played": "播放完毕: {song}",
    "switched": "已切歌",
    "switch_own": "已切歌（自己点的歌自己做主~）",
    "duplicate": "《{song}》已经在队列里啦（第{pos}位），不用重复点哦~",
    "duplicate_playing": "《{song}》正在播放中，不用重复点哦~",
    "batch_done": "🎵 批量点歌完成: {ok}/{n} 首已按顺序加入队列",
    "help": "/play 歌名(歌手) 点歌(支持批量/歌词/拼音错字) | /lyric 歌词点歌 | /queue 队列 | /now 正在播放 | /remove 序号 撤回 | /top 点歌排行 | /switch 投票切歌",
    "queue_empty": "点歌队列为空，快来 /play 点歌吧~",
    "no_playing": "当前没有在播放的歌曲",
    "removed": "已撤回: {song}",
    "remove_denied": "只能撤回自己点的歌哦~",
    "remove_bad_index": "撤回失败：请输入队列中存在的序号（/queue 查看）",
    "top_title": "点歌排行 TOP{n}：",
}
REPLIES = {**_DEFAULT_REPLIES, **(CFG.get("replies") or {})}


def reply(key: str, **kw) -> str:
    """按模板键生成回复文案；模板缺失时回退默认模板，占位符缺失时原样保留。"""
    tpl = REPLIES.get(key) or _DEFAULT_REPLIES.get(key) or key
    try:
        return tpl.format(**kw)
    except (KeyError, IndexError):
        return tpl


SOURCE_DIR = CFG.get("source_dir", os.path.join(os.path.expanduser("~"), "Downloads", "音源", "文件形式"))

# 在线音源脚本 URL 列表（对齐 lx 播放器的"链接导入音源"）：
# 启动时下载到 bot/tmp/online_sources/ 缓存目录（与本地音源目录一起加载）
SOURCE_URLS = CFG.get("source_urls", [])

# 在线音源自更新周期（小时）：后台定时重新拉取 source_urls，内容有变即热重载，
# 保证「链接形式音源」始终为官方最新版；默认 6 小时，最小 0.17（10 分钟）。
SOURCE_REFRESH_INTERVAL = CFG.get("source_refresh_interval", 6)

# 音源看门狗/自检周期（秒）：定时重启崩溃的进程、重试初始化失败的音源（后端恢复后自愈）、
# 保证「永不失效」。默认 300 秒，最小 60 秒。
SOURCE_WATCHDOG_INTERVAL = CFG.get("source_watchdog_interval", 300)

# 单脚本初始化超时（秒）：混淆源解混淆+联网拉配置较慢，太短会误杀慢源。
# 默认 30 秒（原 15s 会把慢源误判为失败），可按需调整。
SOURCE_INIT_TIMEOUT = CFG.get("source_init_timeout", 30)


NODE_PATH = CFG.get("node_path", "node")


BRIDGE_SCRIPT = os.path.join(_PROJECT_ROOT, "source_bridge.js")
