// Music Studio pane: the modern studio UI, framed from its loopback host.
//
// Why a frame instead of components: the studio the owner builds and tests
// (standalone/static — rail, create, library, workspaces, compute, REVamp) is a
// plain HTML/CSS/JS app. Re-implementing it against the plugin SDK is how the
// pane drifted away from the product. The pane therefore asks the plugin route
// for the host URL and frames exactly that UI, on the same library the agent
// tool writes to.
//
// The host is discovered through the plugin's own route: `GET /studio/host` is authoritative and
// profile-aware. An earlier version also read the launcher's state file through
// the desktop app's private bridge, which is not a plugin API — the route is the public seam, and a 404
// there means the gateway predates the route, not that the answer is missing.
import * as sdk from '@hermes/plugin-sdk'
import { createElement as h, useCallback, useEffect, useRef, useState } from 'react'

// A namespace import, not a named one: Desktop builds the SDK module from the names it has, so naming
// `host` would stop the whole pane loading on a Desktop that predates it. Read it as `sdk.host`.
const { ROUTES_AREA, SIDEBAR_NAV_AREA, SandboxedFrame } = sdk

const PLUGIN_ID = 'hermes-music-studio'
const PATH = '/music-studio'
// Longer than the model's 120 s and shorter than the framed page's own 150 s wait.
const DRAFT_TIMEOUT_MS = 140000

const frameWrap = { width: '100%', height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }
const notice = { padding: 24, maxWidth: 640, lineHeight: 1.5 }
const detail = { opacity: 0.7, fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap' }
const bar = { display: 'flex', alignItems: 'center', gap: 12, padding: '6px 10px',
  borderTop: '1px solid rgba(127,127,127,0.25)', fontSize: 12 }
const linkButton = { background: 'transparent', border: 'none', color: 'inherit', font: 'inherit',
  fontSize: 12, cursor: 'pointer', textDecoration: 'underline', padding: 0 }

function draftFailure(err) {
  // ctx.rest throws "403: {json}" for a 4xx body. Pull the page's {error, commands} back out
  // so the frame can show the two commands; any other failure stays the host's own sentence.
  const raw = String((err && err.message) || err)
  const start = raw.indexOf('{')
  if (start !== -1) {
    try {
      const parsed = JSON.parse(raw.slice(start))
      if (parsed && typeof parsed.error === 'string' && Array.isArray(parsed.commands)) {
        return { ok: false, error: parsed.error, commands: parsed.commands }
      }
      // A plain FastAPI refusal ("502: {"detail":"Hermes' model did not answer…"}") is a sentence
      // the person can act on. Showing the raw status line and JSON made a known timeout read as
      // "hermes.api error 500: internal server error".
      if (parsed && typeof parsed.detail === 'string' && parsed.detail.trim()) {
        return { ok: false, error: parsed.detail }
      }
    } catch { /* the host answered in prose */ }
  }
  return { ok: false, error: raw }
}

// Hermes 0.23.1+ serves plugin RPC from the process where register(ctx) ran, so the model is always
// there. The route stays for older hosts: it can be mounted before register(ctx) runs, and then it
// has no model to offer.
async function requestDraft(ctx, body) {
  const rpcHost = sdk.host
  const rest = () => ctx.rest('/studio/draft', { method: 'POST', body, timeoutMs: DRAFT_TIMEOUT_MS })
  if (!rpcHost || typeof rpcHost.request !== 'function') return rest()
  const deadline = Date.now() + DRAFT_TIMEOUT_MS
  let started
  try {
    started = await rpcHost.request('plugin.hermes-music-studio.studio.draft', body, 15000)
  } catch (err) {
    if (err?.code === -32601 || err?.error?.code === -32601 || /(?:^|\D)-32601(?:\D|$)/.test(String(err?.message || ''))) return rest()
    throw err
  }
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, Math.min(1000, Math.max(0, deadline - Date.now()))))
    const remaining = deadline - Date.now()
    if (remaining <= 0) break
    const result = await rpcHost.request('plugin.hermes-music-studio.studio.draft_result',
      { job: started.job }, Math.min(15000, remaining))
    if (result.state === 'done') return result
    if (result.state === 'failed') {
      const failure = { ok: false, error: result.error }
      if (Array.isArray(result.commands)) failure.commands = result.commands
      return failure
    }
    if (result.state !== 'running') throw new Error('The draft job is no longer available.')
  }
  throw new Error('The draft timed out. Try again.')
}

async function answering(url) {
  try {
    // Opaque on purpose: this page's origin cannot read the host's JSON, it only
    // needs to know something is listening.
    await fetch(`${url}/api/health`, { mode: 'no-cors', cache: 'no-store' })
    return true
  } catch {
    return false
  }
}

async function resolveHost(ctx) {
  let url = null
  let hostReply = null
  let routeError = null
  try {
    const reply = await Promise.resolve(ctx.rest('/studio/host'))
    hostReply = reply
    url = reply && typeof reply.url === 'string' ? reply.url : null
    if (!url) routeError = 'the plugin route returned no host url'
  } catch (err) {
    routeError = String((err && err.message) || err)
  }
  if (url && await answering(url)) return { url, token: String((hostReply && hostReply.token) || '') }
  throw new Error(url ? `nothing is answering at ${url}` : `route said: ${routeError}`)
}

