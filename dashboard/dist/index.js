// Hermes Music Studio — Console studio dashboard tab.
// Plain IIFE, no build step. Backend: /api/plugins/hermes-music-studio/*
// (see dashboard/plugin_api.py). Everything rendered here is either a field
// the API returned or a client-only view of it; the UI never invents a take,
// a percentage or a section time.
(function () {
  'use strict'

  var SDK = window.__HERMES_PLUGIN_SDK__
  var PLUGIN = 'hermes-music-studio'
  var API = '/api/plugins/' + PLUGIN
  var TITLE = 'Music Studio'
  var FIELDS = ['title', 'idea', 'caption', 'lyrics', 'duration_seconds', 'instrumental', 'vocal_language', 'bpm', 'keyscale']
  var LANGUAGES = [['', 'Auto'], ['en', 'English'], ['es', 'Spanish'], ['fr', 'French'], ['de', 'German'], ['it', 'Italian'], ['pt', 'Portuguese'], ['ja', 'Japanese'], ['ko', 'Korean'], ['zh', 'Chinese']]
  var DEFAULT_LANG = 'en'
  var POLL_ACTIVE_MS = 2000
  var POLL_IDLE_MS = 10000
  var STUCK_MS = 3 * 60 * 1000
  var BUCKETS = 512
  var SNAP_S = 0.5

  if (!SDK || !SDK.React || !window.__HERMES_PLUGINS__) {
    console.error('[' + PLUGIN + '] dashboard SDK unavailable; tab not registered')
    return
  }

  var React = SDK.React
  var h = React.createElement
  var useState = SDK.hooks.useState
  var useEffect = SDK.hooks.useEffect
  var useRef = SDK.hooks.useRef
  var useCallback = SDK.hooks.useCallback
  var useMemo = SDK.hooks.useMemo
  var fetchJSON = SDK.fetchJSON

  // ---------------------------------------------------------------- styles
  var CSS = [
    '.console{--bg:#0A0C11;--panel:#0E1218;--panel-2:#11161E;--raised:#161D27;--raised-2:#1D2636;--line:#24303F;--line-2:#33445A;--ink:#EEF2F7;--ink-2:#8FA0B6;--ink-3:#5D6C81;--acc:#0053FD;--acc-ink:#FFFFFF;--acc-dim:rgba(0,83,253,.16);--range:#E9B178;--range-dim:rgba(233,177,120,.14);--fail:#D98D7D;--ok:#9FB8A0;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}',
    '.console{position:relative;display:grid;grid-template-rows:42px 54px minmax(0,1fr) 26px;height:calc(100vh - 96px);min-height:620px;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:10px;overflow:hidden;font:13px/1.45 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}',
    '.console *{box-sizing:border-box}',
    '.console button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer}',
    '.console button:disabled{cursor:not-allowed;opacity:.45}',
    '.console :focus-visible{outline:2px solid var(--acc);outline-offset:1px}',
    '.c-micro{font-size:10px;line-height:1.2;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:var(--ink-3)}',
    '.c-mono{font-family:var(--mono);font-size:12px;font-variant-numeric:tabular-nums}',
    '.c-btn{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 9px;border:1px solid var(--line-2);border-radius:5px;background:var(--raised);color:var(--ink-2);font-size:12px;white-space:nowrap}',
    '.c-btn:hover:not(:disabled){background:var(--raised-2);color:var(--ink)}',
    '.c-btn.primary{background:var(--acc);border-color:var(--acc);color:var(--acc-ink);font-weight:600}',
    '.c-btn.primary:hover:not(:disabled){filter:brightness(1.12)}',
    '.c-btn.ghost{background:transparent;border-color:transparent;color:var(--ink-2)}',
    '.c-btn.ghost:hover:not(:disabled){background:var(--raised);color:var(--ink)}',
    '.c-btn.sm{height:22px;padding:0 7px;font-size:11px}',
    '.titlebar{display:flex;align-items:center;gap:14px;padding:0 12px;border-bottom:1px solid var(--line);background:var(--panel)}',
    '.wordmark{display:flex;align-items:center;gap:8px;font-weight:650;font-size:13px;letter-spacing:.01em}',
    '.wordmark i{width:8px;height:8px;border-radius:50%;background:var(--acc);box-shadow:0 0 0 3px var(--acc-dim)}',
    '.crumbs{display:flex;align-items:baseline;gap:8px;min-width:0;overflow:hidden}',
    '.crumbs b{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.crumbs span{font-size:11px;color:var(--ink-3);white-space:nowrap}',
    '.mode{display:flex;margin-left:auto;border:1px solid var(--line);border-radius:5px;overflow:hidden}',
    '.mode button{height:24px;padding:0 8px;font-size:11px;color:var(--ink-3)}',
    '.mode button.on{background:var(--acc-dim);color:var(--ink)}',
    '.tb-right{display:flex;align-items:center;gap:6px}',
    '.transport{display:flex;align-items:center;gap:10px;padding:0 12px;border-bottom:1px solid var(--line);background:var(--panel-2)}',
    '.play{width:30px;height:30px;border-radius:5px;border:1px solid var(--line-2);background:var(--raised);font-size:12px}',
    '.tc{display:flex;align-items:baseline;gap:6px;font-family:var(--mono);font-size:13px}',
    '.tc .now{font-size:15px}',
    '.tc .of{color:var(--ink-3)}',
    '.tmeta{font-size:11px;color:var(--ink-3);display:flex;gap:8px}',
    '.tmeta b{color:var(--ink-2);font-weight:600}',
    '.grow{flex:1 1 auto;min-width:8px}',
    '.eng{display:flex;flex-direction:column;gap:1px;max-width:250px}',
    '.eng select{height:24px;max-width:250px;border:1px solid var(--line-2);border-radius:4px;background:var(--raised);color:var(--ink-2);font-size:11px;padding:0 4px}',
    '.body{display:grid;grid-template-columns:236px minmax(0,1fr) 320px;min-height:0;overflow:hidden}',
    '.rail{border-right:1px solid var(--line);background:var(--panel);overflow:auto;padding:10px 8px;display:flex;flex-direction:column;gap:14px}',
    '.rail header{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:5px;padding:0 2px}',
    '.rail header .n{font-family:var(--mono);font-size:10px;color:var(--ink-3)}',
    '.proj,.railsec,.railtake{display:flex;align-items:center;gap:7px;width:100%;text-align:left;padding:5px 6px;border-radius:5px;color:var(--ink-2)}',
    '.proj b{flex:1 1 auto;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}',
    '.proj .n{font-family:var(--mono);font-size:10px;color:var(--ink-3)}',
    '.proj[aria-pressed="true"]{background:var(--acc-dim);color:var(--ink);box-shadow:inset 2px 0 0 var(--acc)}',
    '.proj:hover,.railsec:hover,.railtake:hover{background:var(--raised)}',
    '.railsec{font-size:11px;padding:4px 6px}',
    '.railsec .t{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.railsec[aria-pressed="true"]{background:var(--acc-dim);color:var(--ink)}',
    '.railtake{font-family:var(--mono);font-size:10px;padding:4px 6px}',
    '.railtake .id{color:var(--ink-2)}',
    '.railtake[aria-pressed="true"]{background:var(--raised-2)}',
    '.railfoot{font-size:10px;line-height:1.5;color:var(--ink-3);margin-top:auto;padding:6px 2px}',
    '.cstage{display:flex;flex-direction:column;min-width:0;min-height:0;padding:10px 12px 6px;gap:8px;background:var(--panel-2);overflow:hidden}',
    '.chead{display:flex;align-items:flex-end;gap:12px;min-width:0}',
    '.chead h1{margin:1px 0 0;font-size:18px;line-height:1.2;letter-spacing:-.02em;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.chead .sub{font-size:11px;color:var(--ink-3);padding-bottom:3px;white-space:nowrap}',
    '.pills{display:flex;gap:5px;margin-left:auto;flex-wrap:wrap;justify-content:flex-end}',
    '.pill{height:20px;display:inline-flex;align-items:center;padding:0 7px;border:1px solid var(--line);border-radius:3px;font-family:var(--mono);font-size:10px;color:var(--ink-2);background:var(--raised)}',
    '.timeline{display:flex;flex-direction:column;min-height:0;flex:1 1 auto}',
    '.ruler{position:relative;height:22px;flex:0 0 22px;border-bottom:1px solid var(--line)}',
    '.ruler span{position:absolute;top:3px;transform:translateX(-50%);font-family:var(--mono);font-size:9px;color:var(--ink-3)}',
    '.ruler i{position:absolute;bottom:0;width:1px;height:4px;background:var(--line-2)}',
    '.band{position:relative;height:58px;flex:0 0 58px;margin-top:3px}',
    '.seccard{position:absolute;top:0;bottom:0;overflow:hidden;border:1px solid var(--line);border-radius:6px;background:var(--raised);padding:5px 7px;text-align:left;display:flex;flex-direction:column;gap:2px;min-width:0}',
    '.seccard:hover{background:var(--raised-2)}',
    '.seccard[aria-pressed="true"]{background:var(--acc-dim);border-color:var(--line-2);box-shadow:inset 0 2px 0 var(--acc)}',
    '.seccard .tag{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.seccard[aria-pressed="true"] .tag{color:var(--ink-2)}',
    '.seccard .range{font-family:var(--mono);font-size:9px;color:var(--ink-3);white-space:nowrap}',
    '.seccard .cnt{font-family:var(--mono);font-size:9px;color:var(--ink-3)}',
    '.seccard .tap{position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--line-2)}',
    '.seccard.chorus .tap{background:var(--acc)}',
    '.wavebox{position:relative;flex:1 1 auto;min-height:120px;max-height:260px;border:1px solid var(--line);border-radius:6px;background:var(--panel);margin-top:4px;overflow:hidden;cursor:crosshair}',
    '.wavebox canvas{position:absolute;inset:0;width:100%;height:100%;display:block}',
    '.wavebox .rg{position:absolute;top:0;bottom:0;background:var(--range-dim);border-left:1px solid var(--range);border-right:1px solid var(--range);pointer-events:none}',
    '.wavebox .rg .lab{position:absolute;top:3px;left:4px;font-family:var(--mono);font-size:9px;color:var(--range);white-space:nowrap}',
    '.wavebox .ph{position:absolute;top:0;bottom:0;width:1px;background:var(--acc);pointer-events:none}',
    '.wavebox .ph:after{content:"";position:absolute;top:0;left:-3px;width:7px;height:5px;background:var(--acc);border-radius:0 0 3px 3px}',
    '.wavebox .noaudio{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:10px;letter-spacing:.12em;color:var(--ink-3);text-transform:uppercase;background:repeating-linear-gradient(135deg,transparent,transparent 6px,rgba(255,255,255,.025) 6px,rgba(255,255,255,.025) 8px)}',
    '.bigfoot{display:flex;align-items:center;gap:14px;height:24px;flex:0 0 24px;font-size:10px;color:var(--ink-3)}',
    '.bigfoot b{color:var(--ink-2);font-family:var(--mono);font-size:10px;font-weight:500}',
    '.bigfoot .r{margin-left:auto;display:flex;gap:12px;font-family:var(--mono)}',
    '.ledger{flex:0 0 auto;border-top:1px solid var(--line);padding-top:2px}',
    '.ledger .lh,.lrow{display:grid;grid-template-columns:1.3fr .8fr .8fr .55fr 1fr;gap:6px;align-items:center;font-size:10px;padding:0 4px;height:22px}',
    '.ledger .lh{color:var(--ink-3);text-transform:uppercase;letter-spacing:.1em;font-weight:700}',
    '.lrow{width:100%;text-align:left;border-top:1px solid rgba(36,48,63,.6);border-radius:0;color:var(--ink-2)}',
    '.lrow:hover{background:var(--raised)}',
    '.lrow[aria-pressed="true"]{background:var(--acc-dim);color:var(--ink)}',
    '.lrow span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.lrow .num{font-family:var(--mono);font-size:10px;color:var(--ink-3)}',
    '.strip{display:flex;gap:6px;overflow-x:auto;padding:2px 0 4px;flex:0 0 auto}',
    '.tile{position:relative;min-width:146px;max-width:190px;display:flex;flex-direction:column;gap:2px;text-align:left;padding:6px 7px 6px;border:1px solid var(--line);border-radius:6px;background:var(--raised)}',
    '.tile:hover{background:var(--raised-2)}',
    '.tile[data-active="1"]{border-color:var(--line-2);box-shadow:inset 0 2px 0 var(--acc)}',
    '.tile .top{display:flex;align-items:center;gap:5px}',
    '.tile .tid{font-family:var(--mono);font-size:11px;font-weight:600}',
    '.tile .dot{width:6px;height:6px;border-radius:50%;background:var(--ink-3)}',
    '.tile .dot.ok{background:var(--ok)}.tile .dot.bad{background:var(--fail)}.tile .dot.live{background:var(--acc)}',
    '.tile .len{margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--ink-2)}',
    '.tile .meta{font-family:var(--mono);font-size:9px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.tile .ab{display:flex;gap:3px;margin-top:1px}',
    '.tile .ab button{width:18px;height:16px;border:1px solid var(--line);border-radius:3px;font-size:9px;font-family:var(--mono);color:var(--ink-3)}',
    '.tile .ab button[aria-pressed="true"]{background:var(--acc-dim);border-color:var(--acc);color:var(--ink)}',
    '.tile .acts{display:flex;gap:4px;align-items:center;margin-top:1px}',
    '.tile a{color:var(--ink-3);font-size:10px;text-decoration:none}',
    '.tile a:hover{color:var(--ink)}',
    '.tile .ms-chip{font-size:9px;padding:0 4px;height:15px}',
    '.lang{font-family:var(--mono);font-size:10px;color:var(--range)}',
    '.track{height:2px;background:var(--line);border-radius:2px;overflow:hidden;margin-top:2px}',
    '.track i{display:block;height:100%;background:var(--acc);transition:none}',
    '.insp{border-left:1px solid var(--line);background:var(--panel);display:flex;flex-direction:column;min-height:0;overflow:hidden}',
    '.itabs{display:flex;height:34px;flex:0 0 34px;border-bottom:1px solid var(--line)}',
    '.itabs button{flex:1;font-size:11px;color:var(--ink-3);border-bottom:2px solid transparent}',
    '.itabs button.on{color:var(--ink);border-bottom-color:var(--acc)}',
    '.ibody{flex:1 1 auto;overflow:auto;padding:10px 12px;display:flex;flex-direction:column;gap:10px}',
    '.ifoot{flex:0 0 auto;border-top:1px solid var(--line);padding:8px 12px;display:flex;align-items:center;gap:8px;font-size:10px;color:var(--ink-3)}',
    '.thead{display:flex;align-items:baseline;gap:6px}',
    '.thead b{font-family:var(--mono);font-size:12px}',
    '.thead span{font-size:10px;color:var(--ink-3)}',
    '.seedrow{display:flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px;color:var(--ink-2)}',
    '.seedrow .latch{height:20px;padding:0 6px;border:1px solid var(--line-2);border-radius:3px;font-size:9px;color:var(--ink-3);font-family:inherit}',
    '.facts{display:grid;grid-template-columns:1fr 1fr;gap:4px 10px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:7px 0}',
    '.fact{display:flex;justify-content:space-between;gap:8px;font-size:10px;color:var(--ink-3)}',
    '.fact b{font-family:var(--mono);font-size:10px;color:var(--ink-2);font-weight:500}',
    '.c-field{display:flex;flex-direction:column;gap:3px}',
    '.c-field label{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}',
    '.c-field input,.c-field textarea,.c-field select{width:100%;border:1px solid var(--line-2);border-radius:4px;background:var(--bg);color:var(--ink);padding:5px 6px;font:inherit;font-size:12px}',
    '.c-field textarea{resize:vertical;line-height:1.5}',
    '.c-field textarea.words{min-height:96px}',
    '.c-field input:focus,.c-field textarea:focus,.c-field select:focus{outline:1px solid var(--acc);outline-offset:-1px}',
    '.adv{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);padding-top:8px}',
    '.check{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--ink-2)}',
    '.check input{width:auto}',
    '.two{display:grid;grid-template-columns:1fr 1fr;gap:8px}',
    '.mut{font-size:10px;color:var(--ink-3);line-height:1.45;margin:0}',
    '.ms-chip{display:inline-flex;align-items:center;height:18px;padding:0 5px;border:1px solid var(--line);border-radius:3px;background:var(--raised);font-size:9px;color:var(--ink-3);white-space:nowrap}',
    '.ms-chip.ok{border-color:rgba(159,184,160,.4);color:var(--ok)}',
    '.ms-chip.bad{border-color:rgba(217,141,125,.45);color:var(--fail)}',
    '.ms-chip.warn{color:var(--ink-2)}',
    '.ms-error{border:1px solid var(--fail);border-radius:5px;background:rgba(217,141,125,.09);color:var(--fail);padding:6px 8px;font-size:11px;white-space:pre-wrap;word-break:break-word}',
    '.ms-notice{border:1px solid var(--line);border-radius:5px;background:var(--raised);padding:6px 8px;font-size:11px;color:var(--ink-2)}',
    '.ms-live{margin:0;font-size:10px;color:var(--ink-3);min-height:0}',
    '.ms-audio{width:100%;height:30px}',
    '.sec-edit{border:1px solid var(--line-2);border-radius:6px;background:var(--panel);padding:7px 9px;display:flex;flex-direction:column;gap:5px;flex:0 0 auto}',
    '.sec-edit .head{display:flex;align-items:center;gap:8px}',
    '.sec-edit .head b{font-family:var(--mono);font-size:10px;color:var(--ink-2)}',
    '.sec-edit .head .d{margin-left:auto;font-family:var(--mono);font-size:9px;color:var(--range)}',
    '.sec-edit textarea{width:100%;min-height:52px;max-height:110px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--ink);font:12px/1.5 inherit;padding:5px 6px;resize:vertical}',
    '.cmpwrap{border:1px solid var(--line);border-radius:6px;padding:7px;display:flex;flex-direction:column;gap:6px}',
    '.cmpcol{display:grid;grid-template-columns:1fr 1fr;gap:8px}',
    '.cmpitem{border:1px solid var(--line);border-radius:5px;padding:6px;display:flex;flex-direction:column;gap:3px}',
    '.cmpitem b{font-size:11px}',
    '.cmpitem .ms-mono{font-family:var(--mono);font-size:9px;color:var(--ink-3)}',
    '.stuck{border:1px solid var(--range);border-radius:5px;padding:6px 8px;font-size:10px;color:var(--ink-2);display:flex;flex-direction:column;gap:5px}',
    '.actionbar{position:absolute;left:250px;right:336px;bottom:52px;display:flex;align-items:center;gap:10px;padding:7px 10px;border:1px solid var(--line-2);border-radius:10px;background:var(--panel);box-shadow:0 10px 30px rgba(0,0,0,.45)}',
    '.actionbar .verb{font-size:9px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:var(--range)}',
    '.actionbar .ro{font-family:var(--mono);font-size:11px;color:var(--ink)}',
    '.actionbar .seed{font-family:var(--mono);font-size:10px;color:var(--ink-3)}',
    '.req{position:absolute;left:250px;right:336px;bottom:28px;font-family:var(--mono);font-size:9px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 4px}',
    '.statusbar{display:flex;align-items:center;gap:12px;padding:0 10px;border-top:1px solid var(--line);background:var(--panel);font-size:10px;color:var(--ink-3)}',
    '.statusbar .r{margin-left:auto;display:flex;gap:12px;font-family:var(--mono)}',
    '.statusbar .warn{color:var(--range)}',
    '.ms-dialog{border:1px solid var(--fail);border-radius:5px;padding:8px;display:flex;flex-direction:column;gap:6px;font-size:11px}',
    '.ms-empty{border:1px dashed var(--line-2);border-radius:6px;padding:12px;text-align:center;color:var(--ink-3);font-size:11px}',
    '.strip-empty{flex:0 0 auto}',
    '@media (max-width:1279px){.body{grid-template-columns:200px minmax(0,1fr) 296px}.actionbar,.req{left:214px;right:312px}}',
    '@media (max-width:1099px){.body{grid-template-columns:44px minmax(0,1fr) 280px}.rail{padding:8px 4px}.rail .grp{display:none}.actionbar,.req{left:58px;right:296px}}',
    '@media (max-width:980px){.body{grid-template-columns:minmax(0,1fr) 320px}.rail{display:none}.ledger{display:none}.actionbar,.req{left:12px;right:336px}}',
    '@media (max-width:720px){.body{grid-template-columns:minmax(0,1fr)}.insp{display:none}.actionbar,.req{left:12px;right:12px}.strip{flex-wrap:wrap}}'
  ].join('')

  function ensureStyles() {
    if (document.getElementById('ms-styles')) return
    var el = document.createElement('style')
    el.id = 'ms-styles'
    el.textContent = CSS
    document.head.appendChild(el)
  }

  // --------------------------------------------------------------- helpers
  function errText(err) {
    if (!err) return 'Unknown error'
    var detail = err.detail || (err.data && err.data.detail) || err.message || String(err)
    var status = err.status || err.statusCode
    return status ? status + ': ' + detail : detail
  }

  function statusOf(err) {
    var direct = Number(err && (err.status || err.statusCode))
    if (isFinite(direct) && direct >= 400) return direct
    var found = /(?:^|\D)(409|413|503)(?:\D|$)/.exec(errText(err))
    return found ? Number(found[1]) : null
  }

  function explain(err, action) {
    var status = statusOf(err)
    var why = status === 409 ? 'the project changed on the server first (409 conflict)'
      : status === 413 ? 'the request was too large (413)'
        : status === 503 ? 'the generation engine is unavailable right now (503)'
          : null
    if (!why) return errText(err)
    return 'Nothing happened: ' + why + ', so ' + (action || 'nothing was applied') + '. ' + errText(err)
  }

  function isStale(err) {
    return /stale draft/i.test(errText(err)) || ((err.status || err.statusCode) === 409 && /stale/i.test(errText(err)))
  }

  function newSeed() {
    var buf = new Uint32Array(1)
    if (window.crypto && window.crypto.getRandomValues) window.crypto.getRandomValues(buf)
    else buf[0] = Math.floor(Math.random() * 4294967296)
    return buf[0] >>> 0
  }

  function asSeed(value) {
    return /^\d+$/.test(String(value == null ? '' : value).trim()) ? Math.min(4294967295, Number(String(value).trim())) : null
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n }

  // Transport clock: mm:ss.d, always padded, exactly as the design specifies.
  function clock(seconds) {
    if (seconds == null || !isFinite(seconds)) return '--:--.-'
    var s = Math.max(0, Number(seconds))
    var m = Math.floor(s / 60)
    var rest = s - m * 60
    return pad2(m) + ':' + (rest < 10 ? '0' : '') + rest.toFixed(1)
  }

  // Short duration for labels and the honest length line: m:ss, no padding.
  function short(seconds) {
    if (seconds == null || !isFinite(seconds)) return '—'
    var s = Math.max(0, Number(seconds))
    var m = Math.floor(s / 60)
    return m + ':' + pad2(Math.round(s - m * 60))
  }

  function draftOf(p) {
    var d = {}
    for (var i = 0; i < FIELDS.length; i++) {
      var k = FIELDS[i]
      d[k] = p && p[k] != null ? p[k] : ''
    }
    d.title = (p && p.title) || ''
    d.idea = (p && p.idea) || ''
    d.caption = (p && p.caption) || ''
    d.lyrics = (p && p.lyrics) || ''
    d.duration_seconds = p && typeof p.duration_seconds === 'number' ? p.duration_seconds : 30
    d.instrumental = !!(p && p.instrumental)
    d.vocal_language = (p && p.vocal_language) || DEFAULT_LANG
    d.bpm = p && typeof p.bpm === 'number' && isFinite(p.bpm) ? p.bpm : null
    d.keyscale = (p && p.keyscale) || null
    return d
  }

  function takes(p) { return (p && Array.isArray(p.takes)) ? p.takes : [] }
  function isActive(t) { return !!t && (t.state === 'queued' || t.state === 'running') }
  function activeTake(p) {
    var t = takes(p)
    for (var i = 0; i < t.length; i++) if (isActive(t[i])) return t[i]
    return null
  }
  function anyActive(list) {
    for (var i = 0; i < (list || []).length; i++) if (activeTake(list[i])) return true
    return false
  }
  function succeeded(p) {
    var out = [], t = takes(p)
    for (var i = 0; i < t.length; i++) if (t[i].state === 'succeeded') out.push(t[i])
    return out
  }
  function queued(p) {
    var out = [], t = takes(p)
    for (var i = 0; i < t.length; i++) if (t[i].state === 'queued') out.push(t[i])
    return out
  }

  function stateLabel(take) {
    if (!take) return ''
    if (take.state === 'queued') return 'Queued'
    if (take.state === 'running') {
      var p = take.progress
      if (typeof p === 'number' && isFinite(p) && p >= 0) {
        var pct = Math.floor(p)
        var eta = take.eta_seconds
        var suffix = typeof eta === 'number' && isFinite(eta) && eta >= 0 ? ' ~' + Math.round(eta) + ' s left' : ''
        return 'Rendering ' + pct + '%' + suffix
      }
      return 'Rendering'
    }
    if (take.state === 'succeeded') return 'Complete'
    if (take.state === 'failed') return 'Failed'
    return String(take.state)
  }

  function tsOf(value) {
    if (value == null || value === '') return null
    if (typeof value === 'number' && isFinite(value)) return value > 1e12 ? value : value * 1000
    var parsed = Date.parse(String(value))
    return isFinite(parsed) ? parsed : null
  }

  // Per live take: when it last reported a NEW progress milestone and the
  // backend's own ETA then. Also clamps the displayed ETA so it never rises
  // between polls (the backend recomputes it from elapsed time).
  function observeTakes(list, seen, now) {
    for (var i = 0; i < (list || []).length; i++) {
      var t = takes(list[i])
      for (var j = 0; j < t.length; j++) {
        var take = t[j]
        if (!isActive(take)) { delete seen[take.take_id]; continue }
        var progress = take.progress == null ? null : take.progress
        var raw = typeof take.eta_seconds === 'number' && isFinite(take.eta_seconds) && take.eta_seconds >= 0 ? take.eta_seconds : null
        var rec = seen[take.take_id]
        if (!rec) { rec = { progress: progress, at: now, eta: raw, floor: raw, progressFloor: progress }; seen[take.take_id] = rec }
        else if (rec.progress !== progress) { rec.progress = progress; rec.at = now; rec.eta = raw }
        // Progress is polled, not performed: a coarser poll may report a lower
        // number for the same render, but the visible bar never goes backwards.
        if (typeof progress === 'number' && isFinite(progress)) {
          if (rec.progressFloor == null || progress > rec.progressFloor) rec.progressFloor = progress
          else take.progress = rec.progressFloor
        }
        if (raw == null) continue
        if (rec.floor == null || raw < rec.floor) rec.floor = raw
        take.eta_seconds = rec.floor
      }
    }
  }

  function stuckTake(project, now, seen) {
    var list = takes(project)
    for (var i = 0; i < list.length; i++) {
      var t = list[i]
      if (!isActive(t)) continue
      var rec = seen[t.take_id]
      if (rec && rec.eta != null) {
        if (now > rec.at + rec.eta * 1000) return t
        continue
      }
      var started = tsOf(t.started_at)
      if (started != null && now - started > STUCK_MS) return t
    }
    return null
  }

  function snapshotLines(take) {
    var s = (take && take.snapshot) || take || {}
    var lines = []
    if (s.caption) lines.push(s.caption)
    if (s.duration_seconds != null) lines.push(s.duration_seconds + ' s')
    if (s.instrumental) lines.push('instrumental')
    else if (s.vocal_language) lines.push('vocals: ' + s.vocal_language)
    if (s.bpm) lines.push(s.bpm + ' BPM')
    if (s.keyscale) lines.push(s.keyscale)
    return lines
  }

  function Badge(props) {
    return h('span', { className: 'ms-chip ' + (props.tone || '') }, props.children)
  }

  function takeUrl(projectId, takeId, suffix) {
    return API + '/projects/' + encodeURIComponent(projectId) + '/takes/' + encodeURIComponent(takeId) + suffix
  }

  // A take with a named, reversible QC edit is played from that edit; the raw
  // render is only served when the server reports no playable edit.
  function editUrl(url, editId) {
    if (editId == null || editId === '') return url
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'edit=' + encodeURIComponent(editId)
  }

  function takeSeed(take) {
    if (!take) return null
    return take.requested_seed != null ? take.requested_seed : take.seed
  }

  function takeDuration(take) {
    if (!take) return null
    if (typeof take.duration === 'number') return take.duration
    if (take.qc && typeof take.qc.file_s === 'number') return take.qc.file_s
    var s = take.snapshot || {}
    return typeof s.duration_seconds === 'number' ? s.duration_seconds : null
  }

  // "rendered 1:45 of 2:00" whenever the music stops short of the request.
  // Uses the QC measurements the worker actually recorded; when there is no
  // QC block, it says nothing rather than guessing.
  function honestLength(take) {
    if (!take || !take.qc) return null
    var qc = take.qc
    var requested = typeof qc.requested_s === 'number' ? qc.requested_s
      : (typeof take.requested_s === 'number' ? take.requested_s : null)
    var rendered = typeof qc.musical_end_s === 'number' ? qc.musical_end_s : null
    if (requested == null || rendered == null || rendered >= requested - 0.05) return null
    return 'rendered ' + short(rendered) + ' of ' + short(requested)
  }

  // The sections model is stored on the draft. Older payloads only have
  // lyrics, so the projection is derived the same way the backend derives it:
  // contiguous, evenly divided across [Tag] headers, no invented times.
  var HEADER = /^\s*\[([^\]]{1,40})\]\s*$/
  var TAGS = { intro: 'intro', verse: 'verse', prechorus: 'prechorus', 'pre-chorus': 'prechorus', 'pre chorus': 'prechorus', chorus: 'chorus', bridge: 'bridge', outro: 'outro', instrumental: 'instrumental', inst: 'instrumental', custom: 'custom' }
  function tagOf(header) {
    var text = String(header).trim()
    var lowered = text.toLowerCase()
    if (TAGS[lowered]) return { tag: TAGS[lowered], label: text }
    var words = lowered.replace('(', ' ').replace(')', ' ').split(' ')
    var key = String(words[0] || '').replace(/[^a-z]/g, '')
    return { tag: TAGS[key] || 'custom', label: text }
  }
  function deriveSections(lyrics, duration) {
    if (typeof lyrics !== 'string' || typeof duration !== 'number' || duration <= 0) return []
    var groups = [], current = null
    var rows = lyrics.split(/\r?\n/)
    for (var i = 0; i < rows.length; i++) {
      var match = HEADER.exec(rows[i])
      if (match) {
        var meta = tagOf(match[1])
        current = { tag: meta.tag, label: meta.label, lyric_lines: [] }
        groups.push(current)
      } else if (current) {
        var line = rows[i].trim()
        if (line) current.lyric_lines.push(line.slice(0, 200))
      }
    }
    if (!groups.length) return []
    var count = groups.length
    return groups.map(function (g, idx) {
      var start = Math.round((duration * idx / count) * 1000) / 1000
      var end = idx === count - 1 ? duration : Math.round((duration * (idx + 1) / count) * 1000) / 1000
      return { id: 'sec-' + (idx + 1), tag: g.tag, label: g.label, start_s: start, end_s: end, lyric_lines: g.lyric_lines }
    })
  }
  function sectionsOf(p) {
    if (p && Array.isArray(p.sections) && p.sections.length) return p.sections
    return deriveSections(p && p.lyrics, p && p.duration_seconds)
  }
  // Replace only the selected section's block in the draft lyrics. Everything
  // outside that block — text above the first [Tag], blank-line structure and
  // long lines in other sections — stays byte-identical. Saving one section
  // never rebuilds the whole song.
  function spliceSectionLyrics(lyrics, section, lines) {
    var source = typeof lyrics === 'string' ? lyrics : ''
    var rows = source.split('\n')
    var spans = []
    for (var i = 0; i < rows.length; i++) {
      var match = HEADER.exec(rows[i])
      if (match) spans.push({ index: i, label: String(match[1]).trim() })
    }
    var wanted = String((section && section.label) || '').trim().toLowerCase()
    var headerIdx = -1
    for (var s = 0; s < spans.length; s++) {
      if (spans[s].label.toLowerCase() === wanted) { headerIdx = spans[s].index; break }
    }
    if (headerIdx < 0) {
      // No header of that name to edit: append the block and leave the rest alone.
      var tail = '[' + section.label + ']\n' + lines.join('\n')
      return source && source.slice(-1) !== '\n' ? source + '\n\n' + tail : source + tail
    }
    var stop = rows.length
    for (var t = 0; t < spans.length; t++) {
      if (spans[t].index > headerIdx) { stop = spans[t].index; break }
    }
    // Keep whatever blank lines closed the old block, so the separator before
    // the next [Tag] survives exactly.
    var trailing = []
    for (var b = stop - 1; b > headerIdx; b--) {
      if (rows[b].trim() === '') trailing.unshift(rows[b])
      else break
    }
    var replacement = [rows[headerIdx]].concat(lines.slice(), trailing)
    return rows.slice(0, headerIdx).concat(replacement, rows.slice(stop)).join('\n')
  }
  function sectionTagClass(tag) { return tag === 'chorus' ? 'chorus' : '' }

  function OnePlayer(props) {
    var [url, setUrl] = useState('')
    var [voiceBusy, setVoiceBusy] = useState(false)
    var [err, setErr] = useState(null)
    var blobRef = useRef(null)
    useEffect(function () {
      return function () { if (blobRef.current) URL.revokeObjectURL(blobRef.current); blobRef.current = null }
    }, [])
    function play() {
      setVoiceBusy(true); setErr(null)
      fetch(editUrl(takeUrl(props.projectId, props.takeId, '/audio'), props.editId), { credentials: 'same-origin' })
        .then(function (res) {
          if (!res || !res.ok) throw new Error('audio request failed' + (res && res.status ? ' (' + res.status + ')' : ''))
          return res.blob()
        })
        .then(function (blob) {
          var next = URL.createObjectURL(blob)
          if (blobRef.current) URL.revokeObjectURL(blobRef.current)
          blobRef.current = next
          setUrl(next)
        })
        .catch(function (e) { setErr(errText(e)) })
        .then(function () { setVoiceBusy(false) })
    }
    function unload() {
      if (blobRef.current) URL.revokeObjectURL(blobRef.current)
      blobRef.current = null; setUrl('')
    }
    return h('div', null,
      h('div', { style: { display: 'flex', gap: '4px' } },
        url
          ? h('button', { type: 'button', className: 'c-btn sm', onClick: unload }, 'Unload')
          : h('button', {
            type: 'button', className: 'c-btn sm', disabled: voiceBusy,
            'aria-label': 'Play take ' + props.n, onClick: play
          }, voiceBusy ? 'Loading…' : 'Play')
      ),
      url ? h('audio', { className: 'ms-audio', src: url, controls: true, 'aria-label': 'Take ' + props.n + ' audio' }) : null,
      err ? h('div', { className: 'ms-error', role: 'alert' }, err) : null
    )
  }

  // ------------------------------------------------------------------- app
  function App() {
    ensureStyles()

    var [loading, setLoading] = useState(true)
    var [projects, setProjects] = useState([])
    var [engineOk, setEngineOk] = useState(null)
    var [engines, setEngines] = useState([])
    var [defaultEngineId, setDefaultEngineId] = useState(null)
    var [engineId, setEngineId] = useState(null)
    var [limits, setLimits] = useState(null)
    var [selectedId, setSelectedId] = useState(null)
    var [draft, setDraft] = useState(draftOf(null))
    var [dirty, setDirty] = useState(false)
    var [formToken, setFormToken] = useState(null)
    var [conflict, setConflict] = useState(null)
    var [error, setError] = useState(null)
    var [notice, setNotice] = useState(null)
    var [busy, setBusy] = useState(null)
    var [audio, setAudio] = useState(null)
    var [playing, setPlaying] = useState(false)
    var [advanced, setAdvanced] = useState(false)
    var [seedText, setSeedText] = useState('')
    var [seedLocked, setSeedLocked] = useState(false)
    var [batchCount, setBatchCount] = useState(3)
    var [presets, setPresets] = useState([])
    var [presetName, setPresetName] = useState('')
    var [presetId, setPresetId] = useState('')
    var [presetsNote, setPresetsNote] = useState(null)
    var [renaming, setRenaming] = useState(false)
    var [renameValue, setRenameValue] = useState('')
    var [confirmDelete, setConfirmDelete] = useState(false)
    var [comparing, setComparing] = useState(false)
    var [keptTakeId, setKeptTakeId] = useState(null)

    // Console-only view state. None of it is ever sent to the server.
    var [activeTab, setActiveTab] = useState('snapshot')
    var [activeTakeId, setActiveTakeId] = useState(null)
    var [selectedSectionId, setSelectedSectionId] = useState(null)
    var [sectionDraft, setSectionDraft] = useState('')
    var [sectionDirty, setSectionDirty] = useState(false)
    var [range, setRange] = useState(null)
    var [playhead, setPlayhead] = useState(0)
    var [abA, setAbA] = useState(null)
    var [abB, setAbB] = useState(null)
    var [audition, setAudition] = useState('A')
    var [peaks, setPeaks] = useState(null)

    var draftRef = useRef(draft)
    draftRef.current = draft
    var dirtyRef = useRef(dirty)
    dirtyRef.current = dirty
    var selectedRef = useRef(selectedId)
    selectedRef.current = selectedId
    var tokenRef = useRef(formToken)
    tokenRef.current = formToken
    var conflictRef = useRef(conflict)
    conflictRef.current = conflict
    var activeTakeRef = useRef(activeTakeId)
    activeTakeRef.current = activeTakeId
    var statusSeq = useRef(0)
    var wrote = function () { statusSeq.current++ }
    var audioRef = useRef(null)
    var audioElRef = useRef(null)
    var aliveRef = useRef(true)
    var seenRef = useRef({})
    var presetsLoaded = useRef(false)
    var boxRef = useRef(null)
    var canvasRef = useRef(null)
    var dragRef = useRef(null)

    var selected = null
    for (var i = 0; i < projects.length; i++) if (projects[i].project_id === selectedId) selected = projects[i]

    function pin(p) {
      var token = p && p.updated_at != null ? p.updated_at : null
      tokenRef.current = token
      setFormToken(token)
    }

    function seed(p) {
      setDraft(draftOf(p))
      setDirty(false)
      pin(p)
      setConflict(null)
    }

    function applyStatus(data) {
      if (!aliveRef.current) return data
      setProjects(data.projects)
      setEngineOk(!!data.engine_available)
      setEngines(Array.isArray(data.engines) ? data.engines : [])
      setDefaultEngineId(data.default_engine_id || null)
      // Keep a real user choice across polls, but never keep an id this server
      // no longer lists: fall back to the server's default instead.
      setEngineId(function (prev) {
        var list = Array.isArray(data.engines) ? data.engines : []
        for (var i = 0; i < list.length; i++) if (list[i].id === prev) return prev
        return data.default_engine_id || null
      })
      setLimits(data.limits || null)
      observeTakes(data.projects, seenRef.current, Date.now())
      var found = null
      for (var i = 0; i < data.projects.length; i++) if (data.projects[i].project_id === selectedRef.current) found = data.projects[i]
      if (!found) {
        var next = data.projects[0] || null
        setSelectedId(next ? next.project_id : null)
        selectedRef.current = next ? next.project_id : null
        seed(next)
        setActiveTakeId(null)
        setSelectedSectionId(null)
        setRange(null)
        return data
      }
      // Keep the active take only while it still exists.
      if (activeTakeRef.current) {
        var aliveTake = null
        for (var j = 0; j < takes(found).length; j++) if (takes(found)[j].take_id === activeTakeRef.current) aliveTake = activeTakeRef.current
        if (!aliveTake) setActiveTakeId(null)
      }
      if (found.updated_at == null) return data
      if (tokenRef.current == null) { pin(found); return data }
      if (found.updated_at === tokenRef.current) { if (conflictRef.current) setConflict(null); return data }
      if (dirtyRef.current) {
        setConflict('Updated elsewhere while you were editing. Your text is kept here; reload to see the latest saved version.')
        return data
      }
      seed(found)
      setNotice('This draft was updated elsewhere. The latest saved version is now shown.')
      return data
    }

    var refresh = useCallback(function () {
      var seq = ++statusSeq.current
      return fetchJSON(API + '/status').then(function (data) {
        if (!data || data.success !== true || !Array.isArray(data.projects)) throw new Error('unexpected /status response')
        if (seq !== statusSeq.current) return data
        setLoading(false)
        return applyStatus(data)
      })
    }, [])

    useEffect(function () {
      var live = true
      var timer = null
      var delay = POLL_IDLE_MS
      function run() {
        refresh().then(function (data) {
          if (!live || !data) return
          delay = anyActive(data.projects) ? POLL_ACTIVE_MS : POLL_IDLE_MS
        }).catch(function (err) {
          if (!live) return
          setLoading(false)
          setError('Could not load studio status — ' + errText(err))
        }).then(function () {
          if (live) timer = setTimeout(run, delay)
        })
      }
      run()
      return function () { live = false; aliveRef.current = false; if (timer) clearTimeout(timer) }
    }, [refresh])

    useEffect(function () {
      aliveRef.current = true
      return function () { aliveRef.current = false; if (audioRef.current) URL.revokeObjectURL(audioRef.current) }
    }, [])

    useEffect(function () {
      if (!advanced || presetsLoaded.current) return
      presetsLoaded.current = true
      var live = true
      fetchJSON(API + '/presets').then(function (res) {
        if (!live) return
        if (!res || res.success !== true) throw new Error('presets unavailable')
        setPresets(Array.isArray(res.presets) ? res.presets : [])
        setPresetsNote(null)
      }).catch(function () {
        presetsLoaded.current = false
        if (live) setPresetsNote('Presets are unavailable on this server right now.')
      })
      return function () { live = false }
    }, [advanced])

    // ---------------------------------------------------------- derived view
    var projTakes = takes(selected)
    var sectionKey = selected ? String(selected.updated_at || '') + '|' + selectedId + '|' + projTakes.length : ''
    var sections = useMemo(function () { return selected ? sectionsOf(selected) : [] }, [sectionKey])

    var activeT = null
    for (var ai = 0; ai < projTakes.length; ai++) if (projTakes[ai].take_id === activeTakeId) activeT = projTakes[ai]
    if (!activeT) activeT = activeTake(selected)
    if (!activeT) {
      var fin = succeeded(selected)
      if (fin.length) activeT = fin[fin.length - 1]
    }
    if (!activeT && projTakes.length) activeT = projTakes[projTakes.length - 1]
    var activeIndex = activeT ? projTakes.indexOf(activeT) : -1
    var activeLabel = activeT ? 'T' + pad2(activeIndex + 1) : '—'

    // The ruler, section cards and playhead are laid on the audio actually in
    // the waveform (the take's own length), not the draft's requested duration.
    var duration = (function () {
      if (!selected) return 0
      var takeDur = takeDuration(activeT)
      if (typeof takeDur === 'number' && takeDur > 0) return takeDur
      var snapDur = typeof selected.duration_seconds === 'number' ? selected.duration_seconds : 0
      return snapDur || 0
    })()

    var selectedSection = null
    for (var si = 0; si < sections.length; si++) if (sections[si].id === selectedSectionId) selectedSection = sections[si]

    var chosenEngine = null
    for (var ei = 0; ei < engines.length; ei++) if (engines[ei].id === engineId) chosenEngine = engines[ei]
    var engineReady = chosenEngine
      ? !!(chosenEngine.available && chosenEngine.supports_generate !== false)
      : engineOk !== false
    var engineReason = chosenEngine && !engineReady
      ? (chosenEngine.selectable_reason
        || (chosenEngine.status && (chosenEngine.status.detail || chosenEngine.status.state))
        || 'unavailable')
      : null
    // Legacy drafts said 'ace'; takes record the canonical id.
    function normalizeEngineId(id) { return id === 'ace' ? 'ace_local' : id }
    function engineLabel(id) {
      var want = normalizeEngineId(id)
      if (!want) return null
      for (var k = 0; k < engines.length; k++) if (engines[k].id === want) return engines[k].label
      return id
    }
    function engineCapabilities(id) {
      var want = normalizeEngineId(id)
      for (var k = 0; k < engines.length; k++) if (engines[k].id === want) return engines[k].capabilities || {}
      return null
    }
    // The child of a take inherits that take's engine. If the parent's engine
    // declares the op false, the control is disabled with the engine's own
    // name and reason instead of queueing a render that must fail.
    var parentEngineCaps = activeT ? engineCapabilities(activeT.engine_id) : null
    var canRepaintParent = !parentEngineCaps || parentEngineCaps.repaint !== false
    var canExtendParent = !parentEngineCaps || parentEngineCaps.continuation !== false
    var parentEngineName = activeT ? (engineLabel(activeT.engine_id) || activeT.engine_id) : null
    var repaintBlockedReason = !canRepaintParent
      ? parentEngineName + ' cannot repaint a range (repaint=false). Generate a take on an engine that supports repaint to edit it.'
      : null
    var extendBlockedReason = !canExtendParent
      ? parentEngineName + ' cannot extend a take (continuation=false). Generate a take on an engine that supports continuation.'
      : null

    // ------------------------------------------------------------- waveform
    var activeState = activeT ? activeT.state : null
    var activeTakeKey = activeT ? activeT.take_id : null
    var activeEditId = activeT ? activeT.playable_edit_id : null
    useEffect(function () {
      if (!selected || !activeTakeKey || activeState !== 'succeeded') { setPeaks(null); return }
      var live = true
      var takeId = activeTakeKey
      var url = editUrl(takeUrl(selected.project_id, takeId, '/peaks?buckets=' + BUCKETS), activeEditId)
      fetchJSON(url).then(function (res) {
        if (!live) return
        if (res && Array.isArray(res.peaks)) setPeaks({ takeId: takeId, values: res.peaks })
        else setPeaks({ takeId: takeId, values: null })
      }).catch(function () {
        // A missing waveform is stated as missing; it is never faked.
        if (live) setPeaks({ takeId: takeId, values: null })
      })
      return function () { live = false }
    }, [selectedId, activeTakeKey, activeState, activeEditId])

    useEffect(function () {
      var c = canvasRef.current
      if (!c) return
      var ctx = c.getContext && c.getContext('2d')
      if (!ctx) return
      var w = c.clientWidth || 600
      var hh = c.clientHeight || 150
      if (c.width !== w) c.width = w
      if (c.height !== hh) c.height = hh
      ctx.clearRect(0, 0, w, hh)
      // Centre rule: the shape of a running take that has not rendered yet.
      ctx.strokeStyle = 'rgba(143,160,182,.35)'
      ctx.beginPath(); ctx.moveTo(0, hh / 2); ctx.lineTo(w, hh / 2); ctx.stroke()
      var values = peaks && peaks.values && activeTakeKey && peaks.takeId === activeTakeKey ? peaks.values : null
      if (!values || !values.length) return
      var mid = hh / 2
      var maxAmp = (hh / 2) * 0.6
      var step = w / values.length
      ctx.fillStyle = 'rgba(0,83,253,.72)'
      for (var k = 0; k < values.length; k++) {
        var v = values[k] || {}
        var amp = Math.max(Math.abs(Number(v.max) || 0), Math.abs(Number(v.min) || 0))
        var bh = Math.max(1, amp * maxAmp)
        ctx.fillRect(k * step, mid - bh, Math.max(1, step - 1), bh * 2)
      }
    }, [peaks, activeTakeKey, range, playhead, audition])

    useEffect(function () {
      var el = audioElRef.current
      if (!el || !isFinite(playhead)) return
      try { if (Math.abs((el.currentTime || 0) - playhead) > 0.35) el.currentTime = playhead } catch (e) { /* not seekable yet */ }
    }, [playhead, audio && audio.url])

    // ------------------------------------------------------------- handlers
    function reload() {
      refresh().catch(function (err) { setError('Could not load studio status — ' + errText(err)) })
    }

    function reloadDraft() {
      refresh().then(function (data) {
        var fresh = null
        for (var i = 0; i < (data.projects || []).length; i++) if (data.projects[i].project_id === selectedId) fresh = data.projects[i]
        if (fresh) seed(fresh)
        setNotice('Reloaded the latest saved version. Unsaved edits were discarded.')
      }).catch(function (err) { setError('Could not load studio status — ' + errText(err)) })
    }

    function resetViewState() {
      setSelectedSectionId(null)
      setSectionDirty(false)
      setRange(null)
      setPlayhead(0)
      setAbA(null); setAbB(null); setAudition('A')
    }

    function selectProject(p) {
      setSelectedId(p.project_id)
      selectedRef.current = p.project_id
      seed(p)
      setError(null)
      setNotice(null)
      setRenaming(false)
      setConfirmDelete(false)
      setComparing(false)
      setActiveTakeId(null)
      resetViewState()
    }

    function setField(key, value) {
      setDraft(function (prev) {
        var next = {}
        for (var k in prev) next[k] = prev[k]
        next[key] = value
        return next
      })
      setDirty(true)
    }

    function editNumber(key, raw, fallbackValue) {
      var n = Number(raw)
      var value = raw === '' || !isFinite(n) ? fallbackValue : Math.round(n)
      setField(key, value)
    }

    function normalizeDraft() {
      var d = draftRef.current
      var dur = parseInt(d.duration_seconds, 10)
      if (!isFinite(dur)) dur = 30
      var bpm = d.bpm == null || d.bpm === '' ? null : Math.round(Number(d.bpm))
      if (bpm != null && !isFinite(bpm)) bpm = null
      return {
        title: String(d.title || ''),
        idea: String(d.idea || ''),
        caption: String(d.caption || ''),
        lyrics: String(d.lyrics || ''),
        duration_seconds: dur,
        instrumental: !!d.instrumental,
        vocal_language: String(d.vocal_language || DEFAULT_LANG),
        bpm: bpm,
        keyscale: d.keyscale ? String(d.keyscale) : null
      }
    }

    function run(kind, fn, failure) {
      setBusy(kind)
      setError(null)
      setNotice(null)
      return Promise.resolve()
        .then(fn)
        .catch(function (err) { if (aliveRef.current) setError(explain(err, failure)) })
        .then(function () { if (aliveRef.current) setBusy(null) })
    }

    function createProject() {
      run('create', function () {
        return fetchJSON(API + '/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ draft: { title: 'Untitled', idea: '', caption: '', lyrics: '', duration_seconds: 30 } })
        }).then(function (res) {
          if (!res || res.success !== true || !res.project) throw new Error('create returned no project')
          wrote()
          setProjects(function (prev) { return prev.concat([res.project]) })
          selectProject(res.project)
          setNotice('Created project ' + res.project.project_id.slice(0, 8) + '…')
        })
      }, 'nothing was created')
    }

    function saveProject() {
      if (!selectedId) return
      var body = normalizeDraft()
      // Stored sections are a partition of one duration: when the words or the
      // duration move, the stored partition is stale. Clear it so the server
      // re-derives the structure from the text instead of keeping a partition
      // that no longer covers the song.
      var savedLyrics = selected && typeof selected.lyrics === 'string' ? selected.lyrics : ''
      var savedDuration = selected ? selected.duration_seconds : null
      if (body.lyrics !== savedLyrics || body.duration_seconds !== savedDuration) body.sections = []
      run('save', function () {
        var payload = { project_id: selectedId, draft: body }
        if (tokenRef.current != null) payload.base_updated_at = tokenRef.current
        return fetchJSON(API + '/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function (res) {
          if (!res || res.success !== true || !res.project) throw new Error('update returned no project')
          var p = res.project
          wrote()
          setProjects(function (prev) {
            return prev.map(function (x) { return x.project_id === p.project_id ? p : x })
          })
          seed(p)
          setNotice('Saved. Approval was revoked — approve again before generating.')
        }).catch(function (err) {
          if (!isStale(err)) throw err
          return refresh().then(function (data) {
            var fresh = null
            for (var i = 0; i < (data.projects || []).length; i++) if (data.projects[i].project_id === selectedId) fresh = data.projects[i]
            if (fresh) seed(fresh)
            setNotice('Your edit was not applied: this draft was saved elsewhere in the meantime. The latest saved version is shown — reapply your changes if you still need them.')
          })
        })
      }, 'nothing was saved')
    }

    function approveProject() {
      if (!selectedId) return
      run('approve', function () {
        var payload = { project_id: selectedId }
        // Approve the version on screen: a draft that changed under us must
        // 409, never silently approve text the user has not reviewed.
        if (tokenRef.current != null) payload.base_updated_at = tokenRef.current
        return fetchJSON(API + '/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function (res) {
          if (!res || res.success !== true || !res.project) throw new Error('approve returned no project')
          var p = res.project
          wrote()
          setProjects(function (prev) {
            return prev.map(function (x) { return x.project_id === p.project_id ? p : x })
          })
          seed(p)
          setNotice('Approved. Generating uses this exact draft snapshot.')
        }).catch(function (err) {
          if (!isStale(err)) throw err
          return refresh().then(function (data) {
            var fresh = null
            for (var i = 0; i < (data.projects || []).length; i++) if (data.projects[i].project_id === selectedId) fresh = data.projects[i]
            if (fresh) seed(fresh)
            setNotice('Your approval did not land: this draft changed on the server first. The latest saved version is shown — review it and approve again.')
          })
        })
      }, 'nothing was approved')
    }

    function nextSeed() {
      var locked = seedLocked ? asSeed(seedText) : null
      return locked != null ? locked : newSeed()
    }

    function generate() {
      if (!selectedId) return
      var seedValue = nextSeed()
      var lockedNow = seedLocked && asSeed(seedText) != null
      setSeedText(String(seedValue))
      run('generate', function () {
        var payload = { project_id: selectedId, seed: seedValue, lock_seed: lockedNow }
        if (engineId) payload.engine_id = engineId
        return fetchJSON(API + '/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('generate returned no take')
          wrote()
          setNotice('Queued take ' + String(res.take && res.take.take_id || '').slice(0, 8) + '… (seed ' + seedValue + ')')
          return refresh()
        })
      }, 'nothing was queued')
    }

    function batch() {
      if (!selectedId) return
      var count = Math.min(8, Math.max(1, Math.round(Number(batchCount)) || 1))
      var base = seedLocked ? asSeed(seedText) : null
      var body = { project_id: selectedId, count: count }
      if (base != null) body.seed_base = base
      if (engineId) body.engine_id = engineId
      run('batch', function () {
        return fetchJSON(API + '/batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('batch returned no acknowledgement')
          wrote()
          var made = Array.isArray(res.takes) ? res.takes.length
            : isFinite(Number(res.queued != null ? res.queued : res.accepted)) ? Number(res.queued != null ? res.queued : res.accepted) : null
          setNotice(made == null ? 'Batch request accepted. Waiting for the server to list the takes.'
            : 'Queued ' + made + ' take' + (made === 1 ? '' : 's') + (base != null ? ' from seed ' + base : '') + '.')
          return refresh()
        })
      }, 'nothing was queued')
    }

    function cancelBatch() {
      if (!selectedId) return
      run('cancel', function () {
        return fetchJSON(API + '/batch/cancel', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: selectedId })
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('cancel returned no acknowledgement')
          wrote()
          var stopped = Number(res.cancelled)
          setNotice(isFinite(stopped) ? 'Cancelled ' + stopped + ' queued take' + (stopped === 1 ? '' : 's') + '.' : 'Asked the server to cancel the queued takes.')
          return refresh()
        })
      }, 'no queued take was cancelled')
    }

    function recover() {
      run('recover', function () {
        return fetchJSON(API + '/recover', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true })
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('recover returned no result')
          setNotice('Requested recovery for the stuck take.')
          return refresh()
        })
      }, 'nothing was recovered')
    }

    function startRename() {
      setRenameValue((selected && selected.title) || '')
      setRenaming(true)
    }

    function renameProject() {
      if (!selectedId) return
      var title = String(renameValue || '').trim()
      if (!title) { setRenaming(true); return }
      run('rename', function () {
        return fetchJSON(API + '/rename', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: selectedId, title: title })
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('rename returned no project')
          wrote()
          var p = res.project || { project_id: selectedId, title: title }
          setProjects(function (prev) {
            return prev.map(function (x) { return x.project_id === selectedId ? Object.assign({}, x, p) : x })
          })
          if (!dirtyRef.current && p.updated_at != null) pin(p)
          setRenaming(false)
          setNotice('Renamed to “' + title + '”.')
        })
      }, 'the project was not renamed')
    }

    function deleteProject() {
      if (!selectedId) return
      var name = (selected && selected.title) || 'Untitled song'
      var id = selectedId
      run('delete', function () {
        return fetchJSON(API + '/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: id })
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('delete returned no acknowledgement')
          wrote()
          setProjects(function (prev) { return prev.filter(function (x) { return x.project_id !== id }) })
          setConfirmDelete(false)
          setRenaming(false)
          setSelectedId(null)
          selectedRef.current = null
          tokenRef.current = null
          setFormToken(null)
          setDraft(draftOf(null))
          setDirty(false)
          setActiveTakeId(null)
          resetViewState()
          setNotice('Deleted “' + name + '”. Its WAV files were removed too.')
        })
      }, 'the project was not deleted')
    }

    function presetKey(p) { return p.preset_id != null ? p.preset_id : p.name }

    function savePreset() {
      var name = String(presetName || '').trim()
      if (!name) { setError('Name the preset first.'); return }
      var d = normalizeDraft()
      var payload = {
        name: name, caption: d.caption, instrumental: d.instrumental,
        vocal_language: d.vocal_language, bpm: d.bpm, keyscale: d.keyscale
      }
      run('preset', function () {
        return fetchJSON(API + '/presets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('preset save returned no presets')
          setPresets(Array.isArray(res.presets) ? res.presets : function (prev) { return prev.concat([payload]) })
          setPresetName('')
          setPresetId(name)
          setNotice('Saved preset “' + name + '”.')
        })
      }, 'the preset was not saved')
    }

    function applyPreset(id) {
      var found = null
      for (var i = 0; i < presets.length; i++) if (String(presetKey(presets[i])) === String(id)) found = presets[i]
      if (!found) return
      setPresetId(id)
      var s = found.draft || found.params || found
      setDraft(function (prev) {
        var next = {}
        for (var k in prev) next[k] = prev[k]
        next.caption = s.caption != null ? s.caption : s.style != null ? s.style : prev.caption
        next.instrumental = !!s.instrumental
        next.vocal_language = s.vocal_language || prev.vocal_language
        next.bpm = typeof s.bpm === 'number' ? s.bpm : null
        next.keyscale = s.keyscale != null ? s.keyscale : null
        next.duration_seconds = s.duration_seconds != null ? s.duration_seconds : prev.duration_seconds
        return next
      })
      setDirty(true)
      setNotice('Applied preset “' + (found.name || found.preset_id) + '”. Save the draft to keep it.')
    }

    function deletePreset() {
      var found = null
      for (var i = 0; i < presets.length; i++) if (String(presetKey(presets[i])) === String(presetId)) found = presets[i]
      if (!found) { setError('Choose a preset to delete.'); return }
      var key = presetKey(found)
      run('preset', function () {
        return fetchJSON(API + '/presets/' + encodeURIComponent(key), { method: 'DELETE' }).then(function (res) {
          if (!res || res.success !== true) throw new Error('preset delete returned no acknowledgement')
          if (Array.isArray(res.presets)) setPresets(res.presets)
          else setPresets(function (prev) { return prev.filter(function (p) { return String(presetKey(p)) !== String(key) }) })
          setPresetId('')
          setNotice('Deleted the preset.')
        })
      }, 'the preset was not deleted')
    }

    function playTake(p, take) {
      run('audio', function () {
        return fetch(editUrl(takeUrl(p.project_id, take.take_id, '/audio'), take.playable_edit_id), { credentials: 'same-origin' })
          .then(function (res) {
            if (!res || !res.ok) throw new Error('audio request failed' + (res && res.status ? ' (' + res.status + ')' : ''))
            return res.blob()
          })
          .then(function (blob) {
            var url = URL.createObjectURL(blob)
            if (audioRef.current) URL.revokeObjectURL(audioRef.current)
            audioRef.current = url
            setPlaying(false)
            setAudio({ takeId: take.take_id, url: url })
          })
      })
    }

    function stopAudio() {
      if (audioRef.current) URL.revokeObjectURL(audioRef.current)
      audioRef.current = null
      setPlaying(false)
      setAudio(null)
    }

    function togglePlay() {
      var el = audioElRef.current
      if (el && audio && audio.takeId === (activeT && activeT.take_id)) {
        if (el.paused) { var pl = el.play && el.play(); if (pl && pl.catch) pl.catch(function () {}) }
        else if (el.pause) el.pause()
        return
      }
      if (selected && activeT && activeT.state === 'succeeded') playTake(selected, activeT)
    }

    function selectSection(sec) {
      setSelectedSectionId(sec.id)
      setSectionDraft((sec.lyric_lines || []).join('\n'))
      setSectionDirty(false)
      setPlayhead(sec.start_s)
    }

    function saveSection() {
      if (!selected || !selectedSection) return
      if (dirtyRef.current) {
        setError('Save or discard your unsaved draft edits first — saving section words reloads the draft.')
        return
      }
      var text = String(sectionDraft || '')
      var lines = text.split(/\r?\n/).map(function (l) { return l.trim() }).filter(function (l) { return l.length > 0 })
      var next = sections.map(function (s) {
        return s.id === selectedSection.id
          ? { id: s.id, tag: s.tag, label: s.label, start_s: s.start_s, end_s: s.end_s, lyric_lines: lines }
          : { id: s.id, tag: s.tag, label: s.label, start_s: s.start_s, end_s: s.end_s, lyric_lines: s.lyric_lines }
      })
      // Splice the one edited block; the rest of the lyrics stay as written.
      var spliced = spliceSectionLyrics(selected.lyrics || '', selectedSection, lines)
      // A lyrics-derived partition is the server's: sending it back would pin it
      // as stored and stop later lyric or duration edits from refreshing it.
      var payloadDraft = selected.sections_source === 'lyrics'
        ? { lyrics: spliced }
        : { sections: next, lyrics: spliced }
      var sectionLabel = selectedSection.label
      var projectId = selected.project_id
      run('section', function () {
        var payload = { project_id: projectId, draft: payloadDraft }
        // The version token whose text is in this editor. A poll never moves it.
        if (tokenRef.current != null) payload.base_updated_at = tokenRef.current
        return fetchJSON(API + '/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function (res) {
          if (!res || res.success !== true || !res.project) throw new Error('update returned no project')
          wrote()
          var p = res.project
          setProjects(function (prev) { return prev.map(function (x) { return x.project_id === p.project_id ? p : x }) })
          seed(p)
          setSectionDirty(false)
          setNotice('Saved ' + sectionLabel + '. Approval was revoked — approve again before generating.')
        }).catch(function (err) {
          if (!isStale(err)) throw err
          return refresh().then(function (data) {
            var fresh = null
            for (var i = 0; i < (data.projects || []).length; i++) if (data.projects[i].project_id === projectId) fresh = data.projects[i]
            if (fresh) seed(fresh)
            setSectionDirty(false)
            setNotice('Your lyric edit was not applied: this draft was saved elsewhere in the meantime (409 stale draft). The latest saved version is shown.')
          })
        })
      }, 'no lyric edit was saved')
    }

    function submitRepaint() {
      if (!selected || !activeT || !range) return
      var start = Math.round(range.start * 10) / 10
      var end = Math.round(range.end * 10) / 10
      if (end - start < 3) { setError('A repaint range must be at least 3.0 s (it is ' + (end - start).toFixed(1) + ' s).'); return }
      var takeId = activeT.take_id
      var projectId = selected.project_id
      run('repaint', function () {
        return fetchJSON(API + '/repaint', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: projectId, take_id: takeId, start_s: start, end_s: end })
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('repaint returned no take')
          wrote()
          setRange(null)
          setNotice('Queued a repaint of ' + clock(start) + '–' + clock(end) + ' as a new take. The server draws a fresh seed.')
          return refresh()
        })
      }, 'nothing was queued')
    }

    function submitExtend() {
      if (!selected || !activeT) return
      var takeId = activeT.take_id
      var projectId = selected.project_id
      var label = activeLabel
      run('extend', function () {
        return fetchJSON(API + '/extend', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: projectId, take_id: takeId, extend_seconds: 8 })
        }).then(function (res) {
          if (!res || res.success !== true) throw new Error('extend returned no take')
          wrote()
          setNotice('Queued an 8 s extension of ' + label + ' as a new take.')
          return refresh()
        })
      }, 'nothing was queued')
    }

    function assignAB(takeId, slot) {
      if (slot === 'A') setAbA(takeId)
      else setAbB(takeId)
    }

    function auditionSlot(slot) {
      var id = slot === 'A' ? abA : abB
      if (!id) { setNotice('Assign a take to ' + slot + ' first.'); return }
      setAudition(slot)
      var same = id === activeTakeId
      setActiveTakeId(id)
      // Switching never resets the playhead: it is the shared cursor of every
      // layer, and the whole point of A/B is hearing the same moment.
      if (!same && audioRef.current) stopAudio()
    }

    function timeAt(clientX) {
      var el = boxRef.current
      var rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : { left: 0, width: 0 }
      var w = rect.width || 600
      var x = Math.max(0, Math.min(w, Number(clientX) - (rect.left || 0)))
      var t = duration ? (x / w) * duration : 0
      return Math.max(0, Math.min(duration, Math.round(t / SNAP_S) * SNAP_S))
    }

    function beginRange(e) {
      if (!activeT || activeT.state !== 'succeeded' || !duration) return
      var t = timeAt(e.clientX)
      dragRef.current = { anchor: t, moved: false }
      setPlayhead(t)
      setRange({ start: t, end: Math.min(duration, t + SNAP_S), live: false })
    }

    function dragRange(e) {
      var d = dragRef.current
      if (!d) return
      var t = timeAt(e.clientX)
      if (Math.abs(t - d.anchor) >= SNAP_S) d.moved = true
      var start = Math.min(d.anchor, t)
      var end = Math.max(d.anchor, t)
      if (end - start < SNAP_S) end = Math.min(duration, start + SNAP_S)
      setRange({ start: start, end: end, live: true })
    }

    function endRange() {
      var d = dragRef.current
      if (!d) return
      dragRef.current = null
      if (!d.moved) { setRange(null); return }
      setRange(function (prev) { return prev ? { start: prev.start, end: prev.end, live: false } : prev })
    }

    // ------------------------------------------------------------ keyboard
    useEffect(function () {
      function onKey(e) {
        var el = e.target
        var tag = el && el.tagName ? String(el.tagName).toUpperCase() : ''
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (el && el.isContentEditable)) return
        // Space is the browser's activation key for these; never hijack it.
        if (tag === 'BUTTON' || tag === 'A' || (el && el.getAttribute && el.getAttribute('role') === 'tab')) return
        if (e.key === ' ') { e.preventDefault(); togglePlay(); return }
        if (e.key === '[' || e.key === ']') {
          e.preventDefault()
          var dir = e.key === ']' ? 1 : -1
          var idx = -1
          for (var i = 0; i < sections.length; i++) if (sections[i].id === selectedSectionId) idx = i
          var nextIdx = idx < 0 ? (dir > 0 ? 0 : sections.length - 1) : Math.max(0, Math.min(sections.length - 1, idx + dir))
          if (sections[nextIdx]) selectSection(sections[nextIdx])
          return
        }
        if (/^[1-9]$/.test(e.key)) {
          var n = Number(e.key) - 1
          if (projTakes[n]) { setActiveTakeId(projTakes[n].take_id); if (audioRef.current) stopAudio() }
          return
        }
        if (e.key === 'Escape') { setRange(null); return }
      }
      window.addEventListener('keydown', onKey)
      return function () { window.removeEventListener('keydown', onKey) }
    })

    // -------------------------------------------------------------- render
    var activeList = selected ? projTakes.filter(isActive) : []
    var liveText = activeList.length
      ? activeList.map(function (t) { return 'Take ' + (projTakes.indexOf(t) + 1) + ': ' + stateLabel(t) }).join(' · ')
      : selected ? 'No take in progress.' : ''
    var stuck = selected ? stuckTake(selected, Date.now(), seenRef.current) : null
    var running = selected ? activeTake(selected) : null
    var finished = selected ? succeeded(selected) : []
    var queuedList = selected ? queued(selected) : []
    var canSave = dirty && !busy
    var canApprove = !busy && !dirty && !conflict && selected && !selected.approved
    var canGenerate = !busy && !dirty && !conflict && selected && selected.approved && !running && engineReady
    var canBatch = !busy && !dirty && !conflict && selected && selected.approved && engineReady
    var batchN = Math.min(8, Math.max(1, Math.round(Number(batchCount)) || 1))

    var projectList = projects.length === 0
      ? h('div', { className: 'ms-empty' }, loading ? 'Loading projects…' : 'No projects yet. Create one to start.')
      : projects.map(function (p) {
        var t = takes(p)
        return h('button', {
          key: p.project_id, type: 'button', className: 'proj',
          'aria-pressed': p.project_id === selectedId,
          onClick: function () { selectProject(p) }
        },
          h('b', null, p.title && p.title.trim() ? p.title : '(untitled)'),
          h('span', { className: 'n' }, String(t.length))
        )
      })

    var railSections = sections.map(function (sec) {
      return h('button', {
        key: sec.id, type: 'button', className: 'railsec',
        'aria-pressed': sec.id === selectedSectionId,
        onClick: function () { selectSection(sec) }
      },
        h('span', { className: 'ms-chip' }, sec.tag),
        h('span', { className: 't' }, sec.label)
      )
    })

    var railTakes = projTakes.slice().reverse().map(function (t) {
      var idx = projTakes.indexOf(t)
      return h('button', {
        key: t.take_id, type: 'button', className: 'railtake',
        'aria-pressed': t.take_id === (activeT && activeT.take_id),
        onClick: function () { setActiveTakeId(t.take_id); if (audioRef.current) stopAudio() }
      },
        h('span', { className: 'id' }, 'T' + pad2(idx + 1)),
        h('span', { className: 'dot ' + (t.state === 'succeeded' ? 'ok' : t.state === 'failed' ? 'bad' : ''), 'aria-hidden': 'true' }),
        h('span', { style: { marginLeft: 'auto' } }, short(takeDuration(t)))
      )
    })

    var rulerTicks = []
    if (duration > 0) {
      var tickStep = duration > 90 ? 20 : duration > 40 ? 10 : 5
      for (var ts = 0; ts <= duration + 0.001; ts += tickStep) {
        var leftPct = (ts / duration) * 100
        rulerTicks.push(h('i', { key: 'tick' + ts, style: { left: leftPct + '%' } }))
        rulerTicks.push(h('span', { key: 'lab' + ts, style: { left: leftPct + '%' } }, short(ts)))
      }
    }

    var sectionCards = sections.map(function (sec) {
      // Clip the card to the audio in the waveform; a card that starts after the
      // take ends would otherwise run past the sound it belongs to.
      var start = Math.max(0, Math.min(duration, sec.start_s))
      var end = Math.max(start, Math.min(duration, sec.end_s))
      if (!duration || end <= start) return null
      var width = ((end - start) / duration) * 100
      var left = (start / duration) * 100
      return h('button', {
        key: sec.id, type: 'button',
        className: 'seccard ' + sectionTagClass(sec.tag),
        'data-section': sec.id,
        'aria-label': 'Select section ' + sec.label,
        'aria-pressed': sec.id === selectedSectionId,
        style: { left: left + '%', width: Math.max(0.5, width) + '%' },
        onClick: function () { selectSection(sec) }
      },
        h('span', { className: 'tap' }),
        h('span', { className: 'tag' }, sec.label),
        width > 12 ? h('span', { className: 'range' }, clock(sec.start_s) + '–' + clock(sec.end_s)) : null,
        h('span', { className: 'cnt' }, (sec.lyric_lines || []).length + ((sec.lyric_lines || []).length === 1 ? ' line' : ' lines'))
      )
    })

    var rangePct = range && duration
      ? { left: (range.start / duration) * 100, width: Math.max(0.2, ((range.end - range.start) / duration) * 100) }
      : null
    var playPct = duration ? Math.max(0, Math.min(100, (playhead / duration) * 100)) : 0

    var waveOverlay = []
    if (rangePct) {
      waveOverlay.push(h('div', {
        key: 'rg', className: 'rg',
        style: { left: rangePct.left + '%', width: rangePct.width + '%' }
      }, h('span', { className: 'lab' }, clock(range.start) + '–' + clock(range.end) + ' · ' + (range.end - range.start).toFixed(1) + ' s')))
    }
    waveOverlay.push(h('div', { key: 'ph', className: 'ph', style: { left: playPct + '%' } }))

    var noAudio = !activeT || activeT.state === 'failed' || activeT.state === 'queued'
    var wavebox = h('div', {
      className: 'wavebox', ref: boxRef,
      onMouseDown: beginRange, onMouseMove: dragRange, onMouseUp: endRange, onMouseLeave: endRange,
      'data-testid': 'waveform',
      'aria-label': 'Waveform for take ' + (activeIndex + 1) + (activeT ? ', ' + stateLabel(activeT) : '')
    },
      h('canvas', { ref: canvasRef }),
      noAudio ? h('div', { className: 'noaudio' }, activeT && activeT.state === 'failed' ? 'NO AUDIO — FAILED' : activeT ? 'NO AUDIO YET' : 'NO TAKE') : null,
      waveOverlay
    )

    var takeStrip = h('div', { className: 'strip', 'aria-label': 'Takes' }, projTakes.length === 0
      ? h('div', { className: 'ms-empty strip-empty', style: { width: '100%' } }, 'No takes yet — generate the first one.')
      : projTakes.map(function (t) {
        var idx = projTakes.indexOf(t)
        var len = honestLength(t)
        var seedShown = takeSeed(t)
        var engId = t.engine_id || (t.snapshot && t.snapshot.engine) || null
        var eng = engId ? (engineLabel(engId) || engId) : '—'
        var tone = t.state === 'succeeded' ? 'ok' : t.state === 'failed' ? 'bad' : (t.state === 'running' ? 'live' : '')
        return h('div', {
          key: t.take_id, className: 'tile', 'data-active': t.take_id === (activeT && activeT.take_id) ? '1' : '0',
          onClick: function () { setActiveTakeId(t.take_id); if (audioRef.current) stopAudio() }
        },
          h('div', { className: 'top' },
            h('span', { className: 'tid' }, 'T' + pad2(idx + 1)),
            h('span', { className: 'dot ' + tone, 'aria-hidden': 'true' }),
            h('span', { className: 'len' }, short(takeDuration(t)))
          ),
          h('span', { className: 'ms-chip ' + (t.state === 'succeeded' ? 'ok' : t.state === 'failed' ? 'bad' : 'warn') }, stateLabel(t)),
          t.state === 'queued' && typeof t.queue_position === 'number' ? h('span', { className: 'ms-chip' }, 'Queue #' + t.queue_position) : null,
          h('span', { className: 'meta' }, 'seed ' + (seedShown == null ? '—' : seedShown) + ' · ' + eng + (t.seed_locked ? ' · locked' : '')),
          len ? h('span', { className: 'lang' }, len) : null,
          t.state === 'running' && typeof t.progress === 'number'
            ? h('div', { className: 'track' }, h('i', { style: { width: Math.max(0, Math.min(100, t.progress)) + '%' } }))
            : null,
          h('div', { className: 'ab' },
            h('button', {
              type: 'button', 'aria-label': 'Assign take ' + (idx + 1) + ' to A',
              'aria-pressed': abA === t.take_id,
              onClick: function (e) { e.stopPropagation(); assignAB(t.take_id, 'A') }
            }, 'A'),
            h('button', {
              type: 'button', 'aria-label': 'Assign take ' + (idx + 1) + ' to B',
              'aria-pressed': abB === t.take_id,
              onClick: function (e) { e.stopPropagation(); assignAB(t.take_id, 'B') }
            }, 'B')
          ),
          h('div', { className: 'acts' },
            t.state === 'succeeded'
              ? h('button', {
                type: 'button', className: 'c-btn sm',
                disabled: busy === 'audio',
                onClick: function (e) { e.stopPropagation(); (audio && audio.takeId === t.take_id) ? stopAudio() : playTake(selected, t) }
              }, busy === 'audio' ? 'Loading…' : (audio && audio.takeId === t.take_id ? 'Unload' : 'Play'))
              : null,
            (t.state === 'succeeded' || t.audio_file)
              ? h('a', {
                className: 'c-btn sm', download: '',
                href: editUrl(takeUrl(selected.project_id, t.take_id, '/download'), t.playable_edit_id),
                'aria-label': 'Download WAV for take ' + (idx + 1)
              }, 'Download WAV')
              : null,
            t.error ? h('span', { className: 'ms-chip bad' }, t.error) : null
          )
        )
      }))

    var takeStripHeader = h('div', { style: { display: 'flex', alignItems: 'center', gap: '8px' } },
      h('span', { className: 'c-micro' }, 'Takes'),
      finished.length >= 2
        ? h('button', {
          type: 'button', className: 'c-btn sm', disabled: !!busy,
          'aria-expanded': comparing,
          onClick: function () {
            setComparing(function (c) { return !c })
          }
        }, comparing ? 'Hide compare' : 'Compare takes')
        : null
    )

    var ledger = h('div', { className: 'ledger', 'aria-label': 'Section ledger' },
      h('div', { className: 'lh' }, h('span', null, 'Section'), h('span', null, 'Start'), h('span', null, 'Length'), h('span', null, 'Lines')),
      sections.map(function (sec) {
        return h('button', {
          key: sec.id, type: 'button', className: 'lrow',
          'aria-pressed': sec.id === selectedSectionId,
          onClick: function () { selectSection(sec) }
        },
          h('span', null, sec.label),
          h('span', { className: 'num' }, clock(sec.start_s)),
          h('span', { className: 'num' }, (sec.end_s - sec.start_s).toFixed(1) + ' s'),
          h('span', { className: 'num' }, String((sec.lyric_lines || []).length))
        )
      })
    )

    var sectionEditor = selectedSection
      ? h('div', { className: 'sec-edit' },
        h('div', { className: 'head' },
          h('span', { className: 'c-micro' }, selectedSection.label),
          h('b', null, clock(selectedSection.start_s) + '–' + clock(selectedSection.end_s)),
          h('span', { className: 'd' }, sectionDirty ? 'unsaved lyrics' : 'saved')
        ),
        h('textarea', {
          'aria-label': 'Section words',
          value: sectionDraft,
          onChange: function (e) { setSectionDraft(e.target.value); setSectionDirty(true) }
        }),
        h('div', { style: { display: 'flex', gap: '6px', alignItems: 'center' } },
          h('button', { type: 'button', className: 'c-btn primary', disabled: !!busy || !sectionDirty || dirty, onClick: saveSection }, busy === 'section' ? 'Saving…' : 'Save section words'),
          h('button', {
            type: 'button', className: 'c-btn', disabled: !!busy,
            onClick: function () { setSectionDraft((selectedSection.lyric_lines || []).join('\n')); setSectionDirty(false) }
          }, 'Revert')
        )
      )
      : null

    // ------------------------------------------------------------- inspector
    var batchControls = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } },
      h('div', { className: 'c-field', style: { maxWidth: '9rem' } },
        h('label', { htmlFor: 'ms-count' }, 'Takes per batch'),
        h('input', {
          id: 'ms-count', type: 'number', min: '1', max: '8', 'aria-label': 'Takes per batch',
          value: batchCount,
          onChange: function (e) { setBatchCount(e.target.value) }
        })
      ),
      h('div', { style: { display: 'flex', gap: '6px' } },
        h('button', { type: 'button', className: 'c-btn primary', disabled: !canBatch, onClick: batch },
          busy === 'batch' ? 'Queuing…' : 'Give me ' + batchN + ' take' + (batchN === 1 ? '' : 's')),
        queuedList.length
          ? h('button', { type: 'button', className: 'c-btn', disabled: !!busy, onClick: cancelBatch },
            busy === 'cancel' ? 'Cancelling…' : 'Cancel batch')
          : null
      ),
      h('p', { className: 'mut' }, 'Queues ' + batchN + ' take' + (batchN === 1 ? '' : 's') + '. One GPU job at a time.')
    )

    var advancedPanel = advanced
      ? h('div', { className: 'adv', id: 'ms-advanced', role: 'group', 'aria-label': 'Advanced options' },
        h('div', { className: 'c-micro' }, 'Advanced'),
        h('label', { className: 'check' },
          h('input', {
            type: 'checkbox', 'aria-label': 'Instrumental', checked: !!draft.instrumental,
            onChange: function (e) { setField('instrumental', e.target.checked) }
          }),
          'Instrumental (no vocals)'
        ),
        h('div', { className: 'c-field' },
          h('label', { htmlFor: 'ms-lang' }, 'Vocal language'),
          h('select', {
            id: 'ms-lang', 'aria-label': 'Vocal language', value: draft.vocal_language || '',
            onChange: function (e) { setField('vocal_language', e.target.value) }
          }, LANGUAGES.map(function (pair) {
            return h('option', { key: pair[0] || 'auto', value: pair[0] }, pair[1])
          }))
        ),
        h('div', { className: 'two' },
          h('div', { className: 'c-field' },
            h('label', { htmlFor: 'ms-bpm' }, 'BPM'),
            h('input', {
              id: 'ms-bpm', type: 'number', min: '20', max: '300', 'aria-label': 'BPM',
              value: draft.bpm == null ? '' : draft.bpm,
              onChange: function (e) { editNumber('bpm', e.target.value, null) }
            })
          ),
          h('div', { className: 'c-field' },
            h('label', { htmlFor: 'ms-key' }, 'Key scale'),
            h('input', {
              id: 'ms-key', type: 'text', 'aria-label': 'Key scale', placeholder: 'Am, F#m, C major…',
              value: draft.keyscale == null ? '' : draft.keyscale,
              onChange: function (e) { setField('keyscale', e.target.value === '' ? null : e.target.value) }
            })
          )
        ),
        h('div', { className: 'two' },
          h('div', { className: 'c-field' },
            h('label', { htmlFor: 'ms-seed' }, 'Seed'),
            h('input', {
              id: 'ms-seed', type: 'number', min: '0', 'aria-label': 'Seed', placeholder: 'Use a winning seed',
              value: seedText,
              onChange: function (e) { setSeedText(e.target.value) }
            })
          ),
          h('label', { className: 'check' },
            h('input', {
              type: 'checkbox', 'aria-label': 'Lock this seed', checked: seedLocked,
              onChange: function (e) { setSeedLocked(e.target.checked) }
            }),
            'Lock this seed'
          )
        ),
        h('p', { className: 'mut' }, seedLocked
          ? (asSeed(seedText) != null ? 'Every take from here reuses this seed, so one prompt can be A/B tested honestly.' : 'Type a whole number to lock it; until then a fresh seed is drawn each take.')
          : 'A fresh seed is drawn each take. Generate once, then lock the seed to repeat a sound.'),
        h('div', { className: 'c-field' },
          h('label', { htmlFor: 'ms-preset-name' }, 'Preset name'),
          h('input', {
            id: 'ms-preset-name', type: 'text', 'aria-label': 'Preset name', placeholder: 'Warm analog ballad',
            value: presetName,
            onChange: function (e) { setPresetName(e.target.value) }
          })
        ),
        h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          h('button', { type: 'button', className: 'c-btn', disabled: !!busy || !String(presetName).trim(), onClick: savePreset }, 'Save preset'),
          presets.length
            ? h('select', {
              'aria-label': 'Apply preset', value: presetId,
              style: { height: '26px', border: '1px solid var(--line-2)', borderRadius: '4px', background: 'var(--bg)', color: 'var(--ink-2)', fontSize: '11px' },
              onChange: function (e) { applyPreset(e.target.value) }
            }, [h('option', { key: 'none', value: '' }, 'Choose a preset…')].concat(presets.map(function (p) {
              return h('option', { key: presetKey(p), value: presetKey(p) }, p.name || presetKey(p))
            })))
            : null,
          presetId ? h('button', { type: 'button', className: 'c-btn', disabled: !!busy, onClick: deletePreset }, 'Delete preset') : null
        ),
        presetsNote ? h('p', { className: 'mut' }, presetsNote) : null,
        h('div', { style: { borderTop: '1px solid var(--line)', paddingTop: '8px', display: 'flex', flexDirection: 'column', gap: '6px' } },
          h('div', { className: 'c-micro' }, 'Batch'),
          batchControls
        )
      )
      : null

    var comparePanel = comparing && finished.length >= 2
      ? h('div', { className: 'cmpwrap', role: 'group', 'aria-label': 'A/B compare' },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
          h('strong', { style: { fontSize: '12px' } }, 'A/B compare'),
          h(Badge, null, finished.length + ' finished takes')
        ),
        h('p', { className: 'mut' }, 'Each take kept its own snapshot of the song. Play them side by side; the keeper marker stays in this browser.'),
        h('div', { className: 'cmpcol' }, finished.map(function (take) {
          var index = projTakes.indexOf(take)
          var seedShown = takeSeed(take)
          return h('div', { key: take.take_id, className: 'cmpitem' },
            h('div', { style: { display: 'flex', justifyContent: 'space-between' } },
              h('b', null, 'Take ' + (index + 1)),
              h('span', { className: 'ms-mono' }, 'Seed ' + (seedShown == null ? '—' : seedShown))
            ),
            snapshotLines(take).map(function (line, j) {
              return h('span', { key: j, className: 'ms-mono' }, line)
            }),
            h(OnePlayer, { projectId: selected.project_id, takeId: take.take_id, editId: take.playable_edit_id, n: index + 1 }),
            h('div', { style: { display: 'flex', gap: '4px', flexWrap: 'wrap' } },
              h('button', {
                type: 'button', className: 'c-btn sm',
                'aria-pressed': keptTakeId === take.take_id,
                onClick: function () { setKeptTakeId(take.take_id) }
              }, keptTakeId === take.take_id ? 'Kept ✓' : 'Keep this one'),
              seedShown != null
                ? h('button', {
                  type: 'button', className: 'c-btn sm',
                  onClick: function () {
                    setSeedText(String(seedShown))
                    setSeedLocked(true)
                    setNotice('Seed ' + seedShown + ' is locked for the next take.')
                  }
                }, 'Use seed ' + seedShown)
                : null
            )
          )
        }))
      )
      : h('div', { className: 'ms-empty' }, 'Two finished takes are needed to compare. Generate another take first.')

    var takeHeader = activeT
      ? h('div', null,
        h('div', { className: 'thead' },
          h('b', null, activeLabel),
          h('span', null, String(activeT.state)),
          h('span', { style: { marginLeft: 'auto' } }, short(takeDuration(activeT)))
        ),
        h('div', { className: 'seedrow' },
          h('span', null, 'seed ' + (takeSeed(activeT) == null ? '—' : takeSeed(activeT))),
          h('span', { className: 'latch' }, activeT.seed_locked ? 'locked' : 'free'),
          h('span', { style: { marginLeft: 'auto' } }, activeT ? (engineLabel(activeT.engine_id) || activeT.engine_id || 'engine —') : 'engine —')
        ),
        honestLength(activeT) ? h('div', { className: 'lang' }, honestLength(activeT)) : null,
        h('div', { className: 'facts' },
          h('div', { className: 'fact' }, h('span', null, 'BPM'), h('b', null, activeT.snapshot && activeT.snapshot.bpm ? activeT.snapshot.bpm : (selected.bpm || '—'))),
          h('div', { className: 'fact' }, h('span', null, 'Key'), h('b', null, activeT.snapshot && activeT.snapshot.keyscale ? activeT.snapshot.keyscale : (selected.keyscale || '—'))),
          h('div', { className: 'fact' }, h('span', null, 'Language'), h('b', null, activeT.snapshot && activeT.snapshot.vocal_language ? activeT.snapshot.vocal_language : (selected.vocal_language || '—'))),
          h('div', { className: 'fact' }, h('span', null, 'Length'), h('b', null, short(takeDuration(activeT)))),
          h('div', { className: 'fact' }, h('span', null, 'Started'), h('b', null, activeT.started_at ? String(activeT.started_at).slice(11, 19) : '—')),
          h('div', { className: 'fact' }, h('span', null, 'Finished'), h('b', null, activeT.finished_at ? String(activeT.finished_at).slice(11, 19) : '—'))
        ),
        activeT.qc ? h('p', { className: 'mut' },
          'QC: peak ' + (activeT.qc.peak_dbfs == null ? '—' : activeT.qc.peak_dbfs + ' dBFS') +
          ' · tail ' + (activeT.qc.tail_silence_s == null ? '—' : activeT.qc.tail_silence_s + ' s') +
          ' · file ' + (activeT.qc.file_s == null ? '—' : short(activeT.qc.file_s))
        ) : null
      )
      : h('p', { className: 'mut' }, 'No take selected. Generate the first one.')

    var draftForm = !selected ? null : h('div', { style: { display: 'flex', flexDirection: 'column', gap: '9px' } },
      h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
        h('span', { className: 'c-micro' }, renaming ? 'Rename project' : 'Draft'),
        h(Badge, { tone: selected.approved ? 'ok' : 'warn' }, selected.approved ? 'approved' : 'not approved'),
        h('span', { className: 'c-mono', style: { marginLeft: 'auto', color: 'var(--ink-3)', fontSize: '9px' } }, String(selected.project_id).slice(0, 8))
      ),
      renaming
        ? h('div', { style: { display: 'flex', gap: '5px', alignItems: 'flex-end' } },
          h('div', { className: 'c-field', style: { flex: '1 1 auto' } },
            h('label', { htmlFor: 'ms-project-title' }, 'Project title'),
            h('input', {
              id: 'ms-project-title', type: 'text', 'aria-label': 'Project title', value: renameValue,
              maxLength: 200,
              onChange: function (e) { setRenameValue(e.target.value) }
            })
          ),
          h('button', { type: 'button', className: 'c-btn', disabled: !!busy, onClick: renameProject }, 'Save title'),
          h('button', { type: 'button', className: 'c-btn ghost', disabled: !!busy, onClick: function () { setRenaming(false) } }, 'Cancel rename')
        )
        : h('div', { style: { display: 'flex', gap: '4px' } },
          h('button', { type: 'button', className: 'c-btn sm', disabled: !!busy, onClick: startRename }, 'Rename'),
          h('button', { type: 'button', className: 'c-btn sm', disabled: !!busy, onClick: function () { setConfirmDelete(true) } }, 'Delete project')
        ),
      confirmDelete
        ? h('div', { className: 'ms-dialog', role: 'alertdialog', 'aria-label': 'Delete project' },
          h('strong', null, 'Delete “' + ((selected.title && selected.title.trim()) || 'Untitled song') + '”?'),
          h('span', { className: 'mut' }, 'This permanently deletes the project and its WAV files. This cannot be undone.'),
          h('div', { style: { display: 'flex', gap: '5px' } },
            h('button', { type: 'button', className: 'c-btn', disabled: !!busy, onClick: deleteProject }, 'Delete permanently'),
            h('button', { type: 'button', className: 'c-btn ghost', disabled: !!busy, onClick: function () { setConfirmDelete(false) } }, 'Cancel delete')
          )
        )
        : null,
      h('div', { className: 'c-field' },
        h('label', { htmlFor: 'ms-title' }, 'Title'),
        h('input', {
          id: 'ms-title', type: 'text', value: draft.title, maxLength: 200,
          placeholder: 'Midnight Signal',
          onChange: function (e) { setField('title', e.target.value) }
        })
      ),
      h('div', { className: 'c-field' },
        h('label', { htmlFor: 'ms-idea' }, 'Idea (notes for you)'),
        h('textarea', {
          id: 'ms-idea', value: draft.idea, rows: 2,
          placeholder: 'What the song is about',
          onChange: function (e) { setField('idea', e.target.value) }
        })
      ),
      h('div', { className: 'c-field' },
        h('label', { htmlFor: 'ms-caption' }, 'Style / caption (required)'),
        h('textarea', {
          id: 'ms-caption', value: draft.caption, rows: 2,
          placeholder: 'genre, instrumentation, mood, tempo…',
          onChange: function (e) { setField('caption', e.target.value) }
        })
      ),
      h('div', { className: 'c-field' },
        h('label', { htmlFor: 'ms-lyrics' }, 'Lyrics (required)'),
        h('textarea', {
          id: 'ms-lyrics', className: 'words', value: draft.lyrics,
          placeholder: '[verse]\n…',
          onChange: function (e) { setField('lyrics', e.target.value) }
        })
      ),
      h('div', { className: 'c-field' },
        (function () {
          // The cap belongs to the engine that will render the take: MiniMax
          // accepts 10-360 s, ACE 10-180 s. Top-level limits are the default
          // engine's, not a ceiling for every engine.
          var durLimits = limits
          for (var i = 0; i < engines.length; i++) {
            if (engines[i].id === engineId && engines[i].limits) durLimits = engines[i].limits
          }
          var durMin = durLimits ? durLimits.min_seconds : 10
          var durMax = durLimits ? durLimits.max_seconds : 180
          return [
            h('label', { htmlFor: 'ms-dur' }, 'Duration — ' + durMin + ' to ' + durMax + ' seconds'),
            h('div', { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
              h('input', {
                type: 'range', min: durMin, max: durMax, step: 1,
                value: Math.min(durMax, Math.max(durMin, Number(draft.duration_seconds) || 30)),
                onChange: function (e) { setField('duration_seconds', parseInt(e.target.value, 10)) }
              }),
              h('input', {
                id: 'ms-dur', type: 'number', min: durMin, max: durMax, step: 1,
                value: draft.duration_seconds,
                style: { width: '64px' },
                onChange: function (e) { editNumber('duration_seconds', e.target.value, 30) }
              })
            )
          ]
        })()
      ),
      h('div', { style: { display: 'flex', gap: '6px', alignItems: 'center' } },
        h('button', {
          type: 'button', className: 'c-btn', disabled: !!busy,
          'aria-expanded': advanced, 'aria-controls': 'ms-advanced',
          onClick: function () { setAdvanced(function (a) { return !a }) }
        }, advanced ? 'Simple view' : 'Advanced options'),
        h('span', { className: 'mut' }, advanced ? 'Language, tempo, key, seed, presets.' : 'Just the words and the sound.')
      ),
      advancedPanel,
      h('div', { style: { display: 'flex', gap: '5px', flexWrap: 'wrap' } },
        h('button', { type: 'button', className: 'c-btn primary', disabled: !canSave, onClick: saveProject },
          busy === 'save' ? 'Saving…' : 'Save changes'),
        h('button', { type: 'button', className: 'c-btn', disabled: !canApprove, onClick: approveProject },
          busy === 'approve' ? 'Approving…' : 'Approve'),
        h('button', { type: 'button', className: 'c-btn ghost', disabled: !!busy, onClick: reload }, 'Refresh')
      ),
      dirty ? h('div', { className: 'mut' }, 'Unsaved edits — save before approving.') : null,
      !selected.approved && !dirty ? h('div', { className: 'mut' }, 'Caption and lyrics must be non-empty, duration within the engine limits, then approve.') : null
    )

    var snapshotBody = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } },
      takeHeader,
      selected ? draftForm : null,
      comparing ? comparePanel : null,
      stuck
        ? h('div', { className: 'stuck' },
          h('span', null, 'Take ' + (projTakes.indexOf(stuck) + 1) + ' is not advancing: it has reported no new progress for longer than the time it said it had left.'),
          h('button', { type: 'button', className: 'c-btn sm', disabled: !!busy, onClick: recover },
            busy === 'recover' ? 'Recovering…' : 'Recover stuck take')
        )
        : null
    )

    var inspectorBody
    if (activeTab === 'compare') inspectorBody = comparePanel
    else if (activeTab === 'batch') inspectorBody = batchControls
    else inspectorBody = snapshotBody

    var inspector = h('aside', { className: 'insp', 'aria-label': 'Inspector' },
      h('div', { className: 'itabs', role: 'tablist' },
        ['snapshot', 'compare', 'batch'].map(function (tab) {
          return h('button', {
            key: tab, type: 'button', role: 'tab', className: activeTab === tab ? 'on' : '',
            'aria-selected': activeTab === tab,
            onClick: function () { setActiveTab(tab) }
          }, tab === 'snapshot' ? 'Snapshot' : tab === 'compare' ? 'Compare' : 'Batch')
        })
      ),
      h('div', { className: 'ibody', role: 'tabpanel' }, inspectorBody),
      h('div', { className: 'ifoot' },
        h('span', null, activeT ? activeLabel + ' · immutable' : 'No take'),
        h('button', {
          type: 'button', className: 'c-btn sm', style: { marginLeft: 'auto' },
          disabled: !!busy || !activeT || activeT.state !== 'succeeded' || !canExtendParent,
          onClick: submitExtend,
          title: extendBlockedReason || 'Append 8 s as a new take'
        }, busy === 'extend' ? 'Extending…' : 'Extend')
      )
    )

    var requestLine = null
    if (range) {
      requestLine = '→ repaint ' + clock(range.start) + '–' + clock(range.end) +
        ' · seed: new (server-drawn) · snapshot ' +
        activeLabel + ' · → new take · request preview · not sent'
    }

    var actionBar = range && activeT && activeT.state === 'succeeded'
      ? h('div', { className: 'actionbar', role: 'group', 'aria-label': 'Repaint range' },
        h('span', { className: 'verb' }, 'Repaint range'),
        h('span', { className: 'ro' }, clock(range.start) + '–' + clock(range.end) + ' · ' + (range.end - range.start).toFixed(1) + ' s'),
        h('span', { className: 'seed' }, 'seed: new (server-drawn)'),
        h('span', { className: 'grow' }),
        h('button', { type: 'button', className: 'c-btn primary', disabled: !!busy || !canRepaintParent, onClick: submitRepaint, title: repaintBlockedReason || 'Repaint this range as a new take' },
          busy === 'repaint' ? 'Queuing…' : 'Redo this section'),
        h('button', { type: 'button', className: 'c-btn', disabled: !!busy || !canExtendParent, onClick: submitExtend, title: extendBlockedReason || 'Append 8 s as a new take' }, 'Extend'),
        h('button', { type: 'button', className: 'c-btn ghost', onClick: function () { setRange(null) }, 'aria-label': 'Clear range' }, '×')
      )
      : null

    // The parent engine is the one that would render the child. Say so where the
    // action is, not only after the server refuses it.
    var capabilityNotice = (range || (activeT && activeT.state === 'succeeded'))
      ? (repaintBlockedReason || extendBlockedReason)
        ? h('div', { className: 'warn', role: 'status' }, repaintBlockedReason || extendBlockedReason)
        : null
      : null

    var engineBlock = null
    if (engines.length) {
      engineBlock = h('div', { className: 'eng' },
        h('select', {
          'aria-label': 'Engine', value: engineId || defaultEngineId || '',
          onChange: function (e) { setEngineId(e.target.value) },
          title: 'Choose the engine that renders the next take. The server refuses an unavailable choice instead of substituting one.'
        }, engines.map(function (e) {
          var detail = e.selectable_reason
            || (e.status && (e.status.detail || e.status.state))
            || 'unavailable'
          var selectable = e.available && e.supports_generate !== false
          var text = e.label + (selectable
            ? (e.id === defaultEngineId ? ' — default' : '')
            : ' — Unavailable: ' + detail)
          return h('option', {
            key: e.id, value: e.id,
            disabled: !selectable,
            title: selectable ? e.label : detail
          }, text)
        }))
      )
    } else {
      engineBlock = h(Badge, { tone: engineOk === false ? 'bad' : '' }, engineOk === null ? 'engine: unknown' : engineOk ? 'engine ready' : 'engine unavailable')
    }

    var stageHeader = h('div', { className: 'chead' },
      h('div', { style: { minWidth: 0 } },
        h('div', { className: 'c-micro' }, 'Project · ' + (activeT ? 'take ' + pad2(activeIndex + 1) : 'no take')),
        h('h1', null, selected && selected.title && selected.title.trim() ? selected.title : selected ? '(untitled)' : TITLE)
      ),
      h('div', { className: 'sub' }, selected
        ? (selected.approved ? 'approved' : 'draft') + ' · ' + projTakes.length + ' take' + (projTakes.length === 1 ? '' : 's')
        : ''),
      h('div', { className: 'pills' },
        selected && selected.bpm ? h('span', { className: 'pill' }, selected.bpm + ' bpm') : null,
        selected && selected.keyscale ? h('span', { className: 'pill' }, selected.keyscale) : null,
        selected && selected.vocal_language ? h('span', { className: 'pill' }, selected.vocal_language) : null,
        engineId ? h('span', { className: 'pill' }, engineId) : null
      )
    )

    var timeline = selected
      ? h('div', { className: 'timeline' },
        h('div', { className: 'ruler', 'aria-hidden': 'true' }, rulerTicks),
        h('div', { className: 'band' }, sectionCards),
        wavebox,
        h('div', { className: 'bigfoot' },
          h('span', null, 'SECTION ', h('b', null, selectedSection ? selectedSection.label : '—')),
          h('span', null, 'RANGE ', h('b', null, range ? clock(range.start) + '–' + clock(range.end) : '—')),
          h('span', { className: 'r' },
            h('span', null, selectedSection ? (selectedSection.lyric_lines || []).length + ' lines' : sections.length + ' sections'),
            h('span', null, selectedSection ? (selectedSection.end_s - selectedSection.start_s).toFixed(1) + ' s' : duration ? short(duration) : '—')
          )
        )
      )
      : null

    var emptyStage = !selected
      ? h('div', { className: 'ms-empty' }, loading ? 'Loading…' : 'Select a project on the left, or create a new one.')
      : null

    var stage = h('main', { className: 'cstage', style: range ? { paddingBottom: '64px' } : null },
      stageHeader,
      selected ? timeline : emptyStage,
      selected ? sectionEditor : null,
      selected ? ledger : null,
      selected ? takeStripHeader : null,
      selected ? takeStrip : null,
      selected && audio ? h('div', { style: { flex: '0 0 auto' } },
        h('audio', {
          ref: audioElRef, className: 'ms-audio', src: audio.url, controls: true, autoPlay: true,
          onPlay: function () { setPlaying(true) },
          onPause: function () { setPlaying(false) },
          onEnded: function () { setPlaying(false) },
          onTimeUpdate: function (e) { setPlayhead(e.currentTarget.currentTime || 0) },
          onError: function () { setError('The browser could not decode the returned WAV audio.') }
        })
      ) : null
    )

    var rail = h('aside', { className: 'rail', 'aria-label': 'Project rail' },
      h('div', { className: 'grp' },
        h('header', null, h('span', { className: 'c-micro' }, 'Projects'), h('span', { className: 'n' }, String(projects.length))),
        projectList,
        h('button', { type: 'button', className: 'c-btn sm', disabled: !!busy, onClick: reload, style: { marginTop: '6px' } }, 'Reload')
      ),
      h('div', { className: 'grp' },
        h('header', null, h('span', { className: 'c-micro' }, 'Sections'), h('span', { className: 'n' }, String(sections.length))),
        railSections
      ),
      h('div', { className: 'grp' },
        h('header', null, h('span', { className: 'c-micro' }, 'Takes'), h('span', { className: 'n' }, String(projTakes.length))),
        railTakes
      ),
      h('div', { className: 'railfoot' }, 'One GPU job at a time.', h('br'), 'Takes are immutable.')
    )

    var statusLine = (engineReady === false || engineOk === false)
      ? h('span', { className: 'warn' }, 'Engine unavailable — drafts and edits still work.' + (engineReason ? ' ' + engineReason : ''))
      : h('span', null, chosenEngine ? chosenEngine.label + ' · ' + ((chosenEngine.status && chosenEngine.status.state) || 'unknown') : 'Engine status unknown')

    return h('div', { className: 'console' },
      h('header', { className: 'titlebar' },
        h('div', { className: 'wordmark' }, h('i'), TITLE),
        h('div', { className: 'crumbs' },
          h('b', null, selected ? (selected.title || '(untitled)') : 'No project'),
          h('span', null, selected ? projTakes.length + ' takes · ' + sections.length + ' sections' : '')
        ),
        h('div', { className: 'mode', role: 'group', 'aria-label': 'Studio mode' },
          h('button', { type: 'button', disabled: true }, 'Canvas'),
          h('button', { type: 'button', className: 'on', 'aria-pressed': 'true' }, 'Console'),
          h('button', { type: 'button', disabled: true }, 'Timeline Pro')
        ),
        h('div', { className: 'tb-right' },
          h('button', { type: 'button', className: 'c-btn primary', disabled: busy === 'create', onClick: createProject },
            busy === 'create' ? 'Creating…' : 'New project')
        )
      ),
      h('div', { className: 'transport' },
        h('button', {
          type: 'button', className: 'play', 'aria-label': 'Play or pause',
          title: 'Play / pause (Space)', disabled: !activeT || activeT.state !== 'succeeded', onClick: togglePlay
        }, playing ? '❚❚' : '▶'),
        h('div', { className: 'tc' },
          h('span', { className: 'now' }, clock(playhead)),
          h('span', { className: 'of' }, '/ ' + (duration ? clock(duration) : '--:--.-'))
        ),
        h('div', { className: 'tmeta' },
          h('span', null, h('b', null, selected && selected.bpm ? selected.bpm : '—'), ' BPM'),
          h('span', null, h('b', null, selected && selected.keyscale ? selected.keyscale : '—')),
          h('span', null, h('b', null, activeT ? 'T' + pad2(activeIndex + 1) : '—'))
        ),
        h('div', { className: 'grow' }),
        h('div', { className: 'mode', role: 'group', 'aria-label': 'Audition' },
          h('button', { type: 'button', 'aria-pressed': audition === 'A', className: audition === 'A' ? 'on' : '', 'aria-label': 'Audition A', onClick: function () { auditionSlot('A') } }, 'A'),
          h('button', { type: 'button', 'aria-pressed': audition === 'B', className: audition === 'B' ? 'on' : '', 'aria-label': 'Audition B', onClick: function () { auditionSlot('B') } }, 'B')
        ),
        engineBlock,
        h('button', { type: 'button', className: 'c-btn primary', disabled: !canGenerate, onClick: generate },
          busy === 'generate' ? 'Queuing…' : (running ? 'Generating…' : 'Generate'))
      ),
      h('div', { className: 'body' }, rail, stage, inspector),
      actionBar,
      capabilityNotice,
      requestLine ? h('div', { className: 'req', 'aria-label': 'Request preview' }, requestLine) : null,
      h('footer', { className: 'statusbar' },
        statusLine,
        h('span', { className: 'r' },
          h('span', null, activeT ? activeLabel + ' · ' + short(takeDuration(activeT)) : 'no take'),
          h('span', null, projTakes.length + ' takes')
        )
      ),
      conflict
        ? h('div', { className: 'ms-error', role: 'alert', style: { position: 'absolute', left: '250px', right: '336px', top: '104px', zIndex: 4 } },
          h('div', null, conflict),
          h('button', { type: 'button', className: 'c-btn sm', disabled: !!busy, onClick: reloadDraft }, 'Reload draft'))
        : null,
      error ? h('div', { className: 'ms-error', role: 'alert', style: { position: 'absolute', left: '250px', right: '336px', bottom: '6px', zIndex: 4 } }, error) : null,
      notice ? h('div', { className: 'ms-notice', role: 'status', style: { position: 'absolute', left: '250px', right: '336px', bottom: '6px', zIndex: 3 } }, notice) : null,
      h('p', { className: 'ms-live', role: 'status', 'aria-live': 'polite', style: { position: 'absolute', left: '-9999px', width: '1px', height: '1px', overflow: 'hidden' } }, liveText)
    )
  }

  window.__HERMES_PLUGINS__.register(PLUGIN, App)
})()
