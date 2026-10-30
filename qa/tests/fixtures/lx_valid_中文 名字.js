/*!
 * @name QA验收-中文 空格 文件名
 * @description QA夹具：中文+空格文件名兼容性
 * @version 1.0.0
 * @author QA-Horizon
 */

const musicSources = {
  kw: { name: 'QA中文文件名源', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
};
lx.on(lx.EVENT_NAMES.request, () => Promise.resolve('https://example.invalid/zh.mp3'));
lx.send(lx.EVENT_NAMES.inited, { status: true, sources: musicSources });
