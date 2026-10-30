"""匹配评分模块（与 search 同接口）。

注意：自"重排/缺字/大小写/空格/多语言容错"改造后，search.py 的默认 _fuzzy_match
已经是基于 rapidfuzz 的多策略实现（ratio + partial_ratio + CJK 字级 token_sort）。
本模块不再提供第二套评分器，而是直接复用 search 的健壮实现，保留 enable/disable
钩子以维持历史调用方式的兼容性（enable 为幂等的安全切换）。

设计约束（来自用户需求）：
- 歌手/歌名顺序可颠倒、分隔符随意甚至无分隔都能搜到；
- 中文支持缺字（告白球/告白气）与乱序重排（气球告白/气告白球）；
- 英文大小写、空格不敏感且可无空格（IRELLYLIKEYOU / IreallyLikeYOU / I reallylike）；
- 其他语言同理（归一化保留 Unicode 词字符，多策略评分语言无关）。
"""
from . import search

# 直接复用 search 的健壮匹配器（避免两套实现漂移）
_fuzzy_match = search._fuzzy_match
_score_search_result = search._score_search_result


def enable():
    """切换主路径评分器为健壮实现（幂等；默认即此实现）。"""
    search.FUZZY_MATCH = _fuzzy_match
    search.SCORE_RESULT = _score_search_result


def disable():
    """保持兼容：恢复为同一健壮实现（历史回退钩子，行为不变）。"""
    search.FUZZY_MATCH = search._fuzzy_match
    search.SCORE_RESULT = search._score_search_result
