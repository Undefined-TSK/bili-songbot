#!/usr/bin/env python3
"""批量点歌单元测试（TDD：先 RED 后 GREEN）。

设计铁律（用户要求，2026-10-29 整改）：
1. 批量 = 把一条 /play 查询拆成多个独立单曲查询，逐个走现有单曲逻辑——单曲代码零改动。
2. **拆分绝不依赖歌手别名数据库**（普适性）：结构候选（每段一组 / 两两一组）+
   搜索验证（高分 + 歌手与组内段互认 + 组间不同歌），全部通过才成立。
3. 别名数据库只做本职工作：把用户输入的歌手别名替换/互认成真实歌手，不参与解析拆分。
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bot.batch import batch_plans, cap_batch_queries, MAX_BATCH_SONGS
from bot.artist_alias import canonical_display
from bot.core import split_play_query
from bot.models import SongInfo


def _song(name, singer, platform="kw", score=150):
    return SongInfo(name=name, singer=singer, platform=platform, song_id="1", score=score)


class TestCanonicalDisplayIsAliasOnly(unittest.TestCase):
    """别名数据库本职 API：别名 -> 规范写法（只做替换/互认，不做结构解析）。"""

    def test_hybrid_to_display(self):
        self.assertEqual(canonical_display('迈克尔jackson'), 'Michael Jackson')

    def test_nickname_to_display(self):
        self.assertEqual(canonical_display('水果姐'), 'Katy Perry')
        self.assertEqual(canonical_display('蹲妹'), 'Carly Rae Jepsen')
        self.assertEqual(canonical_display('少女时代'), '少女时代')

    def test_unknown_returns_none(self):
        self.assertIsNone(canonical_display('李荣浩'))
        self.assertIsNone(canonical_display(''))

    def test_split_play_query_appends_variant_later(self):
        # 本职用法：把用户输入的歌手别名替换成真实歌手名作为追加搜索候选
        c = split_play_query('thriller 迈克尔jackson')
        self.assertEqual(c[0], ('thriller 迈克尔jackson', ''))
        self.assertIn(('thriller', 'Michael Jackson'), c)
        self.assertLess(c.index(('thriller', '迈克尔jackson')),
                        c.index(('thriller', 'Michael Jackson')))


class TestBatchPlansZeroDict(unittest.TestCase):
    """结构候选生成：零词典、纯结构规则（分号可信；每段一组/两两一组需验证）。"""

    def test_semicolon_trusted(self):
        plans = batch_plans('告白气球 周杰伦；稻香 周杰伦；Lemon 米津玄師')
        self.assertEqual(plans, [(['告白气球 周杰伦', '稻香 周杰伦', 'Lemon 米津玄師'], True)])

    def test_per_segment_candidate(self):
        # >=3 段：候选1 = 首段歌手假设（歌手段拼进每组子查询，组内顺序由单曲解析处理）
        plans = batch_plans('水果姐 firework birthday roar')
        self.assertIn((['水果姐 firework', '水果姐 birthday', '水果姐 roar'], False), plans)

    def test_pairwise_candidate(self):
        # 偶数段：候选2 = 两两一组（歌 歌手 交替，组内顺序交给单曲解析）
        plans = batch_plans('周杰伦 稻香 林俊杰 江南')
        self.assertIn((['周杰伦 稻香', '林俊杰 江南'], False), plans)

    def test_unknown_artist_segments_still_get_candidates(self):
        # 普适性核心：不在任何数据库里的歌手也照常生成候选（验证交给搜索结果）
        plans = batch_plans('李荣浩 模特 李白')
        self.assertTrue(plans, '零词典：未收录歌手也必须产生结构候选')
        self.assertIn((['李荣浩 模特', '李荣浩 李白'], False), plans)

    def test_two_segments_no_plan(self):
        # 经典单曲两段绝不产生批量计划
        self.assertEqual(batch_plans('告白气球 周杰伦'), [])
        self.assertEqual(batch_plans('gee 少女时代'), [])

    def test_multiword_song_gets_candidates_but_verification_guards(self):
        # 多词歌名（call me maybe 蹲妹）会产生结构候选（零词典无法预判），
        # 但搜索验证会拒绝（见 TestBatchVerify）——保护移到验证层
        plans = batch_plans('call me maybe 蹲妹')
        self.assertTrue(plans)
        self.assertTrue(all(not trusted for _, trusted in plans))

    def test_empty(self):
        self.assertEqual(batch_plans(''), [])
        self.assertEqual(batch_plans('  '), [])


class TestBatchVerify(unittest.TestCase):
    """搜索验证：结构候选必须靠搜索结果确认（mock search_song，不碰真实网络）。"""

    def _run_verify(self, groups, fake_search):
        from bot.core import BiliMusicBot
        bot_ins = BiliMusicBot()
        bot_ins.session = None
        with patch("bot.core.search_song", side_effect=fake_search):
            return asyncio.run(bot_ins._verify_batch_groups(groups))

    def test_all_groups_pass(self):
        # 每组搜到高分歌且歌手与组内某段互认 -> 批量成立
        resp = {"水果姐 firework": _song("Firework", "Katy Perry"),
                "水果姐 birthday": _song("Birthday", "Katy Perry"),
                "水果姐 roar": _song("Roar", "Katy Perry")}
        songs = self._run_verify(['水果姐 firework', '水果姐 birthday', '水果姐 roar'],
                                 lambda sess, kw, sg: resp.get(kw))
        self.assertIsNotNone(songs)
        self.assertEqual([s.name for s in songs], ['Firework', 'Birthday', 'Roar'])

    def test_zero_dict_artist_passes(self):
        # 普适性：数据库外歌手（李荣浩）靠歌手模糊匹配一致即可成立
        resp = {"李荣浩 模特": _song("模特", "李荣浩"),
                "李荣浩 李白": _song("李白", "李荣浩")}
        songs = self._run_verify(['李荣浩 模特', '李荣浩 李白'],
                                 lambda sess, kw, sg: resp.get(kw))
        self.assertIsNotNone(songs)
        self.assertEqual(len(songs), 2)

    def test_low_score_rejects(self):
        # 任一组置信度不足 -> 该候选作废（宁缺毋滥，回退单曲）
        resp = {"a b": _song("B", "X", score=150),
                "a c": _song("C", "X", score=60)}
        songs = self._run_verify(['a b', 'a c'], lambda sess, kw, sg: resp.get(kw))
        self.assertIsNone(songs)

    def test_singer_mismatch_rejects(self):
        # 组结果的歌手与组内任何一段都对不上 -> 拆分有误，作废
        resp = {"u will": _song("Whatever", "随便某歌手", score=150),
                "nev know": _song("Know", "随便某歌手", score=150)}
        songs = self._run_verify(['u will', 'nev know'],
                                 lambda sess, kw, sg: resp.get(kw))
        self.assertIsNone(songs)

    def test_not_found_rejects(self):
        songs = self._run_verify(['a b', 'a c'], lambda sess, kw, sg: None)
        self.assertIsNone(songs)

    def test_duplicate_song_rejects(self):
        # 两组命中同一首 -> 说明拆错了，作废
        songs = self._run_verify(['a b', 'a c'],
                                 lambda sess, kw, sg: _song("Same", "X"))
        self.assertIsNone(songs)


class TestBatchCap(unittest.TestCase):
    """批量上限截断：超限只取前 N 个（config.json: max_batch_songs）。"""

    def test_truncate_over_cap(self):
        many = [f'song{i}' for i in range(MAX_BATCH_SONGS + 3)]
        kept, dropped = cap_batch_queries(many)
        self.assertEqual(len(kept), MAX_BATCH_SONGS)
        self.assertEqual(dropped, 3)

    def test_cap_constant_in_config(self):
        self.assertIsInstance(MAX_BATCH_SONGS, int)
        self.assertGreaterEqual(MAX_BATCH_SONGS, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
