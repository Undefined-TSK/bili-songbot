#!/usr/bin/env python3
"""匹配器单元测试：_normalize_text / _fuzzy_match / _score_search_result / extract_artist。
用法: python3 tests/test_matcher.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.core import split_play_query
from bot.search import _fuzzy_match, _normalize_text, _score_search_result, extract_artist


class TestNormalizeText(unittest.TestCase):

    def test_brackets_and_punct(self):
        self.assertEqual(_normalize_text('告白气球 (Live)'), '告白气球', '括号及括号内内容整体去除')
        self.assertEqual(_normalize_text('夜曲（独白版）'), '夜曲')
        self.assertEqual(_normalize_text('Hello, World!'), 'helloworld')
        self.assertEqual(_normalize_text(' 稻 香 '), '稻香')
        self.assertEqual(_normalize_text(''), '')
        # 日文假名保留（\u3040-\u30ff 段）
        self.assertEqual(_normalize_text('さくら'), 'さくら')


class TestFuzzyMatch(unittest.TestCase):

    def test_exact_and_substring(self):
        self.assertTrue(_fuzzy_match('告白气球', '告白气球'))
        self.assertTrue(_fuzzy_match('告白气球', '告白气球 (Live)'), '标准化后子串应命中')
        self.assertTrue(_fuzzy_match('夜曲', '夜曲'))

    def test_char_overlap(self):
        self.assertTrue(_fuzzy_match('告白气球', '告白求球'), '6 字中 5 字相同 >= 60%')

    def test_mismatch(self):
        self.assertFalse(_fuzzy_match('告白气球', '完全无关的歌名'))
        self.assertFalse(_fuzzy_match('', '告白气球'), '空关键词不匹配')


class TestScore(unittest.TestCase):

    def test_perfect(self):
        # 歌名完全 + 歌手完全 = 150（search_song 的完美匹配阈值）
        self.assertEqual(_score_search_result('告白气球', '周杰伦', '告白气球', '周杰伦'), 150)

    def test_name_only(self):
        self.assertEqual(_score_search_result('告白气球', '', '告白气球', ''), 100)
        self.assertEqual(_score_search_result('告白气球XYZ', '', '告白气球', ''), 80)
        # 包含关键词+50，"新版"为衍生版本强降权-55 => -5（低于门禁，确保原版优先）
        self.assertEqual(_score_search_result('新版告白气球', '', '告白气球', ''), -5)

    def test_suffix_penalty(self):
        # 衍生版本强降权：'Live' 命中惩罚 -55，歌名完全+100，歌手+50 => 95
        self.assertEqual(_score_search_result('告白气球 (Live)', '周杰伦', '告白气球', '周杰伦'), 95)
        # '伴奏' 命中惩罚 -55，歌名以关键词开头+80，歌手+50 => 75
        self.assertEqual(_score_search_result('告白气球 伴奏', '周杰伦', '告白气球', '周杰伦'), 75)

    def test_singer_dimension(self):
        # 歌手不匹配 -10
        self.assertEqual(_score_search_result('告白气球', '费玉清', '告白气球', '周杰伦'), 90)
        # 歌手包含 +30
        self.assertEqual(_score_search_result('告白气球', '周杰伦/费玉清', '告白气球', '周杰伦'), 130)

    def test_fuzzy_name_tier(self):
        # 模糊歌名 +30，无歌手不加分
        self.assertEqual(_score_search_result('告白求球', '', '告白气球', ''), 30)
        # 完全不匹配 +10
        self.assertEqual(_score_search_result('毫无关系', '', '告白气球', ''), 10)


class TestFuzzyFreeInput(unittest.TestCase):
    """自由输入模糊匹配：中文缺字/乱序重排、英文大小写/空格/无空格、多语言容错。

    对应需求：play 命令高度自由——歌手与歌名顺序可颠倒、分隔符随意甚至无分隔，
    且歌手与歌名均支持模糊匹配（中文缺字/重排，英文不区分大小写与空格、可无空格）。
    """

    # 中文：缺字 + 乱序重排 都应命中
    def test_cjk_missing_chars(self):
        self.assertTrue(_fuzzy_match('告白球', '告白气球'))
        self.assertTrue(_fuzzy_match('告白气', '告白气球'))

    def test_cjk_reorder(self):
        self.assertTrue(_fuzzy_match('气球告白', '告白气球'))
        self.assertTrue(_fuzzy_match('气告白球', '告白气球'))

    # 歌手缩写/乱序 都应命中
    def test_singer_abbrev(self):
        self.assertTrue(_fuzzy_match('周杰', '周杰伦'))
        self.assertTrue(_fuzzy_match('周伦', '周杰伦'))
        self.assertTrue(_fuzzy_match('杰伦', '周杰伦'))

    # 英文：大小写 / 空格 / 无空格 / 缺字母 都应命中
    def test_english_case_space(self):
        self.assertTrue(_fuzzy_match('I really like you', 'I Really Like You'))
        self.assertTrue(_fuzzy_match('IRELLYLIKEYOU', 'I really like you'))   # 缺字母 a + 无空格
        self.assertTrue(_fuzzy_match('IreallyLikeYOU', 'I really like you'))  # 大小写混排 + 无空格
        self.assertTrue(_fuzzy_match('Ireally like y', 'I really like you'))  # 部分无空格 + 截断
        self.assertTrue(_fuzzy_match('I reallylike', 'I really like you'))    # 缺尾词

    # 无关内容必须拒绝（避免误匹配）——准确率保障
    def test_reject_unrelated(self):
        self.assertFalse(_fuzzy_match('告白气球', '青花瓷'))
        # 关键：共享单字"杰"的不同歌手必须拒绝（Jaccard 多重集不被单字误导）
        self.assertFalse(_fuzzy_match('周杰伦', '林俊杰'))
        self.assertFalse(_fuzzy_match('ireallylikeyou', 'shape of you'))

    # 短查询（单/双字）不应过度匹配——避免误配，兼顾准确率
    def test_short_query_no_overmatch(self):
        # 完全无关的单/双字必须拒绝
        self.assertFalse(_fuzzy_match('天', '告白气球'), '无关单字不应匹配')
        self.assertFalse(_fuzzy_match('大海', '告白气球'), '无关双字不应匹配')
        # 子串包含（气球⊂告白气球、周⊂周杰伦）仍应匹配（真实场景合理）
        self.assertTrue(_fuzzy_match('气球', '告白气球'))
        self.assertTrue(_fuzzy_match('周', '周杰伦'))
        # 2 字完全转置（天晴≈晴天）应匹配（用户要求顺序可颠倒）
        self.assertTrue(_fuzzy_match('天晴', '晴天'))


class TestSplitPlayQuery(unittest.TestCase):
    """/play 自由输入解析：顺序可颠倒、分隔符随意甚至无分隔。"""

    def test_explicit_delimiter_both_orders(self):
        c = split_play_query('告白气球 - 周杰伦')
        self.assertIn(('告白气球', '周杰伦'), c)
        self.assertIn(('周杰伦', '告白气球'), c)

    def test_reversed_order(self):
        c = split_play_query('周杰伦/告白气球')
        self.assertIn(('告白气球', '周杰伦'), c)

    def test_no_delimiter_chinese_split(self):
        # 无分隔中文：jieba 拆词，把最长词当歌手、其余当歌名，两种顺序都试（自由度+准确率）
        c = split_play_query('告白气球周杰伦')
        self.assertIn(('告白气球', '周杰伦'), c, '应拆出(歌名,歌手)')
        self.assertIn(('周杰伦', '告白气球'), c, '应拆出(歌手,歌名)颠倒顺序')
        self.assertIn(('告白气球周杰伦', ''), c, '整句兜底仍保留')

    def test_no_delimiter_reversed(self):
        c = split_play_query('周杰伦告白气球')
        self.assertIn(('告白气球', '周杰伦'), c)
        self.assertIn(('周杰伦', '告白气球'), c)

    def test_no_delimiter_english_whole(self):
        # 英文无分隔/多词：不按空格切、不 jieba 拆，整句兜底（保护 "see you again"）
        self.assertEqual(split_play_query('see you again'), [('see you again', '')])
        self.assertEqual(split_play_query('ireallylikeyou'), [('ireallylikeyou', '')])

    def test_single_word_no_split(self):
        # 单 token（歌名本就无分隔）只整句，不引入噪声候选
        self.assertEqual(split_play_query('七里香'), [('七里香', '')])

    def test_empty(self):
        self.assertEqual(split_play_query(''), [])
        self.assertEqual(split_play_query('   '), [])


class TestExtractArtist(unittest.TestCase):

    def test_string_separators(self):
        self.assertEqual(extract_artist('周杰伦&费玉清'), '周杰伦/费玉清')
        self.assertEqual(extract_artist('周杰伦, 费玉清'), '周杰伦/费玉清')
        self.assertEqual(extract_artist('周杰伦、费玉清'), '周杰伦/费玉清')
        self.assertEqual(extract_artist('A feat. B'), 'A/B')
        self.assertEqual(extract_artist('周杰伦'), '周杰伦')

    def test_list_and_dict(self):
        self.assertEqual(extract_artist(['周杰伦', '费玉清']), '周杰伦/费玉清')
        self.assertEqual(extract_artist([{'name': '周杰伦'}, {'name': '费玉清'}]), '周杰伦/费玉清')
        self.assertEqual(extract_artist({'name': '周杰伦'}), '周杰伦')

    def test_empty_and_numbers(self):
        self.assertEqual(extract_artist(''), '')
        self.assertEqual(extract_artist(None), '')
        self.assertEqual(extract_artist([]), '')
        self.assertEqual(extract_artist([1, 2]), '1/2')


if __name__ == '__main__':
    unittest.main(verbosity=2)
