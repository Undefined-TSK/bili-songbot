/** 主控：B 站弹幕指令、队列消费、播放编排、Cookie 热更新。 */
import fs from "node:fs";
import { batchPlans, capBatchQueries, maxBatchSongs } from "./batch.js";
import { CONFIG_PATH, config, reply } from "./config.js";
import { BiliDanmaku } from "./danmaku.js";
import { hub } from "./events.js";
import { looksLikeLyric, resolveFromLyric } from "./lyrics.js";
import { log } from "./logger.js";
import { lx } from "./lxserver.js";
import { FfplayPlayer } from "./player.js";
import { CookieManager, fetchBuvidPair, getRealRoomId, getRoomInfo, updateBuvid3, updateBuvid4 } from "./protocol.js";
import { PlayQueue } from "./queue.js";
import { BiliSender } from "./sender.js";
import {
  PLATFORM_NAMES,
  QUALITY_LADDER,
  cacheMusicUrl,
  cachedMusicUrl,
  fuzzyMatch,
  invalidateMusicUrl,
  searchSong,
  splitPlayQuery,
  suggestSong,
} from "./search.js";
import { SongStats } from "./stats.js";
import type { QueueItem, SongInfo } from "./types.js";
import { formatSongName, normalizeText, sleep } from "./util.js";
import { VoteManager } from "./vote.js";
import { artistAliasMatch, canonicalSinger } from "./artist-alias.js";

const BATCH_VERIFY_SCORE = 100;

export class BiliMusicBot {
  readonly playback = new FfplayPlayer();
  readonly queue = new PlayQueue(config.dataPath("queue_file", "queue.json") || undefined);
  readonly stats = new SongStats(config.dataPath("stats_file", "song_stats.json") || undefined);
  readonly cookieMgr = new CookieManager();
  sender: BiliSender | null = null;
  danmaku: BiliDanmaku | null = null;
  vote: VoteManager | null = null;
  currentItem: QueueItem | null = null;
  viewerCount = 0;
  accepting = true;
  startedAt = Date.now() / 1000;
  ready = false;
  running = false;
  private roomId = 0;
  private roomInput = "";
  private skipRequested = false;
  private configMtime = 0;
  private buvidFetched = false;

  get isReady(): boolean {
    return this.ready;
  }

  async run(): Promise<void> {
    this.running = true;
    this.startedAt = Date.now() / 1000;
    this.configMtime = this.mtime();
    this.cookieMgr.reloadFromConfig();

    const cfgBuvid3 = String(config.get("bili_buvid3", "") ?? "");
    const cfgBuvid4 = String(config.get("bili_buvid4", "") ?? "");
    if (cfgBuvid3) {
      updateBuvid3(cfgBuvid3);
      log.info(`使用 config.json 中的 bili_buvid3: ${cfgBuvid3.slice(0, 16)}...`);
    } else {
      log.info("config.json 未配置 bili_buvid3，尝试自动获取...");
      const pair = await fetchBuvidPair();
      this.buvidFetched = Boolean(pair.b3);
    }
    if (cfgBuvid4) updateBuvid4(cfgBuvid4);

    const cookieOk = await this.cookieMgr.validate();
    if (!cookieOk) {
      log.error(`Cookie 无效（${this.cookieMgr.reason}），弹幕发送功能不可用。机器人仍可监听弹幕和播放音乐`);
      log.error("请更新 config.json 中的 bili_sessdata 和 bili_jct，保存后自动热更新");
    }
    await this.initialize();
  }

  private mtime(): number {
    try {
      return fs.statSync(CONFIG_PATH).mtimeMs;
    } catch {
      return 0;
    }
  }

