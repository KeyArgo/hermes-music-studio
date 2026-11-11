// Music Studio pane: the modern studio UI, framed from its loopback host.
//
// Why a frame instead of components: the studio the owner builds and tests
// (standalone/static — rail, create, library, workspaces, compute, REVamp) is a
// plain HTML/CSS/JS app. Re-implementing it against the plugin SDK is how the
// pane drifted away from the product. The pane therefore asks the plugin route
// for the host URL and frames exactly that UI, on the same library the agent
// tool writes to.
//
// Two ways to find the host, on purpose: `GET /studio/host` (authoritative, and
// profile-aware), then the state file the launcher writes next to the library.
// The file path matters because a gateway that started before this route existed
// answers 404 for it until it restarts, while the host it already runs is fine
// and recorded on disk.
import { ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { createElement as h, useCallback, useEffect, useState } from 'react'

const PLUGIN_ID = 'hermes-music-studio'
const PATH = '/music-studio'
const STATE_REL = ['plugin-data', 'hermes-music-studio', 'studio-host.json']

const frameWrap = { width: '100%', height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }
const notice = { padding: 24, maxWidth: 640, lineHeight: 1.5 }
const detail = { opacity: 0.7, fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap' }
const bar = { display: 'flex', alignItems: 'center', gap: 12, padding: '6px 10px',
  borderTop: '1px solid rgba(127,127,127,0.25)', fontSize: 12 }
const linkButton = { background: 'transparent', border: 'none', color: 'inherit', font: 'inherit',
  fontSize: 12, cursor: 'pointer', textDecoration: 'underline', padding: 0 }

/** The host launcher's state file, read through the desktop file bridge. */
async function readHostFromDisk() {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop : null
  const rootFn = bridge && (bridge.desktopPluginsRoot || (bridge.fs && bridge.fs.desktopPluginsRoot))
  if (!bridge || typeof bridge.readFileText !== 'function' || typeof rootFn !== 'function') return null
  const root = String((await rootFn()) || '')
  const home = root.replace(/[\\/]desktop-plugins[\\/]?$/, '')
  if (!home || home === root) return null
  for (const parts of [STATE_REL, ['profiles', 'default'].concat(STATE_REL)]) {
    try {
      const file = await bridge.readFileText(`${home}/${parts.join('/')}`)
      const text = String((file && file.text) || '').trim()
      if (!text) continue
      const state = JSON.parse(text)
      const port = Number(state && state.port)
      if (Number.isInteger(port) && port > 0 && port < 65536) return `http://127.0.0.1:${port}`
    } catch {
      /* no state file here yet */
    }
  }
  return null
}

async function answering(url) {
  try {
    // Opaque on purpose: this page's origin cannot read the host's JSON, it only
    // needs to know something is listening.
    await fetch(`${url}/api/status`, { mode: 'no-cors', cache: 'no-store' })
    return true
  } catch {
    return false
  }
}

async function resolveHost(ctx) {
  let routeError = null
  try {
    const reply = await Promise.resolve(ctx.rest('/studio/host'))
    const url = reply && typeof reply.url === 'string' ? reply.url : null
    if (url) return url
    routeError = 'the plugin route returned no host url'
  } catch (err) {
    routeError = String((err && err.message) || err)
  }
  const fromDisk = await readHostFromDisk().catch(() => null)
  if (fromDisk && await answering(fromDisk)) return fromDisk
  throw new Error(fromDisk
    ? `nothing is answering at ${fromDisk} (route said: ${routeError})`
    : `route said: ${routeError}`)
}

function useStudioHost(ctx) {
  const [state, setState] = useState({ ready: false, url: null, error: null, attempt: 0 })

  useEffect(() => {
    let alive = true
    setState(prev => ({ ...prev, ready: false, error: null }))
    resolveHost(ctx)
      .then(url => { if (alive) setState(prev => ({ ...prev, ready: true, url })) })
      .catch(err => {
        if (alive) setState(prev => ({ ...prev, ready: true, error: String((err && err.message) || err) }))
      })
    return () => { alive = false }
  }, [ctx, state.attempt])

  const retry = useCallback(() => setState(prev => ({ ...prev, attempt: prev.attempt + 1 })), [])
  return { ...state, retry }
}

export function Studio({ ctx }) {
  const host = useStudioHost(ctx)

  // The framed studio says when it has closed itself; the pane then leaves the route rather
  // than framing a dead host. A blank frame that still says "Music Studio" is worse than
  // no frame at all.
  useEffect(() => {
    const onMessage = event => {
      const data = event && event.data
      if (!data || data.type !== 'hermes-music-studio:closed') return
      if (typeof ctx.navigate === 'function') ctx.navigate('/')
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [ctx])

  if (!host.ready) {
    return h('div', { style: notice }, 'Starting the studio…')
  }

  if (host.error || !host.url) {
    return h('div', { style: notice }, [
      h('p', { key: 't' }, 'The studio host is not running, so the interface cannot load.'),
      h('p', { key: 'd', style: detail }, host.error || 'no url'),
      h('p', { key: 'h', style: { opacity: 0.7 } },
        'The host starts with the plugin. If this persists, start it once with: ' +
        'python3.13 -m music_studio.host (inside the plugin folder, with HEWN_STUDIO_DATA_DIR set).'),
      h('button', { key: 'r', type: 'button', onClick: host.retry }, 'Try again')
    ])
  }

  const openExternal = () => {
    const target = `${host.url}/`
    const bridge = typeof window !== 'undefined' ? window.hermesDesktop : null
    if (bridge && typeof bridge.openExternal === 'function') bridge.openExternal(target)
    else if (typeof window !== 'undefined') window.open(target, '_blank', 'noopener')
  }

  return h('div', { style: frameWrap }, [
    h('iframe', {
      key: 'studio',
      src: `${host.url}/`,
      title: 'Music Studio',
      allow: 'autoplay; clipboard-write',
      style: { flex: 1, width: '100%', border: 'none', display: 'block', background: 'transparent' }
    }),
    h('div', { key: 'bar', style: bar }, [
      h('span', { key: 'where', style: { opacity: 0.65 } }, `${host.url} · same library as the agent tool`),
      h('button', { key: 'open', type: 'button', style: linkButton, onClick: openExternal }, 'Open in a browser ↗'),
      h('button', { key: 'reload', type: 'button', style: linkButton, onClick: host.retry }, 'Restart host')
    ])
  ])
}

export default {
  id: PLUGIN_ID,
  name: 'Music Studio',
  register(ctx) {
    ctx.register({ id: 'studio', area: ROUTES_AREA, data: { path: PATH }, render: () => h(Studio, { ctx }) })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Music Studio', codicon: 'unmute' } })
  }
}
