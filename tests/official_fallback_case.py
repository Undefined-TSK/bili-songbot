#!/usr/bin/env python3
"""官方标准音源场景独立进程用例：内置引擎回退 + musicInfo 字段保真。

由 tests/e2e_test.py 以子进程调用（避免主进程 bot 模块缓存污染——
主进程的 bot 已绑定 _run 目录的假脚本配置，同进程二次 import 不会重载）。
本用例只装载 official-source.js（模拟 lx 社区标准脚本：无 search 动作），
并断言搜索结果不是假脚本的 mock 数据（id 非 MUSIC_ 前缀）。
需要真实网络（国内音乐平台 API）。
"""
import asyncio
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


async def main():
    test_dir = os.path.join(HERE, '_run_official')
    shutil.rmtree(test_dir, ignore_errors=True)
    os.makedirs(os.path.join(test_dir, 'fake_sources'), exist_ok=True)
    shutil.copy(os.path.join(HERE, 'fake_sources', 'official-source.js'),
                os.path.join(test_dir, 'fake_sources', 'official-source.js'))
    shutil.copytree(os.path.join(PROJECT_ROOT, 'bot'), os.path.join(test_dir, 'bot'),
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy(os.path.join(PROJECT_ROOT, 'source_bridge.js'), os.path.join(test_dir, 'source_bridge.js'))
    shutil.copy(os.path.join(PROJECT_ROOT, 'musicSdk_bundle.cjs'), os.path.join(test_dir, 'musicSdk_bundle.cjs'))
    with open(os.path.join(test_dir, 'config.json'), 'w', encoding='utf-8') as f:
        f.write('{"bili_room_id":"123456","bili_sessdata":"x","bili_jct":"x","bili_buvid3":"x","bili_buvid4":"x",'
                '"source_dir":"' + os.path.join(test_dir, 'fake_sources').replace('\\', '/') + '","node_path":"node"}')

    sys.path.insert(0, test_dir)
    import bot  # 干净进程：此 bot 绑定 official-source.js 目录

    bridge = bot.SourceBridge()
    await bridge.start()
    try:
        # 用例 7：官方标准脚本（无 search）-> 内置引擎回退
        r = await bridge.search('kw', '告白气球', limit=5)
        assert r and any('告白气球' in x['name'] for x in r), \
            f'内置引擎回退应能搜到歌: {r and r[:2]}'
        first = next(x for x in r if '告白气球' in x['name'])
        sid = str(first.get('id') or first.get('songmid') or '')
        assert sid and not sid.startswith('MUSIC_'), \
            f'结果疑似假脚本 mock 数据（id={sid}），内置引擎回退未真正触发'
        print('7. 官方标准脚本（无 search）-> 内置引擎回退 OK ->',
              [(x['name'], x['singer']) for x in r[:2]])

        # 用例 8：musicInfo 字段保真（对齐落雪：搜索到播放传完整对象；kg 的 hash 是关键标识）
        r2 = await bridge.search('kg', '告白气球', limit=5)
        assert r2 and any('告白气球' in x['name'] for x in r2), 'kg 搜索失败'
        target = next(x for x in r2 if '告白气球' in x['name'])
        kg_sid = str(target.get('id') or target.get('songmid') or '')
        assert kg_sid and not kg_sid.startswith('MUSIC_'), f'kg 结果疑似 mock: {kg_sid}'
        url = await bridge.get_url('kg', {'name': target['name'], 'singer': target['singer'],
                                          'song_id': kg_sid}, '128k')
        # kg 平台 musicInfo 实有字段全保真（meta 仅 tx 平台有，不做跨平台强求）
        for token in ('hash-ok', 'songmid-ok', 'types-ok', '_types-ok'):
            assert token in url, f'musicInfo 字段保真失败（缺 {token}）: {url}'
        print('8. musicInfo 字段保真 OK（kg hash/songmid/types/_types 完整传递，与落雪一致）')
    finally:
        await bridge.stop()
        shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == '__main__':
    asyncio.run(main())
    print('=== 官方脚本场景全部通过 ===')
