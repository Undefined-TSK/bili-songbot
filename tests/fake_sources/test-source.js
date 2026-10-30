/**
 * @name S0验证假音源
 * @version 1.0.0
 * @author s0-e2e-test
 * @description 端到端验证用假音源：无网络，本地假数据
 */

const FAKE_DB = [
  { id: 'MUSIC_001', name: '告白气球', singer: '周杰伦', albumName: '周杰伦的床边故事', interval: '03:35' },
  { id: 'MUSIC_002', name: '晴天', singer: '周杰伦', albumName: '叶惠美', interval: '04:29' },
  { id: 'MUSIC_003', name: '平凡之路', singer: '朴树', albumName: '猎户星座', interval: '05:02' },
];

lx.on('request', (request) => {
  if (request.action === 'search') {
    const kw = request.info.keyword;
    const tokens = kw.split(/\s+/).filter(Boolean); const hits = FAKE_DB.filter(s => tokens.some(t => s.name.includes(t) || s.singer.includes(t) || s.albumName.includes(t)));
    return Promise.resolve(hits);
  }
  if (request.action === 'musicUrl') {
    const mid = request.info.musicInfo.songmid;
    return Promise.resolve('https://m701.music.126.net/fake/' + mid + '/test.mp3');
  }
  return Promise.reject(new Error('unknown action: ' + request.action));
});

lx.send('inited', { sources: {
  kw: { actions: ['search', 'musicUrl'] },
  wy: { actions: ['search', 'musicUrl'] },
  tx: { actions: ['search', 'musicUrl'] },
  kg: { actions: ['search', 'musicUrl'] },
  mg: { actions: ['search', 'musicUrl'] },
} });
