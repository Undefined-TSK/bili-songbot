"""智能模糊匹配增强：拼音 / 谐音 / 错别字 / 简繁 鲁棒比对（可选依赖 pypinyin）。

设计定位（与既有架构正交）：
- 仅服务于 search 评分层的「歌名 / 歌手」比对，让 "周杰轮→周杰伦"、"zhoujielun→周杰伦"、
  "說好不分手→说好不分手(繁体)" 这类**拼音相同但字面不同**的输入也能命中——把"怎么输都能搜到"
  从"字面容错"推进到"语音容错"。
- **绝不触碰**：艺名别名数据库（artist_alias 本职：别名↔真实歌手互认）、批量零词典拆分
  （batch.py）、单曲点歌逻辑（core._handle_play）。本模块只给 search 的模糊比对多一层信号。
- **可选依赖**：pypinyin 缺失时本层自动禁用（优雅降级为既有字面匹配），不影响部署与旧行为。
- **保守放行**：仅在「拼音键完全相等」或「拼音相似度≥90% 且长度差≤3」时命中，且仅对
  **含中日韩字符**的比对生效，避免对纯英文/无关串产生新的误配（准确率优先）。
"""
import re
from typing import Tuple

try:
    from pypinyin import lazy_pinyin
    PINYIN_AVAILABLE = True
except Exception:  # pragma: no cover - 正常环境已安装；缺失则优雅禁用拼音层
    lazy_pinyin = None
    PINYIN_AVAILABLE = False

try:
    from rapidfuzz import fuzz as _rfuzz
except Exception:  # pragma: no cover
    _rfuzz = None


# 中日韩（含扩展 A 与兼容汉字），用于识别"含 CJK"的比对
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def to_pinyin(text: str) -> str:
    """整串无调拼音（小写、去空白）。非 CJK 段会被跳过（由调用方保留）。"""
    if not PINYIN_AVAILABLE or not text:
        return ""
    try:
        return "".join(lazy_pinyin(text)).lower()
    except Exception:  # pragma: no cover - 个别罕见字符 pypinyin 可能抛错
        return ""


def pinyin_key(text: str) -> str:
    """跨「简繁 / 谐音 / 错别字 / 全拼输入」的稳定比对键。

    规则：CJK 连续段 → 无调拼音串；拉丁/数字段 → 原样小写；标点/空白作分隔。
    例：
      周杰轮 / 周杰伦 / 周傑倫(繁) / zhoujielun  →  "zhoujielun"
      告白气球(繁同音)                         →  "gaobaiqiqiu"
      迈克尔jackson                            →  "michaeljackson"
    """
    if not text:
        return ""
    out: list = []
    cur_cjk: list = []
    cur_lat: list = []

    def flush_cjk():
        if cur_cjk:
            out.append(to_pinyin("".join(cur_cjk)))
            cur_cjk.clear()

    def flush_lat():
        if cur_lat:
            out.append("".join(cur_lat).lower())
            cur_lat.clear()

    for ch in text:
        if _CJK_RE.match(ch):
            flush_lat()
            cur_cjk.append(ch)
        elif ch.isascii() and (ch.isalpha() or ch.isdigit()):
            flush_cjk()
            cur_lat.append(ch)
        else:
            flush_cjk()
            flush_lat()  # 标点 / 空白 仅作分隔，不进入比对键
    flush_cjk()
    flush_lat()
    return "".join(out)


def _pinyin_ratio(a: str, b: str) -> float:
    if _rfuzz is not None:
        return _rfuzz.ratio(a, b)
    # 退化：字符重合率
    if not a or not b:
        return 0.0
    common = sum(1 for c in a if c in b)
    return 100.0 * common / max(len(a), len(b))


def pinyin_match(keyword: str, target: str) -> bool:
    """拼音层匹配：谐音 / 错别字 / 简繁 / 全拼输入容错。

    仅对**含 CJK**的比对生效（纯英文交给 rapidfuzz，避免本层改变其既有行为）；
    只在「拼音键完全相等」或「拼音相似度≥90% 且长度差≤3」时放行——严格保守以保障准确率。
    """
    if not PINYIN_AVAILABLE:
        return False
    if not _CJK_RE.search(keyword + target):
        return False
    pk = pinyin_key(keyword)
    pt = pinyin_key(target)
    if not pk or not pt:
        return False
    if pk == pt:
        return True
    if len(pk) >= 4 and len(pt) >= 4 and abs(len(pk) - len(pt)) <= 3:
        if _pinyin_ratio(pk, pt) >= 90.0:
            return True
    return False


def pinyin_similarity(a: str, b: str) -> Tuple[float, bool]:
    """拼音相似度（0~1）与是否达到"可采信"阈值。供评分层做加权参考。"""
    if not PINYIN_AVAILABLE:
        return 0.0, False
    if not _CJK_RE.search(a + b):
        return 0.0, False
    pk, pt = pinyin_key(a), pinyin_key(b)
    if not pk or not pt:
        return 0.0, False
    if pk == pt:
        return 1.0, True
    sim = _pinyin_ratio(pk, pt) / 100.0
    return sim, (len(pk) >= 4 and len(pt) >= 4
                 and abs(len(pk) - len(pt)) <= 3 and sim >= 0.9)
