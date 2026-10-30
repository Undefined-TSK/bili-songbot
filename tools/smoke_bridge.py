import asyncio
import sys
sys.path.insert(0, '.')

from bot.bridge import SourceBridge


async def main():
    b = SourceBridge()
    await b.start()
    total = len(b._all_script_files)
    n_online = len([p for p in b.procs if b._online_dir and p.args[-1].startswith(b._online_dir)])
    deduped = total - len(b.procs) - len(b._failed_files)
    print(f"音源就绪: {len(b.procs)} 个唯一音源进程 / 候选脚本 {total}（本地+在线）"
          f"｜在线官方版 {n_online}｜同音源去重停用 {deduped} 副本｜失败待自愈 {len(b._failed_files)}")
    print(f"内置引擎: {'已启动' if b.builtin else '未启动'}")
    names = sorted(getattr(p, 'script_name', None) or p.name for p in b.procs)
    for n in names:
        print(f"  - {n}")
    # 统计平台覆盖
    await b.stop()


if __name__ == '__main__':
    asyncio.run(main())
