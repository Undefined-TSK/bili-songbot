/*!
 * @name QA验收-最小标准源
 * @description QA夹具：最小合法 LX 标准音源（仅 wy 平台）
 * @version 1.0.0
 * @author QA-Horizon
 * @homepage https://example.com/qa
 */

const musicSources = {
  wy: { name: 'QA最小标准源', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
};

lx.on(lx.EVENT_NAMES.request, ({ action, source, info }) => {
  if (action !== 'musicUrl') return Promise.reject(new Error('action not supported'));
  return Promise.resolve('https://example.invalid/qa-minimal-audio.mp3');
});

lx.send(lx.EVENT_NAMES.inited, { status: true, sources: musicSources });
