/*!
 * @name QA验收-最小标准源
 * @description QA夹具：与 minimal 同 @name 的旧版本副本（验证去重保留首选）
 * @version 0.9.0
 * @author QA-Horizon
 */

const musicSources = {
  wy: { name: 'QA最小标准源', type: 'music', actions: ['musicUrl'], qualitys: ['128k'] },
};
lx.on(lx.EVENT_NAMES.request, () => Promise.resolve('https://example.invalid/dup.mp3'));
lx.send(lx.EVENT_NAMES.inited, { status: true, sources: musicSources });
