/*!
 * @name 听音音源
 * @description 听音音源 v2。由项目原始已验证逻辑重建为 LX 标准脚本（按歌名+歌手取链），内核零改动加载。
 * @version v2.0.0
 * @author 听音 / 点歌机重建
 */
const DEV_ENABLE = false
const API_URL = 'https://api.yaohud.cn'
const API_KEY = 'xwT5YzREvIwK8LVZ72n'
// 各平台音质映射（对齐项目原始 QUALITY_TO_TINGYIN）
const QUALITY_MAP = {
  wy: { '128k': 'standard', '192k': 'standard', '320k': 'exhigh', 'flac': 'lossless', 'flac24bit': 'hires' },
  tx: { '128k': 'mp3', '192k': 'mp3', '320k': 'hq', 'flac': 'sq', 'flac24bit': 'hires' },
  kw: { '128k': 'standard', '192k': 'standard', '320k': 'exhigh', 'flac': 'lossless', 'flac24bit': 'hires' },
  mg: { '128k': '128k', '192k': '128k', '320k': '320k', 'flac': 'flac', 'flac24bit': 'flac' },
}
// 听音按歌名取链，不支持 kg
const MUSIC_SOURCE = ['wy', 'tx', 'kw', 'mg']
const MUSIC_QUALITY = {
  wy: ['128k', '320k', 'flac', 'flac24bit'],
  tx: ['128k', '320k', 'flac', 'flac24bit'],
  kw: ['128k', '320k', 'flac', 'flac24bit'],
  mg: ['128k', '320k', 'flac', 'flac24bit'],
}
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
const getKeyword = (musicInfo) => {
  const name = musicInfo.name || musicInfo.songName || ''
  const singer = musicInfo.singer || musicInfo.artist || ''
  return (name + ' ' + singer).trim()
}
const handleGetMusicUrl = async (source, musicInfo, quality) => {
  const kw = getKeyword(musicInfo)
  if (!kw) throw new Error('missing keyword')
  const qmap = QUALITY_MAP[source] || {}
  const q = qmap[quality] || 'exhigh'
  let api
  if (source === 'wy') api = `${API_URL}/api/music/wyvip?key=${API_KEY}&msg=${encodeURIComponent(kw)}&n=1&level=${q}`
  else if (source === 'tx') api = `${API_URL}/api/music/qq_plus?key=${API_KEY}&msg=${encodeURIComponent(kw)}&n=1&size=${q}`
  else if (source === 'kw') api = `${API_URL}/api/music/kuwo?key=${API_KEY}&msg=${encodeURIComponent(kw)}&n=1&size=${q}`
  else if (source === 'mg') api = `${API_URL}/api/music/migu?key=${API_KEY}&msg=${encodeURIComponent(kw)}&n=1`
  else throw new Error('platform not supported')
  const resp = await httpFetch(api, { method: 'GET', headers: { 'User-Agent': UA } })
  const { body } = resp
  if (!body || typeof body !== 'object' || body.code !== 200 || !body.data) {
    throw new Error(body && body.msg ? body.msg : '听音 failed')
  }
  const d = body.data
  const url = (d && (d.url || d.music_url || (d.vipmusic && d.vipmusic.url))) || null
  if (url) return url
  throw new Error('听音 no url')
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
