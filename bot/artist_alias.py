"""多语言艺名别名对照（通用领域数据，非音源定制、非查询特例）。

用途：用户可能用中文名、英文名、中英混写（如"中文音译+英文姓氏"）、昵称来点歌，
而各音源返回的歌手字段写法不一。本模块让这些等价写法互相识别。

匹配原则（保守，宁缺毋滥）：
- 组内互为别名（首个条目为规范写法）。
- 完整写法精确命中组内条目 -> 直接等价。
- 否则分词（CJK 连续段 / 拉丁连续段）：查询的**所有**词元都能落到同一别名组，
  且候选至少一个词元落入该组 -> 等价。
- 词元落组：精确等于组内条目，或受控包含（CJK 词元 len>=2、拉丁词元 len>=4
  为组内条目的子串）。无关歌手（周杰伦 vs 林俊杰）不会因共享单字而误配。
"""

import re
from typing import Optional, Set

# 别名组：每组内写法互为等价（首个条目为规范写法）
_GROUPS = [
    # 欧美歌手：中文名 / 英文名 / 昵称
    ("Justin Bieber", "贾斯汀比伯", "贾斯汀·比伯", "jb", "比伯"),
    ("Michael Jackson", "迈克尔杰克逊", "迈克尔·杰克逊", "mj", "杰克逊"),
    ("Taylor Swift", "泰勒斯威夫特", "泰勒·斯威夫特", "霉霉"),
    ("Adele", "阿黛尔"),
    ("Beyoncé", "beyonce", "碧昂丝", "碧昂丝·吉赛尔·诺斯"),
    ("Rihanna", "蕾哈娜", "日日"),
    ("Bruno Mars", "布鲁诺马尔斯", "布鲁诺·马尔斯", "火星哥"),
    ("Ed Sheeran", "ed sheeran", "艾德希兰", "黄老板"),
    ("Eminem", "埃米纳姆", "姆爷", "阿姆"),
    ("Lady Gaga", "lady gaga", "嘎嘎小姐", "嘎嘎"),
    ("Katy Perry", "katy perry", "凯蒂佩里", "水果姐"),
    ("Ariana Grande", "ariana grande", "爱莉安娜格兰德", "a妹"),
    ("Billie Eilish", "billie eilish", "比莉艾利什", "碧梨"),
    ("Carly Rae Jepsen", "carly rae jepsen", "蹲妹", "卡莉蕾·吉普森", "卡莉蕾吉普森"),
    ("Charlie Puth", "charlie puth", "查理普斯", "断眉"),
    ("Justin Timberlake", "贾斯汀汀布莱克", "贾斯汀·汀布莱克"),
    ("Maroon 5", "maroon 5", "魔力红"),
    ("Coldplay", "酷玩乐队", "酷玩"),
    ("Linkin Park", "林肯公园"),
    ("Westlife", "西城男孩"),
    ("Backstreet Boys", "backstreet boys", "后街男孩"),
    ("One Direction", "one direction", "单向组合"),
    ("Avril Lavigne", "艾薇儿", "艾薇儿·拉维尼"),
    ("Elvis Presley", "猫王", "埃尔维斯·普雷斯利"),
    ("Bob Dylan", "鲍勃迪伦", "鲍勃·迪伦"),
    ("Queen", "皇后乐队"),
    # 华语歌手：中文名 / 英文艺名
    ("周杰伦", "jay chou", "jaychou"),
    ("林俊杰", "jj lin", "jjlin"),
    ("陈奕迅", "eason chan"),
    ("邓紫棋", "g.e.m.", "gem", "gem tang"),
    ("王力宏", "leehom wang", "wang leehom"),
    ("张学友", "jacky cheung"),
    ("刘德华", "andy lau"),
    ("王菲", "faye wong"),
    ("五月天", "mayday"),
    ("薛之谦", "joker xue"),
    # 日韩歌手
    ("米津玄師", "米津玄师", "kenshi yonezu"),
    ("松原みき", "miki matsubara"),
    ("防弹少年团", "bts", "bangtan boys"),
    ("blackpink", "블랙핑크"),
    ("IU", "李知恩"),
    ("权志龙", "g-dragon", "gd"),
    ("少女时代", "girls generation", "snsd", "소녀시대"),
]

