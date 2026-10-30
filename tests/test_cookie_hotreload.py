#!/usr/bin/env python3
"""CookieManager 热更新单元测试：改 config.json -> 检测 -> reload -> 双模块写回 -> headers 重建。

用一个隔离副本项目结构跑，不动真实 config.json（用完即清理）。
注意：reload_config 与 protocol 模块全局比较，用例间必须还原全局（模拟进程重启）。
用法: python3 tests/test_cookie_hotreload.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

# 隔离副本：把 bot/ 包 + source_bridge.js + config.json 复制到临时目录再 import
TMP = tempfile.mkdtemp(prefix='bili_cookie_test_')
shutil.copytree(os.path.join(PROJECT_ROOT, 'bot'), os.path.join(TMP, 'bot'),
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
shutil.copy(os.path.join(PROJECT_ROOT, 'source_bridge.js'), os.path.join(TMP, 'source_bridge.js'))

CFG = {
    "bili_room_id": "123456",
    "bili_sessdata": "old_sessdata_value",
    "bili_jct": "old_jct",
    "bili_buvid3": "old_buvid3",
    "bili_buvid4": "old_buvid4",
    "source_dir": "sources",
    "node_path": "node",
}
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as f:
    json.dump(CFG, f)

sys.path.insert(0, TMP)
import bot.config as config            # noqa: E402
import bot.protocol as protocol        # noqa: E402
from bot.protocol import CookieManager  # noqa: E402

OLD = ('old_sessdata_value', 'old_jct', 'old_buvid3', 'old_buvid4')


def _write_cfg(sessdata, jct, buvid3='old_buvid3', buvid4='old_buvid4'):
    """改配置并强制推进 mtime（某些文件系统秒级精度不够）。"""
    cfg = dict(CFG, bili_sessdata=sessdata, bili_jct=jct, bili_buvid3=buvid3, bili_buvid4=buvid4)
    with open(config.CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f)
    future = time.time() + 10
    os.utime(config.CONFIG_PATH, (future, future))


def _reset_module_state():
    """还原 protocol/config 两模块的 Cookie 全局 = 模拟进程刚启动。"""
    protocol.SESSDATA, protocol.BILI_JCT = OLD[0], OLD[1]
    protocol.BILI_BUVID3_CFG, protocol.BILI_BUVID4_CFG = OLD[2], OLD[3]
    config.SESSDATA, config.BILI_JCT = OLD[0], OLD[1]
    config.BILI_BUVID3_CFG, config.BILI_BUVID4_CFG = OLD[2], OLD[3]
    protocol.BILI_COOKIE = f"SESSDATA={OLD[0]}; bili_jct={OLD[1]}; buvid3={OLD[2]}; buvid4={OLD[3]}"
    protocol.BILI_HEADERS["Cookie"] = protocol.BILI_COOKIE


class TestCookieHotReload(unittest.TestCase):

    def setUp(self):
        _write_cfg(*OLD)
        _reset_module_state()
        time.sleep(0.01)
        self.mgr = CookieManager()

    def test_detect_and_reload(self):
        self.assertFalse(self.mgr.check_hot_reload(), '刚构造完不应触发热更新')
        _write_cfg('new_sessdata_value', 'new_jct', buvid3='new_buvid3', buvid4='new_buvid4')
        self.assertTrue(self.mgr.check_hot_reload(), 'config.json 变更应被检测到')
        self.assertTrue(self.mgr.reload_config(), 'reload 应报告已更新')
        # protocol 侧全局已更新
        self.assertEqual(protocol.SESSDATA, 'new_sessdata_value')
        self.assertEqual(protocol.BILI_JCT, 'new_jct')
        # config 模块侧已写回（拆包后双向同步，防止 from-import 快照）
        self.assertEqual(config.SESSDATA, 'new_sessdata_value')
        self.assertEqual(config.BILI_JCT, 'new_jct')
        # headers 重建包含新 Cookie
        self.assertIn('SESSDATA=new_sessdata_value', protocol.BILI_HEADERS['Cookie'])
        self.assertIn('bili_jct=new_jct', protocol.BILI_HEADERS['Cookie'])
        self.assertIn('buvid3=new_buvid3', protocol.BILI_HEADERS['Cookie'])
        self.assertTrue(self.mgr.valid)

    def test_no_change_no_reload(self):
        """契约说明：reload_config 返回 True = 重载尝试完成（原件行为，调用方忽略返回值）；
        内容无变化时真正的可观测效果是 headers 不被重建。"""
        before = protocol.BILI_HEADERS["Cookie"]
        _write_cfg(*OLD)  # 内容与原值完全相同
        self.assertTrue(self.mgr.check_hot_reload(), 'mtime 变化应被检测（内容相同由 reload 内部去重）')
        self.assertTrue(self.mgr.reload_config())
        self.assertEqual(protocol.BILI_HEADERS["Cookie"], before, '内容无变化不应重建 Cookie headers')

    def test_incomplete_cookie_ignored(self):
        """缺 jct：不应进入半截状态（sessdata/jct 必须成对出现，与原件一致）。"""
        _write_cfg('only_sessdata', '', buvid3=OLD[2], buvid4=OLD[3])
        self.assertTrue(self.mgr.check_hot_reload())
        self.assertTrue(self.mgr.reload_config())
        # 全局保持旧值，未被半截写入
        self.assertEqual(protocol.SESSDATA, OLD[0])
        self.assertEqual(protocol.BILI_JCT, OLD[1])
        self.assertIn(f'SESSDATA={OLD[0]}', protocol.BILI_HEADERS["Cookie"])

    def tearDown(self):
        _write_cfg(*OLD)
        _reset_module_state()


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2, exit=False)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
