/** 通用工具：文本标准化、相似度、时长解析、弹幕截断、格式化。 */
export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export function normalizeText(text: string): string {
  return (text || "")
    .replace(/[（(].*?[)）]/g, "")
    .replace(/[^\p{L}\p{N}_\u3040-\u30ff\uac00-\ud7af]/gu, "")
    .toLowerCase();
}

/** 字多重集 Jaccard：中文乱序容错，且不会因共享单字误配。 */
export function jaccardSimilarity(a: string, b: string): number {
  if (!a || !b) return 0;
  const ca = new Map<string, number>();
  const cb = new Map<string, number>();
  for (const ch of a) ca.set(ch, (ca.get(ch) || 0) + 1);
  for (const ch of b) cb.set(ch, (cb.get(ch) || 0) + 1);
  let inter = 0;
  let union = 0;
  for (const key of new Set([...ca.keys(), ...cb.keys()])) {
    const x = ca.get(key) || 0;
    const y = cb.get(key) || 0;
    inter += Math.min(x, y);
    union += Math.max(x, y);
  }
  return union ? inter / union : 0;
}

/** a 是否为 b 的子序列（保持顺序、容忍缺字）。 */
export function isSubsequence(a: string, b: string): boolean {
  let i = 0;
  for (const ch of b) {
    if (i < a.length && ch === a[i]) i += 1;
  }
  return i === a.length;
}

export function parseDuration(value: unknown): number {
  if (value === null || value === undefined || value === "") return 0;
  if (typeof value === "number") return Math.max(0, value);
  const text = String(value).trim();
  try {
    if (text.includes(":")) {
      let total = 0;
      for (const part of text.split(":")) total = total * 60 + Number(part);
      return Math.max(0, total);
    }
    const n = Number(text);
    return Number.isFinite(n) ? Math.max(0, n) : 0;
  } catch {
    return 0;
  }
}

/** 弹幕按 B 站规则截断：中文/全角算 2 单位，ASCII 算 1。 */
export function truncateDanmaku(message: string, maxUnits: number): string {
  let out = "";
  let total = 0;
  for (const ch of message) {
    const weight = ch.codePointAt(0)! > 127 ? 2 : 1;
    if (total + weight > maxUnits) break;
    out += ch;
    total += weight;
  }
  return out;
}

/** 歌名过长时截断（只用于弹幕展示）。 */
export function formatSongName(name: string): string {
  const n = name || "未知";
  return n.length > 10 ? `${n.slice(0, 5)}...` : n;
}

export function maskSecret(value: string): string {
  if (!value) return "";
  return value.length > 12 ? `${"•".repeat(12)}${value.slice(-4)}` : "•".repeat(value.length);
}

/** 全局 API 限速器（B 站请求）。 */
export class RateLimiter {
  private last = 0;
  private chain: Promise<void> = Promise.resolve();

  constructor(private minInterval: number) {}

  setInterval(seconds: number): void {
    this.minInterval = seconds;
  }

  async acquire(): Promise<void> {
    const run = async () => {
      const now = Date.now();
      const elapsed = (now - this.last) / 1000;
      if (elapsed < this.minInterval) {
        await sleep((this.minInterval - elapsed) * 1000);
      }
      this.last = Date.now();
    };
    this.chain = this.chain.then(run, run);
    return this.chain;
  }
}
