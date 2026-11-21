import { inject, reactive } from 'vue'

export const workspaceKey = Symbol('workspace')
export const useWorkspace = () => inject(workspaceKey)
export const positions = ['SA', 'SA Support WS', 'Commando', 'SA Team Leader']
export const shifts = ['6:30am-4:30pm', '7:00am-5:00pm', '7:30am-5:00pm', '9:00am-8:00pm']
export const eventNames = { reports: 'Sale report', pins: 'Location pin', alerts: 'Missing alert' }
export const num = n => Number(n || 0).toLocaleString()
export const dateLabel = (value, options) => new Date(value.length === 10 ? value + 'T12:00:00' : value).toLocaleDateString('en-US', options)
export const formatDMY = value => (value && value.length >= 10) ? value.slice(0, 10).split('-').reverse().join('/') : ''
export const initials = name => (name || '?').split(/\s+/u).slice(0, 2).map(w => Array.from(w)[0]).join('').toUpperCase()

export function createWorkspace() {
  const s = reactive({ data: null, metrics: null, events: [], overviewRows: [], total: 0, days: 7, fromDate: '', toDate: '', kind: 'all', page: 1,
    ready: false, preview: false, authRequired: true, signedIn: false, busy: false, loading: false, error: '', notice: '', form: null })
  let generation = 0
  let noticeTimer
  function notify(message) {
    s.notice = message
    clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => { s.notice = '' }, 4200)
  }
  async function request(path, method = 'GET', body, binary = false) {
    let res
    const file = body instanceof Blob
    try {
      res = await fetch('/api/' + path, { method, headers: { 'X-Fieldline-Request': '1',
        ...(body === undefined || file ? {} : { 'Content-Type': 'application/json' }) },
        body: file ? body : body === undefined ? undefined : JSON.stringify(body), cache: 'no-store' })
    } catch { throw new Error('The dashboard is unreachable. Check the connection and try again.') }
    if (!res.ok) {
      if (res.status === 401 && path !== 'session') {
        s.signedIn = false; s.data = null; s.metrics = null; s.events = []; s.form = null
      }
      const payload = await res.json().catch(() => ({}))
      const detail = Array.isArray(payload.detail) ? payload.detail.map(e => e.loc.slice(1).join(' ') + ' · ' + e.msg).join(', ') : payload.detail
      throw new Error(detail || 'The request could not be completed. Try again.')
    }
    if (binary) return res
    return res.status === 204 ? null : res.json()
  }
  async function refresh() {
    const run = ++generation
    s.loading = true
    try {
      const data = await request('workspace')
      if (generation !== run) return
      if (!s.metrics) s.metrics = { series: [], previous: {}, regional: [], visitors: 0, written_alerts: 0, unwritten_alerts: 0 }
      const today = (data.now || '').slice(0, 10)
      if (!s.toDate && today) s.toDate = today
      if (!s.fromDate && today) s.fromDate = today.slice(0, 8) + '01'
      if (s.fromDate && s.toDate && s.fromDate > s.toDate) s.fromDate = s.toDate
      Object.assign(s, { data, signedIn: true, error: '' })
      const rangeParams = s.fromDate && s.toDate ? { from_date: s.fromDate, to_date: s.toDate } : { days: s.days }
      const [metrics, events, missRes] = await Promise.all([
        request('analytics?' + new URLSearchParams(rangeParams)),
        request('activity?' + new URLSearchParams({ ...rangeParams, kind: s.kind, page: s.page, size: 10 })),
        s.fromDate && s.toDate
          ? request('missing-report?' + new URLSearchParams({ from_date: s.fromDate, to_date: s.toDate })).catch(() => ({ rows: [] }))
          : Promise.resolve({ rows: [] }),
      ])
      if (generation !== run) return
      Object.assign(s, { metrics, events: events.rows, total: events.total, overviewRows: missRes.rows || [] })
    } catch (e) { if (generation === run) s.error = e.message }
    finally { if (generation === run) s.loading = false }
  }
  async function start() {
    try {
      const mode = await request('mode')
      s.preview = mode.preview
      s.authRequired = mode.auth_required
      if (s.preview && s.authRequired) await request('session', 'POST', {})
      await refresh()
    } catch (e) { s.error = e.message }
    finally { s.ready = true }
  }
  async function signIn(code) {
    await request('session', 'POST', { code })
    await refresh()
  }
  async function signOut() {
    await request('session', 'DELETE')
    generation++
    Object.assign(s, { signedIn: false, data: null, metrics: null, events: [], form: null, error: '', loading: false })
  }
  async function save(path, method, body, message) {
    const result = await request(path, method, body)
    await refresh()
    notify(result?.message || message || 'Changes saved.')
  }
  async function download(kind, day, region) {
    const url = 'exports/' + kind + '?' + new URLSearchParams({ day, region })
    const res = await request(url, 'GET', undefined, true)
    const file = URL.createObjectURL(await res.blob())
    const a = document.createElement('a')
    a.href = file; a.download = res.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'Fieldline.xlsx'
    a.click(); setTimeout(() => URL.revokeObjectURL(file), 1500)
    notify('Workbook downloaded.')
  }
  return { s, request, refresh, start, signIn, signOut, save, download, notify }
}