# 标准化：小写、去空白与常见名字分隔符（·・•.'-），保留字母数字与中日韩
_NORM_RE = re.compile(r"[\s·・•.'’\-]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LAT_RE = re.compile(r"[a-z0-9]+")


def _norm(s: str) -> str:
    return _NORM_RE.sub("", (s or "").lower())


def _tokens(normed: str):
    """把标准化后的名字切成词元：连续 CJK 段 / 连续拉丁段（中英混写自然拆开）。"""
    toks, cur, cur_cjk = [], "", False
    for ch in normed:
        is_cjk = bool(_CJK_RE.match(ch))
        is_lat = bool(_LAT_RE.match(ch))
        if not (is_cjk or is_lat):
            if cur:
                toks.append(cur)
            cur, cur_cjk = "", False
            continue
        if cur and (is_cjk != cur_cjk):
            toks.append(cur)
            cur = ch
        else:
            cur += ch
        cur_cjk = is_cjk
    if cur:
        toks.append(cur)
    return toks


# 组索引：标准化条目 -> 组 id 集合；每组规范写法 = 首条目
_ENTRY2GID = {}
for _gid, _g in enumerate(_GROUPS):
    for _e in _g:
        _ENTRY2GID.setdefault(_norm(_e), set()).add(_gid)
_CANON = {i: _g[0] for i, _g in enumerate(_GROUPS)}


def _tok_groups(tok: str) -> Set[int]:
    """词元落组：精确等于条目，或受控包含（CJK>=2 / 拉丁>=4 为条目子串）。"""
    res = set()
    is_cjk = bool(_CJK_RE.fullmatch(tok))
    is_lat = bool(_LAT_RE.fullmatch(tok))
    for entry, gids in _ENTRY2GID.items():
        if tok == entry:
            res |= gids
        elif is_cjk and len(tok) >= 2 and tok in entry:
            res |= gids
        elif is_lat and len(tok) >= 4 and tok in entry:
            res |= gids
    return res


def artist_alias_match(a: str, b: str) -> bool:
    """判断两个歌手写法是否为同一艺人的等价写法。"""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    # 1) 完整写法均精确命中组内条目
    ga, gb = _ENTRY2GID.get(na), _ENTRY2GID.get(nb)
    if ga and gb and (ga & gb):
        return True
    # 2) 词元级：a 的所有词元均落入同一组，且 b 至少一个词元落入该组
    ta, tb = _tokens(na), _tokens(nb)
    if not ta or not tb:
        return False
    ga_toks = [_tok_groups(t) for t in ta]
    gb_toks = [_tok_groups(t) for t in tb]
    all_groups = set().union(*ga_toks) if ga_toks else set()
    for g in all_groups:
        if all(g in s for s in ga_toks) and any(g in s for s in gb_toks):
            return True
    return False


def canonical_singer(name: str) -> Optional[str]:
    """歌手写法的规范化形式：命中别名组返回组规范写法（标准化），否则 None。

    用于跨平台共识：同一歌手被不同音源以中/英文写法返回时，归一后才能形成共识。
    """
    gids = _ENTRY2GID.get(_norm(name))
    if gids:
        return _norm(_CANON[min(gids)])
    return None


def canonical_display(name: str) -> Optional[str]:
    """命中别名组时返回组的规范显示写法（首个条目原样），否则 None。

    用于混写歌手取词容错与批量点歌：把 "迈克尔jackson"/"水果姐" 归一到
    平台最容易理解的写法（"Michael Jackson"/"Katy Perry"）作为追加候选。
    整串精确命中之外，还支持混写：所有词元落入同一组即视为该组（词元级，
    与 artist_alias_match 同一套保守规则）。
    """
    na = _norm(name)
    if not na:
        return None
    gids = _ENTRY2GID.get(na)
    if gids:
        return _GROUPS[min(gids)][0]
    toks = _tokens(na)
    if not toks:
        return None
    tgs = [_tok_groups(t) for t in toks]
    if any(not s for s in tgs):
        return None  # 有词元无法落组 -> 不强行归一（宁缺毋滥）
    for g in tgs[0]:
        if all(g in s for s in tgs[1:]):
            return _GROUPS[g][0]
    return None
