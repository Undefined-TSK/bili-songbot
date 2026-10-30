"""批量点歌拆分层（零词典，普适；不改动任何单曲点歌代码）。

设计铁律（用户要求，2026-10-29 整改）：
1. 批量 = 把一条 /play 查询拆成多个独立单曲查询，逐个走现有单曲逻辑
  （core._handle_play / search_song），单曲全部特性自动继承。
2. **拆分绝不依赖歌手别名数据库**：世界上歌手太多，词典永远不全。
   这里只生成「纯结构候选」（不判断谁是歌手），是否真的是批量交给
   搜索验证（core._verify_batch_groups：每组高分命中 + 歌手与组内段互认
   + 组间不同歌），全部通过才成立，否则回退单曲。
3. 别名数据库（bot/artist_alias）只做本职工作：把用户输入的歌手别名
   互认成真实歌手（在 search/core 内），本模块零依赖。

结构候选（宁缺毋滥）：
- 分号（；/;）显式分隔：每段是独立单曲查询——用户显式表达，免验证（可信）。
- 每段一组（>=3 段）：对应「歌手 歌1 歌2 ...」写法，组作为独立单曲查询。
- 两两一组（>=4 偶数段）：对应「歌 歌手 歌 歌手」写法，组内顺序由单曲解析处理。

上限：MAX_BATCH_SONGS（config.json: max_batch_songs 可覆盖），超限截断并提示。
"""

from typing import List, Tuple


def _from_config() -> int:
    """从 config.json 读取 max_batch_songs；缺省 10。"""
    try:
        from .config import CFG
        return max(1, int(CFG.get("max_batch_songs", 10)))
    except Exception:  # pragma: no cover - 配置缺失时兜底
        return 10


# 批量点歌数量上限（config.json: "max_batch_songs" 可覆盖）
MAX_BATCH_SONGS = _from_config()

# 显式批量分隔符（分号未用于单曲拆分，无冲突；单曲的分隔符 -/|,by 等保持原义）
BATCH_DELIMS = ["；", ";"]

# 结构候选所需最少分段数（含歌手段）
MIN_BATCH_SEGS = 3

# 免验证的可信计划（分号）标记
TRUSTED = True
NEED_VERIFY = False


def cap_batch_queries(queries: List[str]) -> Tuple[List[str], int]:
    """按 MAX_BATCH_SONGS 截断，返回 (保留列表, 截断数)。"""
    if len(queries) <= MAX_BATCH_SONGS:
        return queries, 0
    return queries[:MAX_BATCH_SONGS], len(queries) - MAX_BATCH_SONGS


def batch_plans(query: str) -> List[Tuple[List[str], bool]]:
    """生成批量计划候选：[(子查询组, 是否免验证), ...]，按优先级排列。

    返回空列表 = 结构上不可能是批量，直接走单曲（零开销）。
    计划是否成立由调用方验证（分号可信免验证；结构候选必须搜索验证）。
    """
    q = (query or "").strip()
    if not q:
        return []
    # 1) 显式分号：用户显式表达的批量，每段独立走完整单曲解析（免验证）
    for d in BATCH_DELIMS:
        if d in q:
            parts = [p.strip() for p in q.split(d) if p.strip()]
            if len(parts) >= 2:
                return [(parts, TRUSTED)]
    # 2) 纯结构候选（零词典：不判断任何段是不是歌手，只做结构假设，验证交给搜索结果）
    segs = q.split()
    plans: List[Tuple[List[str], bool]] = []
    if len(segs) >= MIN_BATCH_SEGS:
        # 2a) 首段为歌手假设：「歌手 歌1 歌2 ...」——歌手段拼进每组子查询，
        #     每组作为独立单曲查询交给现有单曲解析（顺序可颠倒由单曲层处理）
        plans.append(([f"{segs[0]} {s}" for s in segs[1:]], NEED_VERIFY))
        # 2b) 两两一组（偶数段）：「歌 歌手 歌 歌手」——组内顺序交给单曲解析
        if len(segs) >= 4 and len(segs) % 2 == 0:
            plans.append(([" ".join(pair) for pair in zip(segs[0::2], segs[1::2])],
                          NEED_VERIFY))
    return plans