  private async initialize(): Promise<void> {
    while (this.running && !this.ready) {
      const room = config.roomId();
      if (!room) {
        log.error("未配置 bili_room_id，请在桌面端「设置」或 config.json 中填写直播间号（本服务会继续重试）");
        await sleep(30000);
        config.reload();
        continue;
      }
      const real = await getRealRoomId(room);
      if (!real) {
        log.error(`无法解析直播间 ${room}，20 秒后重试`);
        await sleep(20000);
        continue;
      }
      this.roomInput = room;
      this.roomId = real;
      this.sender = new BiliSender(this.cookieMgr, real);
      this.vote = new VoteManager(this.sender);
      this.vote.updateViewerCount(this.viewerCount);
      this.danmaku = new BiliDanmaku(room, this.cookieMgr, {
        onMessage: (text, uid, username) => {
          void this.onDanmaku(text, uid, username);
        },
        onViewerCount: (count) => this.onViewerCount(count),
      });
      this.ready = true;
      log.info(`直播间已就绪: ${room}（真实房间号 ${real}）`);
      void this.danmaku.start();
      void this.queueLoop();
      void this.roomCheckLoop();
      void this.cookieCheckLoop();
      if (this.cookieMgr.valid) {
        void this.sender.send("点歌机器人已上线！发送 /play 歌名 歌手 来点歌");
      }
      return;
    }
  }

  stop(): void {
    this.running = false;
    this.ready = false;
    this.vote?.cancel();
    this.danmaku?.stop();
    this.playback.stop();
  }

  private onViewerCount(count: number): void {
    this.viewerCount = count;
    this.vote?.updateViewerCount(count);
  }

  // ---------------------------------------------------------------- 弹幕指令

  private async onDanmaku(text: string, uid: number, username: string): Promise<void> {
    const raw = (text || "").trim();
    if (!raw) return;
    const lower = raw.toLowerCase();
    const kind = lower.startsWith("/play") || lower.startsWith("/lyric") || raw.startsWith("/歌词") ? "request" : "chat";
    hub.addDanmaku(username, raw, kind);

    if (this.vote?.active && ["Y", "N", "YES", "NO"].includes(raw.toUpperCase())) {
      this.vote.handleVote(uid, raw[0]);
      return;
    }

    if (lower.startsWith("/play")) {
      if (!this.accepting) {
        await this.sender?.send("暂停接单中，稍后再点歌哦~");
        return;
      }
      const query = raw.slice(5).trim();
      if (!query) {
        await this.sender?.send("格式: /play 歌名 (可选歌手，如 /play 告白气球 周杰伦)");
        return;
      }
      if (batchPlans(query).length) void this.handlePlayBatch(query, username, uid);
      else void this.handlePlay(query, username, uid);
      return;
    }

    if (lower.startsWith("/lyric") || raw.startsWith("/歌词")) {
      if (!this.accepting) {
        await this.sender?.send("暂停接单中，稍后再点歌哦~");
        return;
      }
      const fragment = raw.startsWith("/歌词") ? raw.slice(3).trim() : raw.slice(6).trim();
      if (!fragment) {
        await this.sender?.send("格式: /lyric 歌词片段（如 /lyric 天青色等烟雨）");
        return;
      }
      void this.handleLyric(fragment, username, uid);
      return;
    }

    if (lower.startsWith("/switch")) {
      const cur = this.currentItem;
      if (cur && this.playback.isPlaying && this.isOwnedBy(cur, username, uid)) {
        void this.skipCurrent(reply("switch_own"));
      } else if (this.playback.isPlaying && !this.vote?.active) {
        void this.handleSwitch();
      } else if (this.vote?.active) {
        await this.sender?.send("投票进行中，请发送 Y 或 N 参与投票");
      } else {
        await this.sender?.send(reply("no_playing"));
      }
      return;
    }

    if (lower.startsWith("/queue") || raw.startsWith("/队列") || raw.startsWith("/点歌列表")) {
      void this.handleQueue();
      return;
    }
    if (lower.startsWith("/now") || raw.startsWith("/当前") || raw.startsWith("/np")) {
      await this.handleNow();
      return;
    }
    if (lower.startsWith("/remove") || raw.startsWith("/撤回")) {
      const arg = raw.includes(" ") ? raw.slice(raw.indexOf(" ") + 1).trim() : "";
      void this.handleRemove(arg, username);
      return;
    }
    if (lower.startsWith("/top") || raw.startsWith("/排行") || raw.startsWith("/点歌排行")) {
      void this.handleTop();
      return;
    }
    if (lower.startsWith("/help") || raw.startsWith("/帮助") || raw.startsWith("/指令")) {
      await this.sender?.send(reply("help"));
    }
  }

