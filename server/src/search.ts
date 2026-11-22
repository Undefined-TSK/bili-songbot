/** 搜索与匹配：并行多源搜索、评分择优、跨源共识、取链（含音质阶梯）。 */
import { ratio, partial_ratio } from "fuzzball";
import { artistAliasMatch, canonicalDisplay, canonicalSinger } from "./artist-alias.js";
import { config } from "./config.js";
import { log } from "./logger.js";
import { lx } from "./lxserver.js";
import { makeSong, type SongInfo } from "./types.js";
import { jaccardSimilarity, isSubsequence, normalizeText, sleep } from "./util.js";
import { pinyinMatch } from "./fuzzy.js";
import { TTLCache } from "./cache.js";

export const PLATFORM_NAMES: Record<string, string> = {
  wy: "网易云",
  tx: "QQ音乐",
  kw: "酷我",
  kg: "酷狗",
  mg: "咪咕",
};

export const QUALITY_LADDER = ["flac24bit", "flac", "320k", "192k", "128k"];

const SOURCES = ["kw", "wy", "tx", "kg", "mg"];

const FUZZY_THRESHOLD = 60;
const MIN_ACCEPT_SCORE = 20;
const SUGGEST_MIN_SCORE = 60;
const EARLY_RETURN_SCORE = 130;
const CONSENSUS_BONUS = 35;

const ttl = config.num("search_cache_ttl", 60);
const urlTtl = config.num("url_cache_ttl", 240);
const maxEntries = config.num("cache_max_entries", 256);
const searchCache = new TTLCache<SongInfo>(ttl, maxEntries);
const urlCache = new TTLCache<string>(urlTtl, maxEntries);

const CJK_RE = /[\u4e00-\u9fff]/;

export const VERSION_NOISE = [
  "lofi", "remix", "cover", "翻唱", "dj", "混音", "伴奏", "现场",
  "live", "acoustic", "instrumental", "radio edit", "extended",
  "试听", "片段", "铃声", "纯音乐", "钢琴", "高燃", "电音", "舞曲", "卡点",
  "抖音", "快手", "车载", "怀旧", "慢四", "重制", "重新", "新版", "改编", "串烧", "vip",
  "母带", "臻品", "慢摇", "伤感", "对唱", "合唱", "国风", "彩铃",
];

export function fuzzyMatch(keyword: string, target: string): boolean {
  const normKw = normalizeText(keyword);
  const normTarget = normalizeText(target);
  if (!normKw || !normTarget) return false;
  if (normTarget.includes(normKw) || normKw.includes(normTarget)) return true;
  if (config.bool("enable_pinyin_fuzzy", true) && pinyinMatch(keyword, target)) return true;
  if (normKw.length <= 2) {
    const ratioOk = ratio(normKw, normTarget) >= 90;
    const jaccardOk = jaccardSimilarity(normKw, normTarget) >= 0.8;
    if ((Math.abs(normKw.length - normTarget.length) <= 1 && (ratioOk || isSubsequence(normKw, normTarget))) || jaccardOk) {
      return true;
    }
    return false;
  }
  let score = Math.max(ratio(normKw, normTarget), partial_ratio(normKw, normTarget));
  if (CJK_RE.test(normKw + normTarget)) {
    score = Math.max(score, jaccardSimilarity(normKw, normTarget) * 100);
    if (Math.abs(normKw.length - normTarget.length) <= 2 && isSubsequence(normKw, normTarget)) {
      score = Math.max(score, 88);
    }
  }
  return score >= FUZZY_THRESHOLD;
}

export function detectQueryVersion(query: string): string | null {
  const q = (query || "").toLowerCase();
  for (const v of VERSION_NOISE) if (q.includes(v.toLowerCase())) return v.toLowerCase();
  return null;
}

