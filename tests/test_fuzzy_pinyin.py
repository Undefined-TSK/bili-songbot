#!/usr/bin/env python3
"""拼音 / 谐音 / 错别字 / 简繁 鲁棒模糊匹配单元测试。

pypinyin 为可选依赖：缺失时整层禁用，相关断言跳过（不影响 CI / 无网环境）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.fuzzy import PINYIN_AVAILABLE, pinyin_key, pinyin_match

from bot.search import _fuzzy_match, _score_search_result

skip_msg = "pypinyin 未安装，拼音层已禁用（可选依赖）"


@unittest.skipIf(not PINYIN_AVAILABLE, skip_msg)
class TestPinyinKey(unittest.TestCase):
    """pinyin_key：跨 谐音/错别字/简繁/全拼 的稳定比对键。"""

    def test_typo_same_pinyin(self):
        # 周杰轮（错别字）与 周杰伦 拼音相同
        self.assertEqual(pinyin_key('周杰轮'), pinyin_key('周杰伦'))

    def test_full_pinyin_input(self):
        # 全拼输入 zhoujielun 与汉字 周杰伦 同键
        self.assertEqual(pinyin_key('zhoujielun'), pinyin_key('周杰伦'))

    def test_traditional_same_pinyin(self):
        # 繁体 周傑倫 与简体 周杰伦 同键
        self.assertEqual(pinyin_key('周傑倫'), pinyin_key('周杰伦'))

    def test_song_pinyin(self):
        self.assertEqual(pinyin_key('告白气球'), pinyin_key('告白氣球'))

    def test_latin_kept(self):
        # 拉丁段原样保留（小写）
        self.assertEqual(pinyin_key('Michael Jackson'), 'michaeljackson')


@unittest.skipIf(not PINYIN_AVAILABLE, skip_msg)
class TestPinyinMatch(unittest.TestCase):
    """pinyin_match：谐音/错别字/简繁/全拼 容错，且对无关串严格拒绝（准确率优先）。"""

    def test_typo_hits(self):
        self.assertTrue(pinyin_match('周杰轮', '周杰伦'))
        self.assertTrue(_fuzzy_match('周杰轮', '周杰伦'), '错别字应经拼音层命中')

    def test_full_pinyin_hits(self):
        self.assertTrue(pinyin_match('zhoujielun', '周杰伦'))
        self.assertTrue(_fuzzy_match('zhoujielun', '周杰伦'), '全拼输入应命中')

    def test_traditional_hits(self):
        self.assertTrue(pinyin_match('周傑倫', '周杰伦'))
        self.assertTrue(_fuzzy_match('告白氣球', '告白气球'), '繁体应命中')

    def test_unrelated_rejected(self):
        # 不同歌/歌手不能因共享少量拼音而误配
        self.assertFalse(pinyin_match('周杰伦', '林俊杰'))
        self.assertFalse(pinyin_match('告白气球', '青花瓷'))
        self.assertFalse(_fuzzy_match('周杰伦', '林俊杰'))

    def test_pure_latin_untouched(self):
        # 纯英文仍由 rapidfuzz 处理，拼音层不引入新误配
        self.assertFalse(_fuzzy_match('ireallylikeyou', 'shape of you'))


@unittest.skipIf(not PINYIN_AVAILABLE, skip_msg)
class TestPinyinScoring(unittest.TestCase):
    """_score_search_result：拼音层给"繁体/全拼"匹配合理加分，不破坏既准确档位。

    注：简体单字错别字已被 rapidfuzz ratio(>=60) 捕获；拼音层主要覆盖
    繁体（ratio<60）与全拼输入（量级差巨大）这类 rapidfuzz 抓不到的场景。
    """

    def test_perfect_still_150(self):
        self.assertEqual(_score_search_result('周杰伦', '周杰伦', '周杰伦', '周杰伦'), 150)

    def test_traditional_singer_gets_pinyin_tier(self):
        # 歌手写繁体（周傑倫）：rapidfuzz 抓不到（ratio<60），拼音命中 +15（而非 -10），歌名精确 +100
        sc = _score_search_result('告白气球', '周杰伦', '告白气球', '周傑倫')
        self.assertEqual(sc, 100 + 15)

    def test_traditional_name_gets_pinyin_tier(self):
        # 歌名写繁体（告白氣球）：拼音命中 +30（而非 +10 低匹配），歌手精确 +50
        sc = _score_search_result('告白氣球', '周杰伦', '告白气球', '周杰伦')
        self.assertEqual(sc, 30 + 50)

    def test_full_pinyin_singer_hits(self):
        # 歌手用全拼（zhoujielun）：拼音命中 +15，歌名精确 +100
        sc = _score_search_result('告白气球', '周杰伦', '告白气球', 'zhoujielun')
        self.assertEqual(sc, 100 + 15)

    def test_unrelated_not_boosted(self):
        # 无关串不得因拼音而加分
        sc = _score_search_result('青花瓷', '林俊杰', '青花瓷', '周杰伦')
        self.assertEqual(sc, 100 - 10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
