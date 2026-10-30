// lxsdk 根 index：承接 musicSdk 内部 '../../index'（= lx 的 renderer/utils barrel 中被 musicSdk 用到的部分）
export * from './common_alias/utils/common.ts'
export * from './common_alias/utils/tools.ts'
export * from './common_alias/utils/nodejs.ts'
export * from './renderer_alias/utils.js'
export const formatPlayCount = (num) => {
  if (num > 100000000) return `${Math.trunc((num / 100000000) * 10) / 10}亿`
  return num > 10000 ? `${Math.trunc((num / 10000) * 10) / 10}万` : String(num)
}
// dateFormat2：原实现用 window.i18n（"N 秒/分钟/小时前"），Node 下等价中文文案
export { dateFormat } from './common_alias/utils/common.ts'
export const dateFormat2 = (time) => {
  const differ = Math.trunc((Date.now() - time) / 1000)
  if (differ < 60) return `${differ}秒前`
  if (differ < 3600) return `${Math.trunc(differ / 60)}分钟前`
  if (differ < 86400) return `${Math.trunc(differ / 3600)}小时前`
  return dateFormat(time)
}
