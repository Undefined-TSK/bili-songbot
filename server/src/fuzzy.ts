/** 拼音 / 谐音 / 错别字鲁棒匹配（pinyin-pro 替代 pypinyin）。 */
import { pinyin } from "pinyin-pro";
import { ratio } from "fuzzball";

const CJK_RE = /[\u3400-\u9fff\uf900-\ufaff]/;

export function toPinyin(text: string): string {
  if (!text) return "";
  try {
    const arr = pinyin(text, { toneType: "none", type: "array" }) as string[];
    return arr.join("").toLowerCase();
  } catch {
    return "";
  }
}

/** CJK 段 → 无调拼音；拉丁/数字段 → 原样小写；标点空白作分隔。 */
export function pinyinKey(text: string): string {
  if (!text) return "";
  const out: string[] = [];
  let cjk: string[] = [];
  let lat: string[] = [];
  const flushCjk = (): void => {
    if (cjk.length) {
      out.push(toPinyin(cjk.join("")));
      cjk = [];
    }
  };
  const flushLat = (): void => {
    if (lat.length) {
      out.push(lat.join("").toLowerCase());
      lat = [];
    }
  };
  for (const ch of text) {
    if (CJK_RE.test(ch)) {
      flushLat();
      cjk.push(ch);
    } else if (/[a-zA-Z0-9]/.test(ch)) {
      flushCjk();
      lat.push(ch);
    } else {
      flushCjk();
      flushLat();
    }
  }
  flushCjk();
  flushLat();
  return out.join("");
}

export function pinyinMatch(keyword: string, target: string): boolean {
  if (!CJK_RE.test(`${keyword}${target}`)) return false;
  const pk = pinyinKey(keyword);
  const pt = pinyinKey(target);
  if (!pk || !pt) return false;
  if (pk === pt) return true;
  if (pk.length >= 4 && pt.length >= 4 && Math.abs(pk.length - pt.length) <= 3) {
    return ratio(pk, pt) >= 90;
  }
  return false;
}
