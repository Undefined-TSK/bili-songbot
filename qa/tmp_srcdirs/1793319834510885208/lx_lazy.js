/*!
 * @name QA验收-惰性脚本
 * @description QA夹具：注册 handler 但永不发送 inited（内核 init 守卫应超时隔离）
 */

lx.on(lx.EVENT_NAMES.request, () => Promise.resolve('https://example.invalid/lazy.mp3'));
