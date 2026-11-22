/** 配置：config.json 加载 / 原子写入 / 热更新读取，全部字段保持向后兼容。 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const PROJECT_ROOT = path.resolve(here, "..", "..");

export const CONFIG_PATH = process.env.SONGBOT_CONFIG || path.join(PROJECT_ROOT, "config.json");

export type ConfigData = Record<string, unknown> & {
  bili_room_id?: string | number;
  bili_sessdata?: string;
  bili_jct?: string;
  bili_buvid3?: string;
  bili_buvid4?: string;
  lxserver_url?: string;
  ffplay_path?: string;
  ffplay_args?: string[];
  min_quality?: string;
  player_backend?: string;
  queue_file?: string;
  stats_file?: string;
  log_file?: string;
  log_level?: string;
  api_port?: number;
  replies?: Record<string, string>;
};

const DEFAULTS: ConfigData = {
  bili_room_id: "",
  bili_sessdata: "",
  bili_jct: "",
  bili_buvid3: "",
  bili_buvid4: "",
  lxserver_url: "http://127.0.0.1:9527",
  ffplay_path: "ffplay",
  ffplay_args: ["-nodisp", "-autoexit", "-loglevel", "quiet"],
  player_backend: "ffplay",
  min_quality: "flac24bit",
  enable_lyrics_search: true,
  enable_pinyin_fuzzy: true,
  max_msg_per_minute: 20,
  danmaku_interval: 5,
  danmaku_max_len: 20,
  api_rate_interval: 1.0,
  room_check_interval: 60,
  vote_duration: 30,
  vote_broadcast_interval: 5,
  url_fetch_timeout: 15,
  lyrics_timeout: 6,
  search_cache_ttl: 60,
  url_cache_ttl: 240,
  cache_max_entries: 256,
  max_batch_songs: 10,
  queue_file: "queue.json",
  stats_file: "song_stats.json",
  log_file: "logs/bot.log",
  log_level: "INFO",
  log_max_bytes: 5 * 1024 * 1024,
  log_backup_count: 3,
  log_max_age_days: 7,
  log_max_total_mb: 50,
};

const DEFAULT_REPLIES: Record<string, string> = {
  searching: "正在搜索: {query}",
  not_found: "未找到: {query}",
  suggest: "未找到: {query}，你是不是想点《{name}》-{singer}？回复 /play {name} {singer} 可直接点播",
  added: "已点: {song} (队列:{pos})",
  added_lyric: "已点(歌词): {song} (队列:{pos})",
  playing: "正在播放: {song}",
  credit: "（{user} 点播）",
  played: "播放完毕: {song}",
  switched: "已切歌",
  switch_own: "已切歌（自己点的歌自己做主~）",
  duplicate: "《{song}》已经在队列里啦（第{pos}位），不用重复点哦~",
  duplicate_playing: "《{song}》正在播放中，不用重复点哦~",
  batch_done: "🎵 批量点歌完成: {ok}/{n} 首已按顺序加入队列",
  help: "/play 歌名(歌手) 点歌(支持批量/歌词/拼音错字) | /lyric 歌词点歌 | /queue 队列 | /now 正在播放 | /remove 序号 撤回 | /top 点歌排行 | /switch 投票切歌",
  queue_empty: "点歌队列为空，快来 /play 点歌吧~",
  no_playing: "当前没有在播放的歌曲",
  removed: "已撤回: {song}",
  remove_denied: "只能撤回自己点的歌哦~",
  remove_bad_index: "撤回失败：请输入队列中存在的序号（/queue 查看）",
  top_title: "点歌排行 TOP{n}：",
};

export class Config {
  data: ConfigData = {};

  constructor() {
    this.reload();
  }

  reload(): ConfigData {
    try {
      const text = fs.readFileSync(CONFIG_PATH, "utf8");
      const parsed = JSON.parse(text);
      this.data = parsed && typeof parsed === "object" ? (parsed as ConfigData) : {};
    } catch {
      // 首次启动没有 config.json 时用默认值继续，API 仍可用
      this.data = {};
    }
    return this.data;
  }

  get<T = unknown>(key: string, fallback?: T): T {
    const value = this.data[key];
    if (value === undefined || value === null || value === "") {
      if (fallback !== undefined) return fallback;
      return DEFAULTS[key] as T;
    }
    return value as T;
  }

  num(key: string, fallback: number): number {
    const v = Number(this.get(key, fallback));
    return Number.isFinite(v) ? v : fallback;
  }

  bool(key: string, fallback: boolean): boolean {
    const v = this.get<unknown>(key, fallback);
    return typeof v === "string" ? v.toLowerCase() !== "false" : Boolean(v);
  }

  roomId(): string {
    return String(this.get("bili_room_id", "") ?? "").trim();
  }

  lxUrl(): string {
    return String(this.get("lxserver_url", "http://127.0.0.1:9527")).replace(/\/+$/, "");
  }

  dataPath(key: string, fallback: string): string {
    const raw = String(this.get(key, fallback) || "").trim();
    if (!raw) return "";
    return path.isAbsolute(raw) ? raw : path.join(path.dirname(CONFIG_PATH), raw);
  }

  replies(): Record<string, string> {
    const custom = (this.data.replies as Record<string, string>) || {};
    return { ...DEFAULT_REPLIES, ...custom };
  }

  save(mutate: (data: ConfigData) => void): void {
    this.reload();
    mutate(this.data);
    fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
    const tmp = `${CONFIG_PATH}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2), "utf8");
    fs.renameSync(tmp, CONFIG_PATH);
  }
}

export const config = new Config();

export function reply(key: string, vars: Record<string, unknown> = {}): string {
  const tpl = config.replies()[key] ?? key;
  return tpl.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}