function useStudioHost(ctx) {
  const [state, setState] = useState({ ready: false, url: null, token: '', error: null, attempt: 0 })

  useEffect(() => {
    let alive = true
    setState(prev => ({ ...prev, ready: false, error: null }))
    resolveHost(ctx)
      .then(reply => { if (alive) setState(prev => ({ ...prev, ready: true, url: reply.url, token: reply.token })) })
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

  // `ctx.os.openExternal` is the public seam; the desktop bridge is the app's own business, not a
  // plugin's (review item 2). A framed page cannot reach it either — it asks over postMessage.
  const openExternal = useCallback(raw => {
    const base = host.token ? `${host.url || ''}/?token=${encodeURIComponent(host.token)}` : `${host.url || ''}/`
    const candidate = typeof raw === 'string' && raw ? raw : base
    // Only a web link goes to the OS. This handler is reachable over postMessage, so a page that
    // is not ours must not be able to hand the desktop `file:`, `javascript:` or a custom scheme.
    let parsed = null
    try { parsed = new URL(String(candidate)) } catch { parsed = null }
    if (!parsed || !/^https?:$/.test(parsed.protocol)) {
      if (typeof ctx?.notify === 'function') {
        ctx.notify({ kind: 'error', message: `Refusing to open ${candidate}: only http(s) links` })
      }
      return
    }
    const target = parsed.href
    if (ctx && ctx.os && typeof ctx.os.openExternal === 'function') {
      void ctx.os.openExternal(target)
      return
    }
    if (typeof ctx?.notify === 'function') {
      ctx.notify({ kind: 'error', message: `This Hermes build cannot open a browser tab. Open ${target}` })
    }
  }, [ctx, host.url, host.token])

  // The framed studio says when it has closed itself, and asks when it needs a browser tab opened;
  // the pane then leaves the route rather than framing a dead host. A blank frame that still says
  // "Music Studio" is worse than no frame at all.
  const frameHost = useRef(null)

  useEffect(() => {
    const onMessage = event => {
      // The studio is the only window allowed to talk: anything else on the page, or another
      // frame, could otherwise ask this pane to navigate or open a URL.
      const frame = frameHost.current && frameHost.current.querySelector
        ? frameHost.current.querySelector('iframe')
        : null
      if (!frame || !frame.contentWindow || event.source !== frame.contentWindow) return
      const data = event && event.data
      if (!data || typeof data.type !== 'string') return
      if (data.type === 'hermes-music-studio:open-external') {
        openExternal(data.url)
        return
      }
      if (data.type === 'hermes-music-studio:draft') {
        // The frame has no model and no plugin route of its own; this process has both.
        const answer = payload => {
          try {
            frame.contentWindow.postMessage(
              { type: 'hermes-music-studio:draft-result', id: data.id, ...payload }, '*')
          } catch { /* the frame went away while drafting */ }
        }
        // The frame's action is the difference between a chat reply and a filled form:
        // 'chat' for "Discuss idea", 'draft' for "Fill my draft". Anything else is a draft.
        const body = { prompt: data.prompt, tone: data.tone, style: data.style,
          action: data.action === 'chat' ? 'chat' : 'draft' }
        if (data.provider) body.provider = data.provider
        if (data.model) body.model = data.model
        Promise.resolve().then(() => requestDraft(ctx, body))
          .then(reply => answer(reply && reply.ok === false ? reply
            : { ok: true, draft: reply && reply.draft, content: reply && reply.content }))
          .catch(err => answer(draftFailure(err)))
        return
      }
      if (data.type === 'hermes-music-studio:models') {
        // Hermes' provider list lives in this process, not in the loopback host the frame talks to.
        const reply = payload => {
          try {
            frame.contentWindow.postMessage(
              { type: 'hermes-music-studio:models-result', id: data.id, ...payload }, '*')
          } catch { /* the frame went away */ }
        }
        Promise.resolve(ctx.rest('/studio/models'))
          .then(choices => reply({ ok: true, choices }))
          .catch(err => reply({ ok: false, error: String((err && err.message) || err) }))
        return
      }
      if (data.type !== 'hermes-music-studio:closed') return
      if (typeof ctx.navigate === 'function') ctx.navigate('/')
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [ctx, openExternal])

  if (!host.ready) {
    return h('div', { style: notice }, 'Starting the studio…')
  }

  if (host.error || !host.url) {
    return h('div', { style: notice }, [
      h('p', { key: 't' }, 'The studio host is not running, so the interface cannot load.'),
      h('p', { key: 'd', style: detail }, host.error || 'no url'),
      h('p', { key: 'h', style: { opacity: 0.7 } },
        'The host starts with the plugin. If this persists, do what its message above says ' +
        '(it names the exact install command), then try again.'),
      h('button', { key: 'r', type: 'button', onClick: host.retry }, 'Try again')
    ])
  }

  return h('div', { style: frameWrap, ref: frameHost }, [
    // The SDK's sandboxed frame, not a raw iframe: guest content gets the app's own posture (scripts
    // run, the origin is opaque, no reach into the app or its bridge).
    h(SandboxedFrame, {
      key: 'studio',
      // The token travels in the query string so the framed page can send it back; the host requires it
      // on every /api route (review item 3).
      src: host.token ? `${host.url}/?token=${encodeURIComponent(host.token)}` : `${host.url}/`,
      title: 'Music Studio',
      sandbox: 'allow-scripts allow-forms allow-downloads',
      style: { flex: 1, width: '100%', border: 'none', display: 'block', background: 'transparent' }
    }),
    h('div', { key: 'bar', style: bar }, [
      h('span', { key: 'where', style: { opacity: 0.65 } }, `${host.url} · same library as the agent tool`),
      h('button', { key: 'open', type: 'button', style: linkButton, onClick: () => openExternal() }, 'Open in a browser ↗'),
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
