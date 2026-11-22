/** 歌词点歌：片段 → 候选(歌名,歌手)，真正的解析仍交给搜索内核。 */
import { config } from "./config.js";
import { log } from "./logger.js";
import { pinyinKey } from "./fuzzy.js";
import type { SongInfo } from "./types.js";

const SEARCH_URL = "https://music.163.com/api/search/get";

export function looksLikeLyric(query: string): boolean {
  const q = (query || "").trim();
  if (q.length < 6) return false;
  if ([" - ", "—", "/", "|", "by", "，", "、"].some((d) => q.includes(d))) return false;
  const tokens = q.split(/\s+/).filter(Boolean);
  if (tokens.length >= 2) return true;
  return q.replace(/[\s\W]/g, "").length >= 6;
}

async function neteaseFragment(fragment: string): Promise<Array<[string, string]>> {
  const timeout = config.num("lyrics_timeout", 6) * 1000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const params = new URLSearchParams({ s: fragment, type: "1", limit: "6" });
    const res = await fetch(`${SEARCH_URL}?${params.toString()}`, {
      headers: { Referer: "https://music.163.com/", "User-Agent": "Mozilla/5.0" },
      signal: controller.signal,
    });
    if (!res.ok) return [];
    const data = (await res.json()) as Record<string, unknown>;
    const songs = (((data.result as Record<string, unknown>)?.songs as Array<Record<string, unknown>>) || []);
    const out: Array<[string, string]> = [];
    for (const s of songs) {
      const name = String(s.name ?? "").trim();
      const artists = (s.artists as Array<Record<string, unknown>>) || [];
      const singer = artists.length ? String(artists[0]?.name ?? "").trim() : "";
      if (name) out.push([name, singer]);
    }
    return out;
  } catch (e) {
    log.debug(`歌词片段搜索异常: ${(e as Error).message}`);
    return [];
  } finally {
    clearTimeout(timer);
  }
}

function fragmentInName(fragment: string, name: string): boolean {
  const f = fragment.trim().toLowerCase();
  const n = name.trim().toLowerCase();
  if (!f || !n) return false;
  if (f.includes(n) || n.includes(f)) return true;
  const pf = pinyinKey(f);
  const pn = pinyinKey(n);
  return Boolean(pf && pf === pn);
}

export async function resolveFromLyric(
  fragment: string,
  searchSong: (keyword: string, singer?: string) => Promise<SongInfo | null>,
): Promise<SongInfo | null> {
  const candidates = await neteaseFragment(fragment);
  if (!candidates.length) return null;
  let best: SongInfo | null = null;
  for (const [name] of candidates) {
    if (!name || fragmentInName(fragment, name)) continue;
    const song = await searchSong(name, "");
    if (song && (!best || song.score > best.score)) best = song;
  }
  if (!best) {
    for (const [name] of candidates) {
      if (!name) continue;
      const song = await searchSong(name, "");
      if (song) return song;
    }
  }
  return best;
}