  private async handleQueue(): Promise<void> {
    const items = this.queue.snapshot();
    if (!items.length) {
      await this.sender?.send(reply("queue_empty"));
      return;
    }
    const lines = items.slice(0, 10).map((it, i) => `${i + 1}. ${formatSongName(it.song.name)}-${it.requester}`);
    await this.sender?.send(`队列: ${lines.join(" | ")}`);
  }

  private async handleNow(): Promise<void> {
    const song = this.playback.current;
    if (!song) {
      await this.sender?.send(reply("no_playing"));
    } else {
      await this.sender?.send(this.playingText(song, this.currentItem?.requester || ""));
    }
  }

  private async handleRemove(arg: string, username: string): Promise<void> {
    const idx = Number.parseInt(arg, 10);
    const items = this.queue.snapshot();
    if (!Number.isFinite(idx) || idx < 1 || idx > items.length) {
      await this.sender?.send(reply("remove_bad_index"));
      return;
    }
    const item = items[idx - 1];
    if (item.requester && item.requester !== username) {
      await this.sender?.send(reply("remove_denied"));
      return;
    }
    this.queue.removeAt(idx - 1);
    await this.sender?.send(reply("removed", { song: formatSongName(item.song.name) }));
  }

  private async handleTop(): Promise<void> {
    const top = this.stats.top(5);
    if (!top.length) {
      await this.sender?.send("还没有点歌记录，点一首歌开始攒热度吧~");
      return;
    }
    const lines = top.map(([name, count], i) => `${i + 1}. ${name} x${count}`);
    await this.sender?.send(`${reply("top_title", { n: top.length })} ${lines.join(" | ")}`);
  }

  // ---------------------------------------------------------------- 搜索/点歌

  async resolveSingle(query: string): Promise<SongInfo | null> {
    let best: SongInfo | null = null;
    for (const [songName, singer] of splitPlayQuery(query)) {
      const song = await searchSong(songName, singer);
      if (song && (!best || song.score > best.score)) {
        best = song;
        if (best.score >= 135) break;
      }
    }
    if (!best) {
      const noSpace = query.replace(/\s+/g, "");
      if (noSpace !== query) best = await searchSong(noSpace, "");
    }
    if (!best && config.bool("enable_lyrics_search", true) && looksLikeLyric(query)) {
      try {
        best = await resolveFromLyric(query, (keyword, singer = "") => searchSong(keyword, singer));
      } catch (e) {
        log.debug(`歌词兜底搜索异常: ${(e as Error).message}`);
      }
    }
    return best;
  }

  private async handlePlay(query: string, requester: string, uid = 0): Promise<SongInfo | null> {
    await this.sender?.send(reply("searching", { query: query.trim() }));
    try {
      const song = await this.resolveSingle(query);
      if (!song) {
        let suggestion: SongInfo | null = null;
        try {
          suggestion = await suggestSong(query);
        } catch (e) {
          log.debug(`智能建议失败: ${(e as Error).message}`);
        }
        if (suggestion) {
          await this.sender?.send(
            reply("suggest", { query: query.trim(), name: suggestion.name, singer: suggestion.singer }),
          );
        } else {
          await this.sender?.send(reply("not_found", { query: query.trim() }));
        }
        return null;
      }
      const dup = this.findDuplicate(song);
      if (dup === "playing") {
        await this.sender?.send(reply("duplicate_playing", { song: formatSongName(song.name) }));
        return null;
      }
      if (typeof dup === "number") {
        await this.sender?.send(reply("duplicate", { song: formatSongName(song.name), pos: dup }));
        return null;
      }
      const pos = this.queue.add({ song, requester, requesterUid: uid });
      this.stats.record(song.name, song.singer, requester);
      await this.sender?.send(reply("added", { song: formatSongName(song.name), pos }));
      return song;
    } catch (e) {
      log.error(`点歌异常: ${(e as Error).message}`);
      await this.sender?.send(`点歌失败: ${(e as Error).message}`);
      return null;
    }
  }