export function scoreSearchResult(
  name: string,
  resultSinger: string,
  keyword: string,
  singer: string,
  queryVersion: string | null = null,
): number {
  let score = 0;
  const normName = normalizeText(name);
  const normKw = normalizeText(keyword);
  if (normName === normKw) score += 100;
  else if (normName.startsWith(normKw)) score += 80;
  else if (normName.includes(normKw)) score += 50;
  else if (fuzzyMatch(keyword, name)) score += 30;
  else if (config.bool("enable_pinyin_fuzzy", true) && pinyinMatch(keyword, name)) score += 30;
  else score += 10;

  const low = name.toLowerCase();
  if (queryVersion && low.includes(queryVersion)) {
    score += 25;
  } else {
    for (const p of VERSION_NOISE) {
      if (low.includes(p.toLowerCase())) {
        score -= 55;
        break;
      }
    }
  }

  if (singer) {
    const normSinger = normalizeText(singer);
    const normRSinger = normalizeText(resultSinger);
    if (normSinger === normRSinger) score += 50;
    else if (normRSinger.includes(normSinger) || normSinger.includes(normRSinger)) score += 30;
    else if (artistAliasMatch(singer, resultSinger)) score += 30;
    else if (fuzzyMatch(singer, resultSinger)) score += 15;
    else if (config.bool("enable_pinyin_fuzzy", true) && pinyinMatch(singer, resultSinger)) score += 15;
    else score -= 10;
  }
  return score;
}

function consensusKey(name: string, singer: string): string {
  const canon = canonicalSinger(singer);
  return `${normalizeText(name)}|${canon || normalizeText(singer)}`;
}

interface SourceBest {
  score: number;
  song: SongInfo;
}

