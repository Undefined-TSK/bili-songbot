#!/usr/bin/env python3
"""S0 端到端回归测试：无需真实网络/B站账号，验证删减+模块化后完整点歌链路。
用法: python3 tests/e2e_test.py
前置: 系统有 node（桥接器需要）；在项目根目录运行。"""
import asyncio
import atexit
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault('BILI_ROOM_ID_TEST', '1')

# 注入测试配置（不影响真实 config.json：测试用独立目录）
TEST_DIR = os.path.join(HERE, '_run')
os.makedirs(os.path.join(TEST_DIR, 'fake_sources'), exist_ok=True)
shutil.copy(os.path.join(HERE, 'fake_sources', 'test-source.js'),
            os.path.join(TEST_DIR, 'fake_sources', 'test-source.js'))
shutil.copytree(os.path.join(PROJECT_ROOT, 'bot'), os.path.join(TEST_DIR, 'bot'),
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
shutil.copy(os.path.join(PROJECT_ROOT, 'source_bridge.js'), os.path.join(TEST_DIR, 'source_bridge.js'))
with open(os.path.join(TEST_DIR, 'config.json'), 'w', encoding='utf-8') as f:
    f.write('{"bili_room_id":"123456","bili_sessdata":"x","bili_jct":"x","bili_buvid3":"x","bili_buvid4":"x",'
            '"source_dir":"' + os.path.join(TEST_DIR, 'fake_sources').replace('\\', '/') + '","node_path":"node"}')

sys.path.insert(0, TEST_DIR)
# 进程退出时清理临时脚手架（覆盖脚本运行与 pytest import 副作用两种路径）
atexit.register(shutil.rmtree, TEST_DIR, ignore_errors=True)
import bot


async def main():
    # 包结构冒烟：模块拆分后全部导出可用
    for sym in ('SourceBridge', 'set_bridge', 'get_bridge', 'search_song',
                'SongInfo', 'PlayQueue', 'VoteManager', 'BiliMusicBot',
                'CookieManager', 'PlaybackManager', 'BiliDanmaku', 'BiliSender'):
        assert hasattr(bot, sym), f'包导出缺失: {sym}'
    print('0. bot 包 12 个核心符号导出 OK（9 模块拆分无遗漏）')

    # S1/S2/S3 适配器可导入性冒烟（均未接主路径，导入不应有任何副作用）
    from bot import danmaku_biliapi, player_mpv, search_rapidfuzz
    from bot.search import FUZZY_MATCH, SCORE_RESULT
    assert FUZZY_MATCH is bot.search._fuzzy_match, '评分器默认钩子应指向手写版（未启用 S3）'
    print('0b. S1/S2/S3 适配器导入冒烟 OK（danmaku_biliapi/player_mpv/search_rapidfuzz，默认未启用）')

    bridge = bot.SourceBridge()
    await bridge.start()
    bot.set_bridge(bridge)
    assert bot.get_bridge() is bridge
    print('1. 桥接器启动 + lx 音源脚本加载 + set/get_bridge 单例 OK')
    results = await bridge.search('kw', '告白气球', limit=5)
    assert results and any(r['name'] == '告白气球' for r in results), '搜索结果不符'
    print('2. bridge.search 链路 OK ->', [(r['name'], r['singer']) for r in results])
    url = await bridge.get_url('kw', {'name': '告白气球', 'singer': '周杰伦', 'song_id': 'MUSIC_001'}, '128k')
    assert url and url.startswith('http'), 'URL 不符'
    print('3. bridge.get_url 链路 OK ->', url)
    song = await bot.search_song(None, '告白气球', '周杰伦')
    assert song is not None and song.name == '告白气球' and song.song_id == '001', f'search_song 异常: {song}'
    print('4. search_song 业务层（get_bridge 单例）OK ->', song.name, '/', song.singer, '/ id:', song.song_id, '/ 平台:', song.platform)
    song2 = await bot.search_song(None, '不存在的歌XYZ', '')
    assert song2 is None
    print('5. 空结果场景 OK -> None（不炸）')

    # 单例守卫：未启动桥时调用必须报清晰错误而非 NameError
    saved = bot.bridge._bridge
    bot.bridge._bridge = None
    try:
        await bot.search_song(None, '告白气球', '周杰伦')
        raise AssertionError('未启动桥时应抛 RuntimeError')
    except RuntimeError as e:
        assert 'set_bridge' in str(e)
        print('6. 未启动桥守卫 OK ->', e)
    finally:
        bot.bridge._bridge = saved

    await bridge.stop()
    print('=== 端到端全部通过 ===')


async def check_official_script_fallback():
    """官方标准音源场景（内置引擎回退 + musicInfo 保真）。

    以独立子进程运行（tests/official_fallback_case.py）：
    同进程内 bot 模块会被 main() 的 import 缓存（绑定 _run 假脚本配置），
    独立进程保证干净加载 + 防假绿断言（结果非 mock 数据）。需要真实网络。
    """
    import subprocess
    case = os.path.join(HERE, 'official_fallback_case.py')
    r = subprocess.run([sys.executable, case], capture_output=True, text=True, timeout=180)
    print(r.stdout)
    assert r.returncode == 0, f'官方脚本场景用例失败:\n{r.stdout}\n{r.stderr[-2000:]}'


if __name__ == '__main__':
    asyncio.run(main())
    asyncio.run(check_official_script_fallback())
    print('=== 全部完成（含内置引擎回退） ===')