  private async handleLyric(fragment: string, requester: string, uid = 0): Promise<SongInfo | null> {
    await this.sender?.send(`正在按歌词识别: ${fragment}`.trim());
    try {
      const song = await resolveFromLyric(fragment, (keyword, singer = "") => searchSong(keyword, singer));
      if (!song) {
        await this.sender?.send(`未找到匹配歌曲: ${fragment}`.trim());
        return null;
      }
      const pos = this.queue.add({ song, requester, requesterUid: uid });
      this.stats.record(song.name, song.singer, requester);
      await this.sender?.send(reply("added_lyric", { song: formatSongName(song.name), pos }));
      return song;
    } catch (e) {
      log.error(`歌词点歌异常: ${(e as Error).message}`);
      await this.sender?.send(`歌词点歌失败: ${(e as Error).message}`);
      return null;
    }
  }

  private async handlePlayBatch(query: string, requester: string, uid = 0): Promise<void> {
    const plans = batchPlans(query);
    if (!plans.length) {
      await this.handlePlay(query, requester, uid);
      return;
    }
    for (const [rawGroups, trusted] of plans) {
      const [groups, dropped] = capBatchQueries(rawGroups);
      if (dropped) {
        await this.sender?.send(
          `批量点歌最多 ${maxBatchSongs()} 首，已截断 ${dropped} 首（可在 config.json 的 max_batch_songs 调整）`,
        );
      }
      const n = groups.length;
      if (trusted) {
        log.info(`批量点歌(分号): 共 ${n} 首 -> ${JSON.stringify(groups)}`);
        let ok = 0;
        for (let i = 0; i < groups.length; i += 1) {
          log.info(`批量点歌 [${i + 1}/${n}]: ${groups[i]}`);
          if (await this.handlePlay(groups[i], requester, uid)) ok += 1;
        }
        await this.sender?.send(reply("batch_done", { ok, n }));
        return;
      }
      const songs = await this.verifyBatchGroups(groups);
      if (songs) {
        log.info(`批量点歌(验证通过): ${JSON.stringify(songs.map((s) => [s.name, s.singer, s.platform]))}`);
        for (const song of songs) {
          const pos = this.queue.add({ song, requester, requesterUid: uid });
          this.stats.record(song.name, song.singer, requester);
          await this.sender?.send(reply("added", { song: formatSongName(song.name), pos }));
        }
        await this.sender?.send(reply("batch_done", { ok: songs.length, n }));
        return;
      }
      log.info(`批量候选验证未通过，尝试下一结构: ${JSON.stringify(groups)}`);
    }
    await this.handlePlay(query, requester, uid);
  }

  private async verifyBatchGroups(groups: string[]): Promise<SongInfo[] | null> {
    const songs = await Promise.all(groups.map((g) => this.resolveSingle(g)));
    const seen = new Set<string>();
    const out: SongInfo[] = [];
    for (let i = 0; i < groups.length; i += 1) {
      const song = songs[i];
      if (!song || song.score < BATCH_VERIFY_SCORE) return null;
      const segs = groups[i].split(/\s+/).filter(Boolean);
      if (segs.length && !segs.some((seg) => artistAliasMatch(seg, song.singer) || fuzzyMatch(seg, song.singer))) {
        log.debug(`批量验证歌手不一致: ${groups[i]} -> ${song.singer}`);
        return null;
      }
      const key = `${normalizeText(song.name)}|${normalizeText(song.singer)}`;
      if (seen.has(key)) return null;
      seen.add(key);
      out.push(song);
    }
    return out;
  }

  // ---------------------------------------------------------------- 公共台边界

  private isOwnedBy(item: QueueItem, username: string, uid = 0): boolean {
    if (item.requesterUid && uid) return item.requesterUid === uid;
    return Boolean(item.requester) && item.requester === username;
  }

