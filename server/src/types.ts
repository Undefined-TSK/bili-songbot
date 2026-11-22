/** 共享类型。 */

export interface LxSongRaw {
  [key: string]: unknown;
}

export interface SongInfo {
  name: string;
  singer: string;
  album: string;
  songId: string;
  platform: string;
  interval: string;
  /** 同歌其他平台候选（跨源回退用） */
  alternates: SongInfo[];
  /** 搜索匹配评分 */
  score: number;
  /** lxserver 搜索返回的原始歌曲对象，取链时原样回传最稳妥 */
  raw?: LxSongRaw;
}

export interface QueueItem {
  song: SongInfo;
  url?: string;
  requester: string;
  requesterUid: number;
}

export interface DanmakuEntry {
  id: number;
  ts: number;
  user: string;
  text: string;
  kind: string;
}

export interface LogEntry {
  id: number;
  ts: number;
  level: string;
  name: string;
  msg: string;
}

export function makeSong(partial: Partial<SongInfo> = {}): SongInfo {
  return {
    name: "",
    singer: "",
    album: "",
    songId: "",
    platform: "",
    interval: "",
    alternates: [],
    score: 0,
    ...partial,
  };
}

export function songToDict(song: SongInfo | null | undefined): Record<string, unknown> | null {
  if (!song) return null;
  return {
    name: song.name,
    singer: song.singer,
    album: song.album,
    song_id: song.songId,
    platform: song.platform,
    interval: song.interval,
    score: song.score,
    alternates: (song.alternates || []).map((a) => songToDict(a)),
  };
}

export function songFromDict(raw: unknown): SongInfo | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const name = String(r.name ?? "").trim();
  if (!name) return null;
  const alternates = Array.isArray(r.alternates)
    ? r.alternates.map((a) => songFromDict(a)).filter((a): a is SongInfo => !!a)
    : [];
  return makeSong({
    name,
    singer: String(r.singer ?? ""),
    album: String(r.album ?? ""),
    songId: String(r.song_id ?? r.songId ?? ""),
    platform: String(r.platform ?? ""),
    interval: String(r.interval ?? ""),
    score: Number(r.score ?? 0) || 0,
    alternates,
    raw: (r.raw as LxSongRaw) || undefined,
  });
}
