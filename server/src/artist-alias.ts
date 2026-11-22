/** 多语言艺名别名互认（通用领域数据）。 */
const GROUPS: string[][] = [
  ["Justin Bieber", "贾斯汀比伯", "贾斯汀·比伯", "jb", "比伯"],
  ["Michael Jackson", "迈克尔杰克逊", "迈克尔·杰克逊", "mj", "杰克逊"],
  ["Taylor Swift", "泰勒斯威夫特", "泰勒·斯威夫特", "霉霉"],
  ["Adele", "阿黛尔"],
  ["Beyoncé", "beyonce", "碧昂丝", "碧昂丝·吉赛尔·诺斯"],
  ["Rihanna", "蕾哈娜", "日日"],
  ["Bruno Mars", "布鲁诺马尔斯", "布鲁诺·马尔斯", "火星哥"],
  ["Ed Sheeran", "ed sheeran", "艾德希兰", "黄老板"],
  ["Eminem", "埃米纳姆", "姆爷", "阿姆"],
  ["Lady Gaga", "lady gaga", "嘎嘎小姐", "嘎嘎"],
  ["Katy Perry", "katy perry", "凯蒂佩里", "水果姐"],
  ["Ariana Grande", "ariana grande", "爱莉安娜格兰德", "a妹"],
  ["Billie Eilish", "billie eilish", "比莉艾利什", "碧梨"],
  ["Carly Rae Jepsen", "carly rae jepsen", "蹲妹", "卡莉蕾·吉普森", "卡莉蕾吉普森"],
  ["Charlie Puth", "charlie puth", "查理普斯", "断眉"],
  ["Justin Timberlake", "贾斯汀汀布莱克", "贾斯汀·汀布莱克"],
  ["Maroon 5", "maroon 5", "魔力红"],
  ["Coldplay", "酷玩乐队", "酷玩"],
  ["Linkin Park", "林肯公园"],
  ["Westlife", "西城男孩"],
  ["Backstreet Boys", "backstreet boys", "后街男孩"],
  ["One Direction", "one direction", "单向组合"],
  ["Avril Lavigne", "艾薇儿", "艾薇儿·拉维尼"],
  ["Elvis Presley", "猫王", "埃尔维斯·普雷斯利"],
  ["Bob Dylan", "鲍勃迪伦", "鲍勃·迪伦"],
  ["Queen", "皇后乐队"],
  ["周杰伦", "jay chou", "jaychou"],
  ["林俊杰", "jj lin", "jjlin"],
  ["陈奕迅", "eason chan"],
  ["邓紫棋", "g.e.m.", "gem", "gem tang"],
  ["王力宏", "leehom wang", "wang leehom"],
  ["张学友", "jacky cheung"],
  ["刘德华", "andy lau"],
  ["王菲", "faye wong"],
  ["五月天", "mayday"],
  ["薛之谦", "joker xue"],
  ["米津玄師", "米津玄师", "kenshi yonezu"],
  ["松原みき", "miki matsubara"],
  ["防弹少年团", "bts", "bangtan boys"],
  ["blackpink", "블랙핑크"],
  ["IU", "李知恩"],
  ["权志龙", "g-dragon", "gd"],
  ["少女时代", "girls generation", "snsd", "소녀시대"],
];

const CJK_RE = /[\u4e00-\u9fff]+/;
const LAT_RE = /[a-z0-9]+/;

function norm(s: string): string {
  return (s || "").toLowerCase().replace(/[\s·・•.'’\-]+/g, "");
}

function tokens(normed: string): string[] {
  const out: string[] = [];
  let cur = "";
  let curCjk = false;
  for (const ch of normed) {
    const isCjk = CJK_RE.test(ch);
    const isLat = LAT_RE.test(ch);
    if (!isCjk && !isLat) {
      if (cur) out.push(cur);
      cur = "";
      curCjk = false;
      continue;
    }
    if (cur && isCjk !== curCjk) {
      out.push(cur);
      cur = ch;
    } else {
      cur += ch;
    }
    curCjk = isCjk;
  }
  if (cur) out.push(cur);
  return out;
}

const ENTRY2GID = new Map<string, Set<number>>();
GROUPS.forEach((group, gid) => {
  for (const entry of group) {
    const key = norm(entry);
    if (!ENTRY2GID.has(key)) ENTRY2GID.set(key, new Set());
    ENTRY2GID.get(key)!.add(gid);
  }
});

function tokenGroups(tok: string): Set<number> {
  const res = new Set<number>();
  const isCjk = /^[\u4e00-\u9fff]+$/.test(tok);
  const isLat = /^[a-z0-9]+$/.test(tok);
  for (const [entry, gids] of ENTRY2GID) {
    if (tok === entry) {
      for (const g of gids) res.add(g);
    } else if (isCjk && tok.length >= 2 && entry.includes(tok)) {
      for (const g of gids) res.add(g);
    } else if (isLat && tok.length >= 4 && entry.includes(tok)) {
      for (const g of gids) res.add(g);
    }
  }
  return res;
}

export function artistAliasMatch(a: string, b: string): boolean {
  const na = norm(a);
  const nb = norm(b);
  if (!na || !nb) return false;
  const ga = ENTRY2GID.get(na);
  const gb = ENTRY2GID.get(nb);
  if (ga && gb) {
    for (const g of ga) if (gb.has(g)) return true;
  }
  const ta = tokens(na);
  const tb = tokens(nb);
  if (!ta.length || !tb.length) return false;
  const gaToks = ta.map(tokenGroups);
  const gbToks = tb.map(tokenGroups);
  const all = new Set<number>();
  for (const s of gaToks) for (const g of s) all.add(g);
  for (const g of all) {
    if (gaToks.every((s) => s.has(g)) && gbToks.some((s) => s.has(g))) return true;
  }
  return false;
}

/** 命中别名组返回组规范写法（标准化），否则 null。 */
export function canonicalSinger(name: string): string | null {
  const gids = ENTRY2GID.get(norm(name));
  if (!gids || !gids.size) return null;
  const gid = Math.min(...gids);
  return norm(GROUPS[gid][0]);
}

/** 命中别名组返回规范显示写法，否则 null。 */
export function canonicalDisplay(name: string): string | null {
  const na = norm(name);
  if (!na) return null;
  const gids = ENTRY2GID.get(na);
  if (gids && gids.size) return GROUPS[Math.min(...gids)][0];
  const toks = tokens(na);
  if (!toks.length) return null;
  const tgs = toks.map(tokenGroups);
  if (tgs.some((s) => s.size === 0)) return null;
  for (const g of tgs[0]) {
    if (tgs.slice(1).every((s) => s.has(g))) return GROUPS[g][0];
  }
  return null;
}
