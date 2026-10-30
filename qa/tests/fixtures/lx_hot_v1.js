/*!
 * @name QA验收-热更新源
 * @description QA夹具：供自更新测试（内容会被服务端改为 v2）
 * @version 1.0.0
 * @author QA-Horizon
 */

const musicSources = {
  wy: { name: 'QA热更新源', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
};
lx.on(lx.EVENT_NAMES.request, () => Promise.resolve('https://example.invalid/hot-v1.mp3'));
lx.send(lx.EVENT_NAMES.inited, { status: true, sources: musicSources });
