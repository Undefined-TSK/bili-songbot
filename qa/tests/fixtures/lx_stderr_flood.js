/*!
 * @name QA验收-stderr洪水
 * @description QA夹具：向 stderr 写 2 万行日志后正常初始化（验证健壮性）
 */

for (let i = 0; i < 20000; i++) console.log('QA stderr flood line', i);
const musicSources = {
  wy: { name: 'QA洪水源', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
};
lx.on(lx.EVENT_NAMES.request, () => Promise.resolve('https://example.invalid/flood.mp3'));
lx.send(lx.EVENT_NAMES.inited, { status: true, sources: musicSources });