function selectBestWithConsensus(
  topBySource: Map<string, SourceBest>,
  fallback: { score: number; source: string; song: SongInfo } | null,
): { score: number; source: string; song: SongInfo } | null {
  if (!topBySource.size) return fallback;
  const counts = new Map<string, number>();
  for (const { song } of topBySource.values()) {
    const key = consensusKey(song.name, song.singer);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  let bestKey = "";
  let bestCount = 0;
  for (const [key, count] of counts) {
    if (count > bestCount) {
      bestKey = key;
      bestCount = count;
    }
  }
  const candidates: Array<{ score: number; source: string; song: SongInfo }> = [];
  for (const [source, entry] of topBySource) {
    const key = consensusKey(entry.song.name, entry.song.singer);
    const bonus = bestCount >= 2 && key === bestKey ? CONSENSUS_BONUS : 0;
    candidates.push({ score: entry.score + bonus, source, song: entry.song });
  }
  candidates.sort((a, b) => b.score - a.score);
  return candidates[0] || fallback;
}

const searchKey = (keyword: string, singer: string, exclude?: string[]): string =>
  `${keyword}\u0000${singer || ""}\u0000${(exclude || []).slice().sort().join(",")}`;

export function cachedMusicUrl(song: SongInfo, quality: string): string | undefined {
  return urlCache.get(`${song.platform}\u0000${song.songId || `${song.name}|${song.singer}`}\u0000${quality}`);
}

export function cacheMusicUrl(song: SongInfo, quality: string, url: string): void {
  if (url && /^https?:/i.test(url)) {
    urlCache.set(`${song.platform}\u0000${song.songId || `${song.name}|${song.singer}`}\u0000${quality}`, url);
  }
}

export function invalidateMusicUrl(song: SongInfo, quality: string): void {
  urlCache.invalidate(`${song.platform}\u0000${song.songId || `${song.name}|${song.singer}`}\u0000${quality}`);
}

export async function searchSong(keyword: string, singer = "", excludeSource?: string[]): Promise<SongInfo | null> {
  if (!(await lx.probe())) {
    log.warn(`音源服务不可用（${config.lxUrl()}），请先启动 lxserver`);
    return null;
  }
  const searchKw = singer ? `${keyword} ${singer}` : keyword;
  const queryVersion = detectQueryVersion(searchKw);
  const key = searchKey(keyword, singer, excludeSource);
  const cached = searchCache.get(key);
  if (cached) {
    log.debug(`搜索缓存命中: ${searchKw}`);
    return cached;
  }

  const sources = SOURCES.filter((s) => !excludeSource?.includes(s));
  const deadline = Date.now() + 3000;
  const startedAt = Date.now();
  let done = false;
  let perfect: SongInfo | null = null;
  let best: { score: number; source: string; song: SongInfo } | null = null;
  const topBySource = new Map<string, SourceBest>();

  const handle = (source: string, list: SongInfo[]): void => {
    for (const item of list) {
      const name = item.name || "";
      const resultSinger = item.singer || "";
      if (!name) continue;
      if (searchKw.trim().includes(" ") && normalizeText(name) === normalizeText(searchKw)) {
        log.debug(`跳过垃圾结果（歌名=搜索词原样回显）: ${name}/${resultSinger}`);
        continue;
      }
      const nameOk = fuzzyMatch(keyword, name) || fuzzyMatch(searchKw, name);
      const singerKwOk = fuzzyMatch(keyword, resultSinger) || artistAliasMatch(keyword, resultSinger);
      if (!nameOk && !singerKwOk) continue;
      if (singer && !(fuzzyMatch(singer, resultSinger) || artistAliasMatch(singer, resultSinger))) continue;

      const score =
        singerKwOk && !singer
          ? scoreSearchResult(name, resultSinger, keyword, keyword, queryVersion)
          : scoreSearchResult(name, resultSinger, keyword, singer, queryVersion);
      log.debug(`搜索候选: ${name} - ${resultSinger} [${source}] 评分=${score}`);

      const song = makeSong({
        name,
        singer: resultSinger,
        album: item.album,
        songId: item.songId,
        platform: source,
        interval: item.interval,
        score,
        raw: item.raw,
      });
      if (score >= 150) {
        perfect = song;
        searchCache.set(key, song);
        return;
      }
      if (!best || score > best.score) best = { score, source, song };
      const cur = topBySource.get(source);
      if (!cur || score > cur.score) topBySource.set(source, { score, song });
    }
  };

  const tasks = sources.map(async (source) => {
    try {
      const list = await lx.search(source, searchKw, 5);
      if (!done) handle(source, list);
    } catch (e) {
      log.debug(`搜索 ${source} 失败: ${(e as Error).message}`);
    }
  });
  void Promise.allSettled(tasks);

  while (Date.now() < deadline) {
    if (perfect) break;
    const chosen = selectBestWithConsensus(topBySource, best);
    if (chosen && Date.now() - startedAt >= 700 && chosen.score >= EARLY_RETURN_SCORE) break;
    await sleep(50);
  }
  done = true;

  if (perfect) {
    const song = perfect as SongInfo;
    log.info(`搜索成功: ${song.name} - ${song.singer} [${PLATFORM_NAMES[song.platform] || song.platform}] ID=${song.songId} (完美匹配)`);
    searchCache.set(key, song);
    return song;
  }

  const chosen = selectBestWithConsensus(topBySource, best);
  if (!chosen) {
    log.info(`搜索失败: ${searchKw}`);
    return null;
  }
  if (chosen.score < MIN_ACCEPT_SCORE) {
    log.info(`搜索失败(置信度不足 ${chosen.score}<${MIN_ACCEPT_SCORE}): ${searchKw}`);
    return null;
  }

  const chosenKey = consensusKey(chosen.song.name, chosen.song.singer);
  const alternates: SongInfo[] = [];
  for (const [altSource, entry] of topBySource) {
    if (altSource === chosen.source) continue;
    if (consensusKey(entry.song.name, entry.song.singer) !== chosenKey) continue;
    alternates.push(makeSong({ ...entry.song, score: 0, alternates: [] }));
  }

  const song = makeSong({ ...chosen.song, score: chosen.score, alternates });
  log.info(
    `搜索成功: ${song.name} - ${song.singer} [${PLATFORM_NAMES[chosen.source] || chosen.source}] ID=${song.songId} ` +
      `(评分=${chosen.score}, 共识候选=${topBySource.size}源, 跨源回退候选=${alternates.length})`,
  );
  searchCache.set(key, song);
  return song;
}

export async function suggestSong(keyword: string, singer = ""): Promise<SongInfo | null> {
  if (!(await lx.probe())) return null;
  const searchKw = singer ? `${keyword} ${singer}` : keyword;
  const deadline = Date.now() + 3000;
  let best: { score: number; song: SongInfo } | null = null;
  let done = false;

  const handle = (list: SongInfo[]): void => {
    for (const item of list) {
      const name = item.name || "";
      const rs = item.singer || "";
      if (!name) continue;
      if (searchKw.includes(" ") && normalizeText(name) === normalizeText(searchKw)) continue;
      let score: number;
      if (singer) {
        score = scoreSearchResult(name, rs, keyword, singer);
      } else {
        const toks = keyword.split(/\s+/).filter(Boolean);
        const cands = [scoreSearchResult(name, rs, keyword, "")];
        for (const t of toks) cands.push(scoreSearchResult(name, rs, t, ""));
        for (let i = 0; i < toks.length; i += 1) {
          for (let j = 0; j < toks.length; j += 1) {
            if (i !== j && toks[i] && toks[j]) cands.push(scoreSearchResult(name, rs, toks[i], toks[j]));
          }
        }
        score = Math.max(...cands);
      }
      if (!best || score > best.score) best = { score, song: makeSong({ ...item, score, alternates: [] }) };
    }
  };

  void Promise.allSettled(
    SOURCES.map(async (source) => {
      try {
        const list = await lx.search(source, searchKw, 3);
        if (!done) handle(list);
      } catch {
        /* ignore */
      }
    }),
  );
  while (Date.now() < deadline && best === null) await sleep(50);
  done = true;
  const chosen = best as { score: number; song: SongInfo } | null;
  if (chosen && chosen.score >= SUGGEST_MIN_SCORE) return chosen.song;
  return null;
}

// ---------------------------------------------------------------- 查询拆分

const QUERY_DELIMS = [
  " - ", " — ", " – ", " _ ", " / ", " | ", " by ", " By ", " BY ",
  "-", "—", "–", "/", "|", ",", "，", "、", ":", "：",
];

const segmenter = new Intl.Segmenter("zh", { granularity: "word" });

function segmentTokens(text: string): string[] {
  try {
    const out: string[] = [];
    for (const seg of segmenter.segment(text)) {
      if (seg.isWordLike && seg.segment.trim()) out.push(seg.segment.trim());
    }
    return out.length ? out : [text];
  } catch {
    return [text];
  }
}

function queryHasVersion(q: string): boolean {
  const toks = q.split(/\s+/).filter(Boolean);
  toks.push(...segmentTokens(q));
  const lower = new Set(VERSION_NOISE.map((v) => v.toLowerCase()));
  return toks.some((t) => lower.has(t.toLowerCase()));
}

/** 把自由格式 /play 查询拆成若干 (歌名, 歌手) 候选（含两种顺序）。 */
export function splitPlayQuery(query: string): Array<[string, string]> {
  const q = (query || "").trim();
  if (!q) return [];
  if (queryHasVersion(q)) return [[q, ""]];
  for (const d of QUERY_DELIMS) {
    const idx = q.indexOf(d);
    if (idx >= 0) {
      const a = q.slice(0, idx).trim();
      const b = q.slice(idx + d.length).trim();
      if (a && b) return [[a, b], [b, a]];
    }
  }
  const candidates: Array<[string, string]> = [[q, ""]];
  if (CJK_RE.test(q)) {
    const toks = segmentTokens(q);
    if (toks.length >= 2) {
      const longest = toks.reduce((a, b) => (b.length > a.length ? b : a), toks[0]);
      if (longest !== q) {
        const rest = toks.filter((t) => t !== longest).join("") || q;
        candidates.push([rest, longest]);
        candidates.push([longest, rest]);
      }
    }
  }
  const expanded = [...candidates];
  for (const [songName, singer] of candidates) {
    if (!singer) continue;
    const canon = canonicalDisplay(singer);
    if (canon && canon !== singer) expanded.push([songName, canon]);
  }
  return expanded;
}
