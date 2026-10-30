#!/usr/bin/env python3
"""rapidfuzz 匹配器测试：健壮多策略模糊匹配（重排/缺字/大小写/空格/多语言容错）。

判定标准：
1. 分档不回退：test_matcher.py 固化的 12 例分档分数完全一致（评分结构未变）。
2. 自由容错：中文缺字/乱序重排、英文大小写/空格/无空格、其他语言都应匹配；
   完全无关内容必须拒绝（避免误匹配）。
3. 择优稳定性：同一候选列表下 best 选择一致；存在干净匹配时干净匹配胜过乱序候选。

用法: python3 tests/test_search_rapidfuzz.py
前置: rapidfuzz（项目依赖，见 requirements.txt）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import rapidfuzz  # noqa: F401
    from bot import search
    from bot.search import _fuzzy_match as fuzzy_old
    from bot.search import _score_search_result as score_old
    from bot.search_rapidfuzz import _fuzzy_match as fuzzy_new
    from bot.search_rapidfuzz import _score_search_result as score_new
    RF_AVAILABLE = True
except Exception:
    RF_AVAILABLE = False


# 分档固化用例（与 tests/test_matcher.py 一致 + 常见真实场景），(name, result_singer, keyword, singer)
TIER_CASES = [
    ('告白气球', '周杰伦', '告白气球', '周杰伦'),   # 150 完全+完全
    ('告白气球', '', '告白气球', ''),               # 100 完全
    ('告白气球XYZ', '', '告白气球', ''),            # 80 开头
    ('新版告白气球', '', '告白气球', ''),           # 20 包含+后缀惩罚
    ('告白气球 (Live)', '周杰伦', '告白气球', '周杰伦'),  # 120
    ('告白气球 伴奏', '周杰伦', '告白气球', '周杰伦'),    # 100
    ('告白气球', '费玉清', '告白气球', '周杰伦'),   # 90 歌手不匹配
    ('告白气球', '周杰伦/费玉清', '告白气球', '周杰伦'),  # 130 歌手包含
    ('告白求球', '', '告白气球', ''),               # 30 模糊（真错别字，两版都应保留）
    ('毫无关系', '', '告白气球', ''),               # 10 低匹配
    ('稻香', '', '稻香', ''),                       # 100
    ('晴天', '周杰伦', '晴天', '周杰伦'),           # 150
]

# 自由容错用例：(keyword, target, 期望匹配, 原因)
DIFF_CASES = [
    ('告白气球', '球气白告', True, '乱序重排：用户需求要求顺序颠倒也能匹配'),
    ('晴天', '天晴', True, '完全转置：用户需求要求顺序颠倒也能匹配'),
    ('气球告白', '告白气球', True, '中文字级重排（token_sort）容错'),
    ('告白球', '告白气球', True, '中文缺字（少一个字）仍匹配'),
    ('周杰', '周杰伦', True, '歌手缩写仍匹配'),
    ('I really like you', 'I Really Like You', True, '英文大小写不敏感'),
    ('IRELLYLIKEYOU', 'I really like you', True, '英文缺字母 a + 无空格仍匹配'),
    ('IreallyLikeYOU', 'I really like you', True, '英文大小写混排 + 无空格仍匹配'),
    ('告白气球', '告白求球', True, '真错别字（单字替换）应保留匹配'),
    ('稻香', '夜曲', False, '完全无关：一致拒绝'),
    ('周杰伦', '林俊杰', False, '不同歌手：一致拒绝'),
    ('周杰伦', '周杰倫', True, '简繁差异：ratio 66.7，应匹配'),
]


@unittest.skipUnless(RF_AVAILABLE, "rapidfuzz 不可用")
class TestTierNoRegression(unittest.TestCase):
    """标准 1：分档固化用例在 rapidfuzz 版下分数完全一致。"""

    def test_tier_scores_identical(self):
        mismatches = []
        for name, rs, kw, singer in TIER_CASES:
            old, new = score_old(name, rs, kw, singer), score_new(name, rs, kw, singer)
            if old != new:
                mismatches.append((name, rs, kw, singer, old, new))
        self.assertEqual(mismatches, [], f"分档回退（应同分）：{mismatches}")


@unittest.skipUnless(RF_AVAILABLE, "rapidfuzz 不可用")
class TestDiffDirection(unittest.TestCase):
    """标准 2：自由容错用例的预期断言；并输出匹配清单作为证据。"""

    def test_free_input_cases(self):
        rows, violations = [], []
        for kw, tgt, expect, reason in DIFF_CASES:
            got = fuzzy_new(kw, tgt)
            rows.append((kw, tgt, got, expect, reason))
            if got != expect:
                violations.append((kw, tgt, got, expect))
        print("\n--- 自由容错清单（_fuzzy_match）---")
        for kw, tgt, got, expect, reason in rows:
            mark = "OK" if got == expect else "FAIL"
            print(f"  [{mark}] {kw!r} vs {tgt!r}: got={got} expect={expect}  ({reason})")
        self.assertEqual(violations, [], f"自由容错用例未达预期：{violations}")


@unittest.skipUnless(RF_AVAILABLE, "rapidfuzz 不可用")
class TestBestSelection(unittest.TestCase):
    """标准 3：择优稳定性。"""

    def test_best_identical_with_ordered_candidates(self):
        """候选含乱序噪音：两版 best 一致；rapidfuzz 版候选更干净（乱序噪音被过滤）。"""
        # 场景：关键词"告白气球"，三个候选——带 Live 后缀的正主、错别字版、乱序噪音
        candidates = [
            {'name': '告白气球 (Live)', 'singer': '周杰伦'},
            {'name': '告白求球', 'singer': '周杰伦'},
            {'name': '球气白告', 'singer': '某翻唱'},   # 乱序噪音
        ]
        kw, singer = '告白气球', '周杰伦'

        def pick(fuzzy, score):
            best, best_score = None, -999
            for c in candidates:
                # 复刻 search_song 的过滤规则（132 行）+ 评分（141 行）
                if not fuzzy(kw, c['name']) and not fuzzy(f"{kw} {singer}", c['name']):
                    continue
                if singer and not fuzzy(singer, c['singer']):
                    continue
                s = score(c['name'], c['singer'], kw, singer)
                if s > best_score:
                    best, best_score = c, s
            return best, best_score

        (old_best, old_score), (new_best, new_score) = pick(fuzzy_old, score_old), pick(fuzzy_new, score_new)
        print(f"\n  手写版 best: {old_best and old_best['name']} (score={old_score})")
        print(f"  rapidfuzz 版 best: {new_best and new_best['name']} (score={new_score})")
        self.assertEqual(old_best['name'], '告白气球 (Live)')
        self.assertEqual(new_best['name'], '告白气球 (Live)', '两版 best 应一致（Live 正主 120 分最高）')
        self.assertEqual(old_score, new_score, 'best 分数应一致')

    def test_clean_beats_scrambled_when_both_present(self):
        """候选同时含干净正主与乱序变体：best 必须选干净正主（不乱序抢先）。"""
        candidates = [
            {'name': '告白气球 (Live)', 'singer': '周杰伦'},  # 干净正主
            {'name': '球气白告', 'singer': '某翻唱'},          # 乱序变体（现在也能模糊匹配）
        ]
        kw, singer = '告白气球', '周杰伦'

        def pick(fuzzy, score):
            best, best_score = None, -999
            for c in candidates:
                if not fuzzy(kw, c['name']) and not fuzzy(f"{kw} {singer}", c['name']):
                    continue
                if singer and not fuzzy(singer, c['singer']):
                    continue
                s = score(c['name'], c['singer'], kw, singer)
                if s > best_score:
                    best, best_score = c, s
            return best, best_score

        (old_best, old_score), (new_best, new_score) = pick(fuzzy_old, score_old), pick(fuzzy_new, score_new)
        print(f"\n  手写版 best: {old_best and old_best['name']} (score={old_score})")
        print(f"  rapidfuzz 版 best: {new_best and new_best['name']} (score={new_score})")
        self.assertEqual(new_best['name'], '告白气球 (Live)', '干净正主应胜过乱序变体')
        self.assertEqual(old_best['name'], new_best['name'], '两版 best 应一致')


@unittest.skipUnless(RF_AVAILABLE, "rapidfuzz 不可用")
class TestHook(unittest.TestCase):
    """enable/disable 钩子行为：默认零变化、enable 幂等、disable 回退。"""

    def setUp(self):
        search_rapidfuzz = __import__('bot.search_rapidfuzz', fromlist=['enable'])
        self.rf = search_rapidfuzz

    def tearDown(self):
        self.rf.disable()  # 保证用例间隔离：默认恢复手写版

    def test_default_is_legacy(self):
        self.assertIs(search.FUZZY_MATCH, search._fuzzy_match, '默认应为手写版（不接主路径）')
        self.assertIs(search.SCORE_RESULT, search._score_search_result)

    def test_enable_disable_idempotent(self):
        self.rf.enable()
        self.assertIs(search.FUZZY_MATCH, self.rf._fuzzy_match)
        self.rf.enable()  # 幂等
        self.assertIs(search.FUZZY_MATCH, self.rf._fuzzy_match)
        self.rf.disable()
        self.assertIs(search.FUZZY_MATCH, search._fuzzy_match)
        self.rf.disable()  # 幂等
        self.assertIs(search.FUZZY_MATCH, search._fuzzy_match)


if __name__ == '__main__':
    unittest.main(verbosity=2)
