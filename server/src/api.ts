/** 本地 HTTP API：保持原 /api/* 契约，供现有桌面前端直接使用。 */
import http from "node:http";
import { CONFIG_PATH, config, type ConfigData } from "./config.js";
import type { BiliMusicBot } from "./bot.js";
import { hub } from "./events.js";
import { log } from "./logger.js";
import { lx } from "./lxserver.js";
import { QUALITY_LADDER, suggestSong } from "./search.js";
import { songFromDict, songToDict, type SongInfo } from "./types.js";
import { maskSecret } from "./util.js";

const DEFAULT_PORT = 8790;
const MAX_BODY = 64 * 1024;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_resolve, reject) => setTimeout(() => reject(new Error("timeout")), ms)),
  ]);
}

export class ApiServer {
  private server: http.Server | null = null;
  private readonly token = (process.env.SONGBOT_API_TOKEN || "").trim();
  port: number | null = null;

  constructor(private bot: BiliMusicBot) {}

  async start(): Promise<number> {
    const desired = Number(process.env.SONGBOT_API_PORT || config.get("api_port", DEFAULT_PORT) || DEFAULT_PORT);
    for (let port = desired; port < desired + 10; port += 1) {
      try {
        await this.listen(port);
        this.port = port;
        log.info(`本地 API 就绪: http://127.0.0.1:${port}${this.token ? "（token 已启用）" : ""}`);
        return port;
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== "EADDRINUSE") log.warn(`API 端口 ${port} 启动失败: ${(e as Error).message}`);
      }
    }
    throw new Error(`本地 API 端口被占用（${desired}-${desired + 9}）`);
  }

  stop(): void {
    try {
      this.server?.close();
    } catch {
      /* ignore */
    }
    this.server = null;
  }

  private listen(port: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const server = http.createServer((req, res) => {
        void this.handle(req, res);
      });
      const onError = (e: Error): void => {
        server.close();
        reject(e);
      };
      server.once("error", onError);
      server.listen(port, "127.0.0.1", () => {
        server.off("error", onError);
        this.server = server;
        resolve();
      });
    });
  }

  // ---------------------------------------------------------------- 基础

  private cors(res: http.ServerResponse): void {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, X-SongBot-Token");
    res.setHeader("Cache-Control", "no-store");
  }

  private json(res: http.ServerResponse, status: number, payload: unknown): void {
    const body = JSON.stringify(payload);
    this.cors(res);
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
    res.end(body);
  }

  private async body(req: http.IncomingMessage): Promise<Record<string, unknown>> {
    return new Promise((resolve) => {
      const chunks: Buffer[] = [];
      let size = 0;
      req.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > MAX_BODY) {
          req.destroy();
          resolve({});
          return;
        }
        chunks.push(chunk);
      });
      req.on("end", () => {
        if (!chunks.length) return resolve({});
        try {
          const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          resolve(parsed && typeof parsed === "object" ? parsed : {});
        } catch {
          resolve({});
        }
      });
      req.on("error", () => resolve({}));
    });
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    const method = req.method || "GET";
    if (method === "OPTIONS") {
      this.cors(res);
      res.writeHead(204);
      res.end();
      return;
    }
    const parsed = new URL(req.url || "/", "http://127.0.0.1");
    const path = parsed.pathname;
    if (this.token && path !== "/api/health" && req.headers["x-songbot-token"] !== this.token) {
      this.json(res, 401, { ok: false, error: "token 无效" });
      return;
    }
    try {
      await this.route(method, path, parsed, req, res);
    } catch (e) {
      log.error(`API ${path} 异常: ${(e as Error).message}`);
      this.json(res, 500, { ok: false, error: (e as Error).message });
    }
  }

  private async route(
    method: string,
    path: string,
    urlObj: URL,
    req: http.IncomingMessage,
    res: http.ServerResponse,
  ): Promise<void> {
    const key = `${method} ${path}`;
    const bot = this.bot;
    switch (key) {
      case "GET /api/health":
        return this.json(res, 200, {
          ok: true,
          status: "ok",
          port: this.port,
          uptime: Math.round((Date.now() / 1000 - hub.startedAt / 1000) * 10) / 10,
        });
      case "GET /api/snapshot": {
        const counters = hub.snapshotCounters();
        return this.json(res, 200, {
          ok: true,
          room: { id: bot.roomDisplay, connected: Boolean(bot.danmaku?.connected), accepting: bot.accepting },
          viewers: bot.viewerCount,
          playback: this.playbackPayload(),
          queue: this.queuePayload(),
          stats: {
            songs_total: bot.stats.total(),
            songs_today: bot.stats.today(),
            uptime: Math.round((Date.now() / 1000 - bot.startedAt) * 10) / 10,
            danmaku_rate: counters.danmaku_rate,
            top: bot.stats.top(5).map(([name, count]) => ({ name, count })),
          },
          sources: await this.sourcesCore(),
          danmaku: hub.danmakuRecent(30),
          activity: hub.activity(),
          last_event_id: counters.danmaku_total,
        });
      }
      case "GET /api/queue":
        return this.json(res, 200, { ok: true, ...this.queuePayload() });
      case "POST /api/queue/remove": {
        const body = await this.body(req);
        const idx = Number(body.index);
        if (!Number.isInteger(idx)) return this.json(res, 400, { ok: false, error: "index 无效" });
        const item = bot.queue.removeAt(idx);
        if (!item) return this.json(res, 404, { ok: false, error: "队列位置不存在" });
        log.info(`队列移除: ${item.song.name} - ${item.song.singer}`);
        return this.json(res, 200, { ok: true, queue: this.queuePayload() });
      }
      case "POST /api/queue/pin": {
        const body = await this.body(req);
        const idx = Number(body.index);
        if (!Number.isInteger(idx)) return this.json(res, 400, { ok: false, error: "index 无效" });
        const target = bot.currentItem && bot.queue.size > 1 ? 1 : 0;
        const item = bot.queue.moveTo(idx, target);
        if (!item) return this.json(res, 404, { ok: false, error: "队列位置不存在" });
        log.info(`队列置顶: ${item.song.name}`);
        return this.json(res, 200, { ok: true, queue: this.queuePayload() });
      }
      case "POST /api/queue/clear": {
        const keep = Boolean(bot.currentItem);
        bot.queue.clear(keep);
        log.info(`队列已清空${keep ? "（保留正在播放）" : ""}`);
        return this.json(res, 200, { ok: true, queue: this.queuePayload() });
      }
      case "POST /api/queue/skip": {
        if (!bot.playback.isPlaying) return this.json(res, 409, { ok: false, error: "当前没有在播放的歌曲" });
        bot.externalSkip();
        log.info("外部请求切歌");
        return this.json(res, 200, { ok: true, playback: this.playbackPayload() });
      }
      case "POST /api/play": {
        const body = await this.body(req);
        let song: SongInfo | null = body.song ? songFromDict(body.song) : null;
        const query = String(body.query ?? "").trim();
        if (!song && query) {
          try {
            song = await withTimeout(bot.resolveSingle(query), 20000);
          } catch (e) {
            log.error(`外部搜索失败: ${(e as Error).message}`);
            song = null;
          }
        }
        if (!song) return this.json(res, 404, { ok: false, error: "未找到匹配歌曲" });
        const result = await bot.enqueue(song, String(body.requester ?? "主播"));
        return this.json(res, result.status, {
          ok: result.ok,
          ...(result.ok ? { pos: result.pos, song: songToDict(song) } : { error: result.error }),
        });
      }
      case "POST /api/search": {
        const body = await this.body(req);
        const query = String(body.query ?? "").trim();
        if (!query) return this.json(res, 400, { ok: false, error: "请输入歌名或歌手" });
        let song: SongInfo | null = null;
        try {
          song = await withTimeout(bot.resolveSingle(query), 20000);
        } catch (e) {
          log.error(`搜索失败: ${(e as Error).message}`);
        }
        const results: unknown[] = [];
        if (song) {
          results.push(songToDict(song));
          for (const alt of song.alternates || []) results.push(songToDict(alt));
        }
        let suggestion: unknown = null;
        if (!song) {
          try {
            suggestion = songToDict(await withTimeout(suggestSong(query), 4000));
          } catch {
            suggestion = null;
          }
        }
        return this.json(res, 200, { ok: true, query, results, suggestion });
      }
      case "POST /api/live": {
        const body = await this.body(req);
        const text = String(body.text ?? "").trim();
        if (!text) return this.json(res, 400, { ok: false, error: "弹幕内容为空" });
        if (!bot.sender) return this.json(res, 503, { ok: false, error: "弹幕发送尚未就绪" });
        if (!bot.cookieMgr.valid) return this.json(res, 400, { ok: false, error: "Cookie 已失效，无法发送弹幕" });
        await bot.sender.send(text);
        return this.json(res, 200, { ok: true });
      }
      case "GET /api/logs": {
        const after = Number(urlObj.searchParams.get("after") || 0) || 0;
        const limit = Math.min(300, Math.max(1, Number(urlObj.searchParams.get("limit") || 200) || 200));
        const items = hub.logsAfter(after, limit);
        const last = items.length ? items[items.length - 1].id : after;
        return this.json(res, 200, { ok: true, items, last_id: last });
      }
      case "GET /api/danmaku": {
        const after = Number(urlObj.searchParams.get("after") || 0) || 0;
        return this.json(res, 200, { ok: true, items: hub.danmakuAfter(after, 100) });
      }
      case "GET /api/sources": {
        const payload = await this.sourcesCore();
        return this.json(res, 200, {
          ok: true,
          ...payload,
          items: [
            {
              name: "LX Music Server",
              file: config.lxUrl(),
              ready: payload.ready > 0,
              sources: payload.platforms,
            },
          ],
        });
      }
      case "GET /api/settings":
        return this.json(res, 200, { ok: true, settings: await this.settingsPayload() });
      case "PUT /api/settings":
        return this.saveSettings(req, res);
      case "POST /api/player/pause": {
        const ok = bot.playback.pause();
        return this.json(res, ok ? 200 : 409, { ok, playback: this.playbackPayload() });
      }
      case "POST /api/player/resume": {
        const ok = bot.playback.resume();
        return this.json(res, ok ? 200 : 409, { ok, playback: this.playbackPayload() });
      }
      case "POST /api/player/volume": {
        const body = await this.body(req);
        const volume = Number(body.volume);
        if (!Number.isFinite(volume)) return this.json(res, 400, { ok: false, error: "volume 无效" });
        bot.playback.setVolume(volume);
        return this.json(res, 200, {
          ok: true,
          volume: (this.playbackPayload().volume as number) ?? 100,
          playback: this.playbackPayload(),
        });
      }
      case "POST /api/bot/accepting": {
        const body = await this.body(req);
        bot.accepting = "accepting" in body ? Boolean(body.accepting) : !bot.accepting;
        log.info(bot.accepting ? "已恢复接单" : "已暂停接单");
        void bot.sender?.send(bot.accepting ? "已恢复接单，欢迎点歌~" : "已暂停接单，稍后再来~");
        return this.json(res, 200, { ok: true, accepting: bot.accepting });
      }
      default:
        return this.json(res, 404, { ok: false, error: "not found" });
    }
  }

  // ---------------------------------------------------------------- 载荷

  private queuePayload(): Record<string, unknown> {
    const bot = this.bot;
    const items = bot.queue.snapshot();
    const currentItem = bot.currentItem;
    const skipFirst = Boolean(currentItem && items.length && items[0] === currentItem);
    const pending: unknown[] = [];
    let pos = 0;
    for (let idx = skipFirst ? 1 : 0; idx < items.length; idx += 1) {
      pos += 1;
      pending.push({
        index: idx,
        pos,
        song: songToDict(items[idx].song),
        requester: items[idx].requester,
      });
    }
    const currentSong = bot.playback.current || currentItem?.song || null;
    return {
      current: currentSong
        ? { song: songToDict(currentSong), requester: currentItem?.requester || "" }
        : null,
      pending,
      size: bot.queue.size,
    };
  }

  private playbackPayload(): Record<string, unknown> {
    const bot = this.bot;
    const state = bot.playback.state();
    const song = bot.playback.current || bot.currentItem?.song || null;
    state.song = songToDict(song);
    state.requester = bot.currentItem?.requester || "";
    return state;
  }

  private async sourcesCore(): Promise<{ total: number; ready: number; platforms: string[] }> {
    const ready = await lx.probe();
    const platforms = ready ? lx.platforms() : [];
    return { total: 1, ready: ready ? 1 : 0, platforms };
  }

  private async settingsPayload(): Promise<Record<string, unknown>> {
    const data = config.data;
    const sessdata = String(data.bili_sessdata ?? "");
    const jct = String(data.bili_jct ?? "");
    return {
      room_id: String(data.bili_room_id ?? ""),
      has_sessdata: Boolean(sessdata),
      sessdata_hint: maskSecret(sessdata),
      jct_hint: maskSecret(jct),
      buvid3_hint: maskSecret(String(data.bili_buvid3 ?? "")),
      cookie_status: this.bot.cookieMgr.valid ? "有效" : this.bot.cookieMgr.reason || "未检测",
      cookie_valid: Boolean(this.bot.cookieMgr.valid),
      player_backend: String(data.player_backend ?? "ffplay"),
      min_quality: String(data.min_quality ?? "flac24bit"),
      enable_lyrics_search: Boolean(data.enable_lyrics_search ?? true),
      max_msg_per_minute: Number(data.max_msg_per_minute ?? 20),
      danmaku_interval: Number(data.danmaku_interval ?? 5),
      qualities: [...QUALITY_LADDER],
      backends: ["ffplay"],
      api_port: this.port || Number(data.api_port ?? DEFAULT_PORT),
      lxserver_url: config.lxUrl(),
      lxserver_ready: await lx.probe(),
      ffplay_path: String(data.ffplay_path ?? "ffplay"),
    };
  }

  private async saveSettings(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    const body = await this.body(req);
    const cfg: ConfigData = JSON.parse(JSON.stringify(config.data || {})) as ConfigData;
    const changed: string[] = [];
    const restartRequired: string[] = [];
    const put = (key: string, value: unknown): void => {
      if (JSON.stringify(cfg[key]) !== JSON.stringify(value)) {
        cfg[key] = value;
        changed.push(key);
      }
    };

    for (const key of ["bili_sessdata", "bili_jct", "bili_buvid3", "bili_buvid4"]) {
      if (key in body) {
        const value = String(body[key] ?? "").trim();
        if (value && !value.startsWith("•")) put(key, value);
      }
    }
    if ("bili_room_id" in body) {
      const room = String(body.bili_room_id ?? "").trim();
      if (/^\d+$/.test(room) && room !== String(cfg.bili_room_id ?? "")) {
        put("bili_room_id", room);
        restartRequired.push("bili_room_id");
      }
    }
    if ("player_backend" in body) {
      const backend = String(body.player_backend ?? "").toLowerCase();
      if (backend === "ffplay" && backend !== cfg.player_backend) {
        put("player_backend", backend);
        restartRequired.push("player_backend");
      }
    }
    if ("min_quality" in body) {
      const quality = String(body.min_quality ?? "");
      if (QUALITY_LADDER.includes(quality)) put("min_quality", quality);
    }
    if ("enable_lyrics_search" in body) put("enable_lyrics_search", Boolean(body.enable_lyrics_search));
    if ("max_msg_per_minute" in body) {
      const value = Number(body.max_msg_per_minute);
      if (Number.isFinite(value)) put("max_msg_per_minute", Math.max(1, Math.min(100, Math.round(value))));
    }
    if ("danmaku_interval" in body) {
      const value = Number(body.danmaku_interval);
      if (Number.isFinite(value)) put("danmaku_interval", Math.max(0, Math.min(60, value)));
    }
    if ("api_port" in body) {
      const value = Number(body.api_port);
      if (Number.isInteger(value) && value >= 1024 && value <= 65000 && value !== cfg.api_port) {
        put("api_port", value);
        restartRequired.push("api_port");
      }
    }
    if ("lxserver_url" in body) {
      const value = String(body.lxserver_url ?? "").trim().replace(/\/+$/, "");
      if (/^https?:\/\//i.test(value)) put("lxserver_url", value);
    }
    if ("ffplay_path" in body) {
      const value = String(body.ffplay_path ?? "").trim();
      if (value) put("ffplay_path", value);
    }

    if (changed.length) {
      try {
        config.save((data) => {
          for (const key of changed) data[key] = cfg[key];
        });
      } catch (e) {
        log.error(`写入 config.json 失败: ${(e as Error).message}`);
        return this.json(res, 500, { ok: false, error: `保存配置失败: ${(e as Error).message}` });
      }
      if (changed.some((k) => k.startsWith("bili_"))) this.bot.cookieMgr.reloadFromConfig();
      log.info(
        `设置已保存: ${changed.join(", ")}${restartRequired.length ? `（需重启: ${restartRequired.join(", ")}）` : ""}`,
      );
    }

    return this.json(res, 200, {
      ok: true,
      changed,
      restart_required: restartRequired,
      settings: await this.settingsPayload(),
      config_path: CONFIG_PATH,
    });
  }
}
