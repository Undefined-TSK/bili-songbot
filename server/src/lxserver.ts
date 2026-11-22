/** lxserver HTTP 客户端：搜索 / 取链 / 歌词 / 下载。lxserver 独立运行，本服务不内嵌音源。 */
import { config } from "./config.js";
import { log } from "./logger.js";
import type { SongInfo } from "./types.js";
import { makeSong } from "./types.js";

export interface LxSearchItem {
  id?: string | number;
  songmid?: string | number;
  song_id?: string | number;
  name?: string;
  singer?: unknown;
  artist?: unknown;
  albumName?: string;
  album?: string;
  interval?: string | number;
  source?: string;
  meta?: Record<string, unknown>;
  [key: string]: unknown;
}

export function extractArtist(raw: unknown): string {
  if (!raw) return "";
  if (typeof raw === "string") {
    let s = raw;
    for (const sep of ["&", ",", "、", "，", " feat ", " ft ", " feat. ", " ft. "]) {
      s = s.split(sep).join("/");
    }
    const parts = s.split("/").map((p) => p.trim()).filter(Boolean);
    return parts.length ? parts.join("/") : s.trim();
  }
  if (Array.isArray(raw)) {
    const names: string[] = [];
    for (const item of raw) {
      if (typeof item === "string") names.push(item.trim());
      else if (item && typeof item === "object") {
        const o = item as Record<string, unknown>;
        const name = o.name ?? o.singer ?? o.artist ?? "";
        if (name) names.push(String(name).trim());
      } else if (typeof item === "number") {
        names.push(String(item));
      }
    }
    return names.join("/");
  }
  if (typeof raw === "object") {
    const o = raw as Record<string, unknown>;
    return extractArtist(o.name ?? o.singer ?? o.artist ?? "");
  }
  return String(raw);
}

function normalizeItem(item: LxSearchItem, source: string): SongInfo {
  const meta = (item.meta || {}) as Record<string, unknown>;
  const idRaw = item.songmid ?? item.id ?? item.song_id ?? meta.songId ?? "";
  let songId = String(idRaw ?? "");
  if (songId.startsWith("MUSIC_")) songId = songId.slice(6);
  return makeSong({
    name: String(item.name ?? "").trim(),
    singer: extractArtist(item.singer ?? item.artist),
    album: String(item.albumName ?? item.album ?? meta.albumName ?? ""),
    songId,
    platform: source,
    interval: item.interval === undefined || item.interval === null ? "" : String(item.interval),
    raw: item as Record<string, unknown>,
  });
}

interface FetchOptions {
  timeoutMs?: number;
  method?: string;
  body?: unknown;
}

class LxServer {
  private probeCache = { at: 0, ready: false };
  private probeInflight: Promise<boolean> | null = null;

  base(): string {
    return config.lxUrl();
  }

  private async request(path: string, opts: FetchOptions = {}): Promise<unknown> {
    const url = `${this.base()}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 8000);
    try {
      const res = await fetch(url, {
        method: opts.method ?? "GET",
        headers: opts.body ? { "Content-Type": "application/json" } : undefined,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
      const text = await res.text();
      let data: unknown = text;
      try {
        data = JSON.parse(text);
      } catch {
        /* 纯文本错误也原样返回 */
      }
      if (!res.ok) {
        const msg =
          data && typeof data === "object"
            ? String((data as Record<string, unknown>).error ?? (data as Record<string, unknown>).message ?? text)
            : String(text);
        throw new Error(`HTTP ${res.status}: ${msg.slice(0, 200)}`);
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  /** 健康探测：搜索接口可返回即视为就绪（带 15 秒缓存）。 */
  async probe(force = false): Promise<boolean> {
    if (!force && Date.now() - this.probeCache.at < 15000) return this.probeCache.ready;
    if (this.probeInflight) return this.probeInflight;
    this.probeInflight = (async () => {
      try {
        const data = (await this.request(
          "/api/music/search?name=test&source=wy&type=song&page=1&limit=1",
          { timeoutMs: 2000 },
        )) as Record<string, unknown>;
        const payload = (data?.data as Record<string, unknown>) || data;
        const ok = Boolean(payload && (Array.isArray(payload.list) || payload.list === undefined));
        this.probeCache = { at: Date.now(), ready: ok };
        return ok;
      } catch {
        this.probeCache = { at: Date.now(), ready: false };
        return false;
      } finally {
        this.probeInflight = null;
      }
    })();
    return this.probeInflight;
  }

  async search(source: string, keyword: string, limit = 5): Promise<SongInfo[]> {
    const params = new URLSearchParams({
      name: keyword,
      source,
      type: "song",
      page: "1",
      limit: String(limit),
    });
    const data = (await this.request(`/api/music/search?${params.toString()}`, { timeoutMs: 8000 })) as Record<
      string,
      unknown
    >;
    const payload = (data?.data as Record<string, unknown>) || data;
    const list = (payload.list as LxSearchItem[]) || [];
    if (!Array.isArray(list)) return [];
    return list
      .filter((it) => it && typeof it === "object" && it.name)
      .map((it) => normalizeItem(it, source));
  }

  async getUrl(song: SongInfo, quality: string, timeoutMs = 12000): Promise<string | null> {
    const songInfo =
      song.raw && typeof song.raw === "object"
        ? song.raw
        : {
            songmid: song.songId,
            hash: song.songId,
            source: song.platform,
            name: song.name,
            singer: song.singer,
            albumName: song.album,
            interval: song.interval,
          };
    try {
      const data = (await this.request("/api/music/url", {
        method: "POST",
        timeoutMs,
        body: {
          songInfo,
          quality,
          enableAutoSwitchApiSource: true,
          excludeApiSources: [],
        },
      })) as Record<string, unknown>;
      const payload = (data?.data as Record<string, unknown>) || data;
      const url = payload?.url;
      if (typeof url === "string" && /^https?:/i.test(url)) return url;
      return null;
    } catch (e) {
      log.debug(`取链失败 [${song.platform}/${quality}]: ${(e as Error).message}`);
      return null;
    }
  }

  async lyric(source: string, songmid: string, name = "", singer = ""): Promise<Record<string, string>> {
    const params = new URLSearchParams({ source, songmid, name, singer });
    const data = (await this.request(`/api/music/lyric?${params.toString()}`, { timeoutMs: 8000 })) as Record<
      string,
      unknown
    >;
    const payload = (data?.data as Record<string, unknown>) || data;
    const out: Record<string, string> = {};
    for (const key of ["lyric", "tlyric", "rlyric", "lxlyric"]) {
      if (typeof payload?.[key] === "string") out[key] = payload[key] as string;
    }
    return out;
  }

  /** 供 /api/sources 展示：平台即 lxserver 已在用的音源集合。 */
  platforms(): string[] {
    return ["kw", "wy", "tx", "kg", "mg"];
  }
}

export const lx = new LxServer();
