/** 批量点歌拆分层（零词典，只生成结构候选，是否成立交给搜索验证）。 */
import { config } from "./config.js";

export const BATCH_DELIMS = ["；", ";"];
const MIN_BATCH_SEGS = 3;

export function maxBatchSongs(): number {
  return Math.max(1, config.num("max_batch_songs", 10));
}

export function capBatchQueries(queries: string[]): [string[], number] {
  const max = maxBatchSongs();
  if (queries.length <= max) return [queries, 0];
  return [queries.slice(0, max), queries.length - max];
}

/** [(子查询组, 是否免验证)]；空数组表示结构上不是批量。 */
export function batchPlans(query: string): Array<[string[], boolean]> {
  const q = (query || "").trim();
  if (!q) return [];
  for (const d of BATCH_DELIMS) {
    if (q.includes(d)) {
      const parts = q.split(d).map((p) => p.trim()).filter(Boolean);
      if (parts.length >= 2) return [[parts, true]];
    }
  }
  const segs = q.split(/\s+/).filter(Boolean);
  const plans: Array<[string[], boolean]> = [];
  if (segs.length >= MIN_BATCH_SEGS) {
    plans.push([segs.slice(1).map((s) => `${segs[0]} ${s}`), false]);
    if (segs.length >= 4 && segs.length % 2 === 0) {
      const pairs: string[] = [];
      for (let i = 0; i < segs.length; i += 2) pairs.push(`${segs[i]} ${segs[i + 1]}`);
      plans.push([pairs, false]);
    }
  }
  return plans;
}