  findDuplicate(song: SongInfo): "playing" | number | null {
    const same = (a: SongInfo, b: SongInfo): boolean => {
      if (normalizeText(a.name) !== normalizeText(b.name)) return false;
      const na = normalizeText(a.singer);
      const nb = normalizeText(b.singer);
      if (!na || !nb) return true;
      return (canonicalSinger(a.singer) || na) === (canonicalSinger(b.singer) || nb);
    };
    if (this.currentItem && same(this.currentItem.song, song)) return "playing";
    const items = this.queue.snapshot();
    for (let i = 0; i < items.length; i += 1) {
      if (same(items[i].song, song)) return i + 1;
    }
    return null;
  }

  private playingText(song: SongInfo, requester = ""): string {
    let text = reply("playing", { song: formatSongName(song.name) });
    if (requester) text += reply("credit", { user: requester });
    return text;
  }

  private async handleSwitch(): Promise<void> {
    const target = this.playback.current;
    const targetId = target?.songId;
    const stillPlaying = (): boolean => {
      const cur = this.playback.current;
      if (!cur) return false;
      return targetId ? cur.songId === targetId : true;
    };
    await this.vote?.startVote(() => this.skipCurrent(reply("switched")), stillPlaying);
  }

  async skipCurrent(message = ""): Promise<void> {
    this.skipRequested = true;
    this.playback.stop();
    await this.sender?.send(message || reply("switched"));
  }

  /** 外部 API 切歌：只停当前播放，不额外发弹幕（前端已有反馈）。 */
  externalSkip(): boolean {
    if (!this.playback.isPlaying) return false;
    this.skipRequested = true;
    this.playback.stop();
    return true;
  }

  // ---------------------------------------------------------------- 队列消费

  private async queueLoop(): Promise<void> {
    while (this.running) {
      if (this.queue.isEmpty) {
        await sleep(1000);
        continue;
      }
      const item = this.queue.peek();
      if (!item) {
        await sleep(1000);
        continue;
      }
      this.currentItem = item;
      this.skipRequested = false;
      let started = false;
      const onStart = (): void => {
        started = true;
        void this.sender?.send(this.playingText(item.song, item.requester));
      };
      if (item.url) {
        started = (await this.playback.play(item.song, item.url, onStart)) || started;
      } else {
        started = (await this.playWithFallback(item.song, onStart)) || started;
      }
      this.currentItem = null;
      await this.onSongFinish(item, started, this.skipRequested);
    }
  }

  private async tryPlaySong(song: SongInfo, onStart: () => void): Promise<boolean> {
    const minQuality = String(config.get("min_quality", "flac24bit"));
    const minIdx = QUALITY_LADDER.indexOf(minQuality);
    const ladder = QUALITY_LADDER.slice(minIdx >= 0 ? minIdx : 0);
    const urlMap = new Map<string, string>();
    for (const quality of ladder) {
      const hit = cachedMusicUrl(song, quality);
      if (hit) urlMap.set(quality, hit);
    }
    const missing = ladder.filter((q) => !urlMap.has(q));
    log.info(
      `并行获取音质: ${ladder.join(", ")} [${PLATFORM_NAMES[song.platform] || song.platform}]（缓存命中 ${urlMap.size}/${ladder.length}）`,
    );

    const timeoutMs = config.num("url_fetch_timeout", 15) * 1000;
    const pending = missing.map(async (quality) => {
      const url = await lx.getUrl(song, quality, Math.max(3000, timeoutMs));
      if (url && !urlMap.has(quality)) {
        urlMap.set(quality, url);
        cacheMusicUrl(song, quality, url);
        log.info(`获取成功 (${quality}): ${url.slice(0, 80)}...`);
      }
    });
    await Promise.race([Promise.allSettled(pending), sleep(timeoutMs)]);

    if (!urlMap.size) {
      log.error(`所有音质均无法获取URL: ${song.name} - ${song.singer} [${PLATFORM_NAMES[song.platform] || song.platform}]`);
      return false;
    }

    for (const quality of ladder) {
      const url = urlMap.get(quality);
      if (!url) continue;
      const started = await this.playback.play(song, url, onStart);
      if (started) return true;
      invalidateMusicUrl(song, quality);
      log.warn(`音质 ${quality} 下载失败，尝试下一音质...`);
    }
    log.error(`所有音质均无法播放: ${song.name} - ${song.singer} [${PLATFORM_NAMES[song.platform] || song.platform}]`);
    return false;
  }

