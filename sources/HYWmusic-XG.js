/*!
 * @name HYWmusic-XG音源
 * @description 西瓜聚合中的 HYWmusic 源（XG 版）。由项目原始已验证逻辑重建为 LX 标准脚本，内核零改动加载。
 * @version v1.0.0
 * @author HYWmusic / 点歌机重建
 */
const DEV_ENABLE = false
const API_URL = 'https://music.bxa241d4.shop'
const CARD_KEY = 'TF-VSS0-8Y73-U1AW-GEXJ'
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
// 兼容各平台 ID 字段（songmid / songId / hash / strMediaMid）
const getSongId = (musicInfo) => {
  const v = musicInfo.songmid ?? musicInfo.songId ?? musicInfo.hash ?? musicInfo.strMediaMid ?? musicInfo.id
  return v == null ? '' : String(v)
}
const handleGetMusicUrl = async (source, musicInfo, quality) => {
  const songId = getSongId(musicInfo)
  if (!songId) throw new Error('missing song id')
  const url = `${API_URL}/api/music/url?source=${source}&songId=${encodeURIComponent(songId)}&quality=${encodeURIComponent(quality)}`
  const resp = await httpFetch(url, {
    method: 'GET',
    headers: {
      'X-Card-Key': CARD_KEY,
      'X-Script-Version': 'HYWmusic_beta',
      'User-Agent': UA,
    },
  })
  const { body } = resp
  if (!body || typeof body !== 'object') throw new Error('invalid response')
  if (body.code === 200 && body.url) return body.url
  throw new Error(body.msg || `HYWmusic-XG failed code=${body.code}`)
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
