/*!
 * @name 洛雪科技v4音源
 * @description 洛雪科技[独家音源]_v4。由项目原始已验证逻辑重建为 LX 标准脚本（星海 XINGHAI 后端），内核零改动加载。
 * @version v4.0.0
 * @author 洛雪科技 / 点歌机重建
 */
const DEV_ENABLE = false
const XINGHAI_MAIN = 'https://music-api.gdstudio.xyz/api.php?use_xbridge3=true&loader_name=forest&need_sec_link=1&sec_link_scene=im&theme=light'
const PLATFORM_TO_XINGHAI = {
  wy: 'netease', tx: 'tencent', kw: 'kuwo', kg: 'kugou', mg: 'migu',
}
const QUALITY_TO_BR = {
  '128k': '128', '192k': '192', '320k': '320', 'flac': '740', 'flac24bit': '999',
}
const MUSIC_QUALITY = {
  wy: ['128k', '320k', 'flac', 'flac24bit'],
  tx: ['128k', '320k', 'flac', 'flac24bit'],
  kw: ['128k', '320k', 'flac', 'flac24bit'],
  kg: ['128k', '320k', 'flac', 'flac24bit'],
  mg: ['128k', '320k', 'flac', 'flac24bit'],
}
const MUSIC_SOURCE = Object.keys(MUSIC_QUALITY)
const { EVENT_NAMES, request, on, send, utils, env, version } = globalThis.lx
const UA = `${env ? `lx-music-${env}/${version}` : 'lx-music-request'}`
const httpFetch = (url, options = { method: 'GET' }) => {
  return new Promise((resolve, reject) => {
    request(url, options, (err, resp) => {
      if (err) return reject(err)
      resolve(resp)
    })
  })
}
// 递归查找响应体中的第一个 http(s) 音频 URL（兼容嵌套返回）
const findUrl = (obj) => {
  if (typeof obj === 'string') {
    const m = obj.match(/https?:\/\/[^\s"'\\]+/)
    return m ? m[0] : null
  }
  if (Array.isArray(obj)) {
    for (const it of obj) {
      const r = findUrl(it)
      if (r) return r
    }
  } else if (obj && typeof obj === 'object') {
    for (const k of Object.keys(obj)) {
      if (typeof obj[k] === 'string' && /https?:\/\//.test(obj[k])) return obj[k]
      const r = findUrl(obj[k])
      if (r) return r
    }
  }
  return null
}
const getSongId = (musicInfo) => {
  const v = musicInfo.songmid ?? musicInfo.songId ?? musicInfo.hash ?? musicInfo.strMediaMid ?? musicInfo.id
  return v == null ? '' : String(v)
}
const handleGetMusicUrl = async (source, musicInfo, quality) => {
  const songId = getSongId(musicInfo)
  if (!songId) throw new Error('missing song id')
  const xsrc = PLATFORM_TO_XINGHAI[source] || source
  const br = QUALITY_TO_BR[quality] || '320'
  const url = `${XINGHAI_MAIN}&types=url&source=${xsrc}&id=${encodeURIComponent(songId)}&br=${br}`
  const resp = await httpFetch(url, { method: 'GET', headers: { 'User-Agent': UA } })
  const { body } = resp
  if (!body) throw new Error('empty response')
  const found = (body && typeof body === 'object' && body.url) ? body.url : findUrl(body)
  if (found) return found
  throw new Error('洛雪科技v4 no url')
}
const musicSources = {}
MUSIC_SOURCE.forEach(item => {
  musicSources[item] = {
    name: item,
    type: 'music',
    actions: ['musicUrl'],
    qualitys: MUSIC_QUALITY[item],
  }
})
on(EVENT_NAMES.request, ({ action, source, info }) => {
  if (action === 'musicUrl') {
    return handleGetMusicUrl(source, info.musicInfo, info.type)
      .then(data => Promise.resolve(data))
      .catch(err => Promise.reject(err))
  }
  return Promise.reject('action not support')
})
send(EVENT_NAMES.inited, { status: true, openDevTools: DEV_ENABLE, sources: musicSources })
