/*!
 * @name QA验收-全平台标准源
 * @description QA夹具：声明全部 5 平台的合法 LX 标准音源
 * @version 1.0.0
 * @author QA-Horizon
 */

const musicSources = {
  wy: { name: 'QA全平台-网易云', type: 'music', actions: ['musicUrl'], qualitys: ['128k', '320k', 'flac'] },
  kw: { name: 'QA全平台-酷我', type: 'music', actions: ['musicUrl'], qualitys: ['128k', '320k'] },
  tx: { name: 'QA全平台-QQ', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
  kg: { name: 'QA全平台-酷狗', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
  mg: { name: 'QA全平台-咪咕', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
};

lx.on(lx.EVENT_NAMES.request, ({ action, source, info }) => {
  if (action !== 'musicUrl') return Promise.reject(new Error('action not supported'));
  return Promise.resolve('https://example.invalid/qa-multi-' + ((info && info.type) || 'none') + '.mp3');
});
lx.send(lx.EVENT_NAMES.inited, { status: true, sources: musicSources });
