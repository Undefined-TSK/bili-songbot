/**
 * @name 官方标准音源模拟
 * @version 1.0.0
 * @author lx-ecosystem-test
 * @description 模拟 lx 社区官方标准音源脚本：只支持 musicUrl，不支持 search
 */

lx.on('request', (request) => {
  if (request.action === 'musicUrl') {
    // 官方标准脚本只处理 musicUrl；把收到的 musicInfo 关键字段编进返回 URL，
    // 供 e2e 断言"搜索->播放"的 musicInfo 字段保真（对齐落雪行为）
    const m = request.info.musicInfo || {};
    const checks = [
      m.hash ? 'hash-ok' : 'hash-missing',
      m.songmid ? 'songmid-ok' : 'songmid-missing',
      m.types !== undefined ? 'types-ok' : 'types-missing',
      m._types !== undefined ? '_types-ok' : '_types-missing',
      m.meta !== undefined ? 'meta-ok' : 'meta-missing',
    ];
    return Promise.resolve('https://m701.music.126.net/official/' + (m.songmid || '') + '/' + checks.join('+') + '.mp3');
  }
  // 官方标准脚本不认识 search/pic/lyric 之外的扩展动作 -> 拒绝
  return Promise.reject(new Error('unsupported action: ' + request.action));
});

lx.send('inited', { sources: {
  kw: { type: 'music', actions: ['musicUrl'], qualitys: ['128k', '320k', 'flac'] },
  wy: { type: 'music', actions: ['musicUrl'], qualitys: ['128k', '320k', 'flac'] },
  tx: { type: 'music', actions: ['musicUrl'], qualitys: ['128k', '320k', 'flac'] },
} });
