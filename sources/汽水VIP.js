/*!
 * @name 汽水VIP音源
 * @description 汽水音乐 VIP 源。由项目原始已验证逻辑重建为 LX 标准脚本，内核零改动加载。
 * @version v1.0.0
 * @author Qishui / 点歌机重建
 */
const DEV_ENABLE = false
const API_URL_HTTPS = 'https://api.vsaa.cn/api/music.qishui.vip'
const API_URL_HTTP = 'http://api.vsaa.cn/api/music.qishui.vip'
const PROXY_URL = 'https://proxy.qishui.vsaa.cn/qishui/proxy'
const QUALITY_MAP = {
  '128k': 'standard', '192k': 'standard', '320k': 'exhigh',
  'flac': 'lossless', 'flac24bit': 'flac24bit',
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
const getSongId = (musicInfo) => {
  const v = musicInfo.songmid ?? musicInfo.songId ?? musicInfo.hash ?? musicInfo.strMediaMid ?? musicInfo.id
  return v == null ? '' : String(v)
}
const handleGetMusicUrl = async (source, musicInfo, quality) => {
  const songId = getSongId(musicInfo)
  if (!songId) throw new Error('missing song id')
  const q = QUALITY_MAP[quality] || 'standard'
  let lastErr = null
  for (const base of [API_URL_HTTPS, API_URL_HTTP]) {
    try {
      const url = `${base}?act=song&id=${encodeURIComponent(songId)}&quality=${q}`
      const resp = await httpFetch(url, { method: 'GET', headers: { 'User-Agent': UA } })
      const { body } = resp
      if (!body || typeof body !== 'object' || body.code !== 200) continue
      let d = body.data
      if (Array.isArray(d)) d = d[0]
      if (!d || typeof d !== 'object') continue
      if (d.url && !d.ekey) return d.url
      if (d.url && d.ekey) {
        const proxyResp = await httpFetch(PROXY_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'User-Agent': UA },
          body: JSON.stringify({
            url: d.url,
            key: d.ekey,
            filename: d.filename || 'KMusic',
            ext: String(d.fileExtension || 'aac'),
          }),
        })
        const pbody = proxyResp.body
        if (pbody && typeof pbody === 'object' && pbody.code === 200 && pbody.url) {
          return String(pbody.url)
        }
      }
    } catch (e) {
      lastErr = e
    }
  }
  throw new Error(lastErr ? lastErr.message : '汽水VIP failed')
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