  private async playWithFallback(song: SongInfo, onStart: () => void): Promise<boolean> {
    if (await this.tryPlaySong(song, onStart)) return true;
    for (const alt of song.alternates || []) {
      log.warn(`主源不可播，跨源回退同歌: ${song.name} - ${song.singer} [${alt.platform}]`);
      if (await this.tryPlaySong(alt, onStart)) return true;
    }
    try {
      const retry = await searchSong(song.name, song.singer, [song.platform]);
      if (retry && retry.platform !== song.platform) {
        log.warn(`主源与同歌候选均不可播，重搜同歌回退: ${retry.name} - ${retry.singer} [${retry.platform}]`);
        if (await this.tryPlaySong(retry, onStart)) return true;
      }
    } catch (e) {
      log.debug(`重搜同歌其他源失败: ${(e as Error).message}`);
    }
    return false;
  }

  private async onSongFinish(item: QueueItem, started: boolean, intentional: boolean): Promise<void> {
    const head = this.queue.peek();
    if (head === item) this.queue.removeCurrent();
    else this.queue.removeAt(this.queue.snapshot().indexOf(item));
    if (intentional) return;
    if (started) {
      await this.sender?.send(reply("played", { song: formatSongName(item.song.name) }));
    } else {
      await this.sender?.send(`播放失败: ${formatSongName(item.song.name)}`);
    }
  }

  // ---------------------------------------------------------------- 后台循环

  private async roomCheckLoop(): Promise<void> {
    const interval = Math.max(10, config.num("room_check_interval", 60));
    while (this.running) {
      await sleep(interval * 1000);
      if (!this.running) return;
      const info = await getRoomInfo(this.roomId);
      if (!info) {
        log.warn("获取房间信息失败");
        continue;
      }
      if (info.liveStatus === 0) {
        log.warn("直播间已关闭，停止弹幕与播放");
        this.danmaku?.stop();
        this.playback.stop();
        this.running = false;
        return;
      }
      log.debug(`直播间状态正常 (live_status=${info.liveStatus})`);
    }
  }

  private async cookieCheckLoop(): Promise<void> {
    while (this.running) {
      await sleep(10000);
      if (!this.running) return;
      try {
        const mtime = this.mtime();
        if (!mtime || mtime === this.configMtime) continue;
        this.configMtime = mtime;
        const before = `${this.cookieMgr.sessdata}|${this.cookieMgr.jct}`;
        config.reload();
        this.cookieMgr.reloadFromConfig();
        const after = `${this.cookieMgr.sessdata}|${this.cookieMgr.jct}`;
        if (before === after) continue;
        log.info("检测到 Cookie 配置变更，重新校验...");
        const ok = await this.cookieMgr.validate();
        if (ok) await this.sender?.send("Cookie 已更新，弹幕发送恢复");
      } catch (e) {
        log.debug(`Cookie 热更新检查异常: ${(e as Error).message}`);
      }
    }
  }

  // ---------------------------------------------------------------- 外部 API 辅助

  async enqueue(song: SongInfo, requester = "主播"): Promise<{ ok: boolean; pos?: number; error?: string; status: number }> {
    const dup = this.findDuplicate(song);
    if (dup === "playing") return { ok: false, error: `《${song.name}》正在播放中`, status: 409 };
    if (typeof dup === "number") return { ok: false, error: `《${song.name}》已在队列第 ${dup} 位`, status: 409 };
    const pos = this.queue.add({ song, requester, requesterUid: 0 });
    this.stats.record(song.name, song.singer, requester);
    log.info(`外部点歌入队: ${song.name} - ${song.singer} (队列:${pos})`);
    void this.sender?.send(`已点: ${song.name} (队列:${pos})`);
    return { ok: true, pos, status: 200 };
  }

  get roomIdResolved(): number {
    return this.roomId;
  }

  get roomDisplay(): string {
    return this.roomInput || config.roomId();
  }

  get buvidReady(): boolean {
    return this.buvidFetched;
  }
}
