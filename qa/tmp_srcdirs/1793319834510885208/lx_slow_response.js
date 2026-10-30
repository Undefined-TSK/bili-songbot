/*!
 * @name QA验收-慢响应源
 * @description QA夹具：musicUrl 请求挂起 30 秒不回调（验证竞速超时不阻塞）
 */

const musicSources = {
  wy: { name: 'QA慢响应源', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
};
lx.on(lx.EVENT_NAMES.request, () => new Promise((resolve) => setTimeout(() => resolve('https://example.invalid/slow.mp3'), 30000)));
lx.send(lx.EVENT_NAMES.inited, { status: true, sources: musicSources });
