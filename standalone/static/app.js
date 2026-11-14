'use strict';
const $ = id => document.getElementById(id);
const STUDIO = (function () {
  const hit = /^(.*?)\/studio\/(?:ui|assets\/(?:index\.html|app\.js|style\.css))\/?$/.exec(location.pathname);
  const safe = /^\/[A-Za-z0-9\-._~%!$&'()*+,;=:@]+(?:\/[A-Za-z0-9\-._~%!$&'()*+,;=:@]+)*$/;
  const base = hit && safe.test(hit[1]) && !/(^|\/)\.\.?(\/|$)/.test(hit[1]) ? hit[1] : null;
  return base === null ? { plugin: false, api: '/api', assets: '/static' } : { plugin: true, api: base, assets: base + '/studio/assets' };
})();
const brandLink = document.querySelector('a.brand');
if (brandLink && STUDIO.plugin) brandLink.href = STUDIO.api + '/studio/ui';
const form = $('draft-form');
let current = null, dirty = false, busy = false, revision = 0, engines = [], projects = [], workspaces = [], conversation = [], libraryFilter = null, modelCatalog = [], targets = [], targetProbes = {}, hardware = null, hardwareProblem = '', hardwareFetchedAt = 0, refreshing = false, pollTimer = null;
const message = (text, bad = false) => { $('form-message').textContent = text; $('form-message').classList.toggle('error', bad); };
// The host requires a per-launch token on every /api route (review item 3). The pane frames this page
// with it in the query string; a browser tab opened by hand has none, which is the point.
const STUDIO_TOKEN = new URLSearchParams(location.search).get('token') || '';
function withToken(headers) {
  const out = Object.assign({}, headers || {});
  if (STUDIO_TOKEN) out['X-Studio-Token'] = STUDIO_TOKEN;
  return out;
}
// Audio and cover art are loaded by the browser itself (<audio src>, <img src>), which cannot send a
// header — so those URLs carry the token. Without this every take is a 401 in a token-gated host,
// even though the API calls beside it work.
function mediaUrl(url) {
  if (!STUDIO_TOKEN || !url) return url;
  const separator = String(url).includes('?') ? '&' : '?';
  return url + separator + 'token=' + encodeURIComponent(STUDIO_TOKEN);
}
async function api(path, data, method) {
  const opts = method ? {method} : data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)};
  if (method && data !== undefined) { opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(data); }
  opts.headers = withToken(opts.headers);
  const res = await fetch(STUDIO.api + path, opts);
  const value = await res.json();
  if (!res.ok || value.success === false) throw new Error(value.detail || value.error || 'Request failed');
  return value;
}
function buttons() {
  form.querySelector('[type=submit]').disabled = busy;
  $('new-song').disabled = busy;
  $('nav-library').disabled = busy;
  $('nav-workspaces').disabled = busy;
  $('nav-compute').disabled = busy;
  $('nav-listen').disabled = busy;
  $('approve').disabled = busy || !current || dirty || current.approved;
  const engineId = form.elements.engine.value, engine = engines.find(e => e.id === engineId), targetId = form.elements.comfy_target_id.value;
  const ready = engineId === 'minimax_m3_comfy' && targetId ? targetProbes[targetId]?.available === true : !!engine?.available;
  $('render').disabled = busy || !current || dirty || !current.approved || !ready;
  // A chorus preview needs the draft saved, not approved: it renders four lines, and the person
  // hearing it is the approval the full render still waits for.
  if ($('preview-chorus')) $('preview-chorus').disabled = busy || !current || dirty || !ready;
  $('draft-state').textContent = !current ? 'New idea' : dirty ? 'Unsaved changes' : current.approved ? 'Approved' : 'Draft saved';
}
function fields() {
  const engine = form.elements.engine.value;
  return {title:form.elements.title.value.trim(), caption:form.elements.caption.value.trim(), lyrics:form.elements.lyrics.value,
    instrumental:form.elements.instrumental.checked, duration_seconds:Number(form.elements.duration_seconds.value), engine,
    bpm: form.elements.bpm.value ? Number(form.elements.bpm.value) : null,
    keyscale: form.elements.keyscale.value.trim() || null, vocal_language: form.elements.vocal_language.value,
    minimax_guidance: engine === 'minimax_m3_comfy' ? Number(form.elements.minimax_guidance.value) : null,
    comfy_target_id: engine === 'minimax_m3_comfy' ? (form.elements.comfy_target_id.value || null) : null};
}
function loadProject(p) {
  if (dirty && !window.confirm('Discard unsaved changes?')) return;
  current = p;
  for (const key of ['title','caption','lyrics','duration_seconds','engine']) form.elements[key].value = p[key] ?? (key === 'duration_seconds' ? 30 : '');
  if (p.engine === 'ace') form.elements.engine.value = 'ace_local';
  form.elements.instrumental.checked = !!p.instrumental;
  form.elements.bpm.value = p.bpm ?? ''; form.elements.keyscale.value = p.keyscale ?? ''; form.elements.vocal_language.value = p.vocal_language || 'en';
  form.elements.minimax_guidance.value = p.minimax_guidance ?? 1.5; $('minimax-guidance-value').value = form.elements.minimax_guidance.value;
  ensureTargetOption(p.comfy_target_id);
  form.elements.comfy_target_id.value = p.comfy_target_id || '';
  $('workspace-select').value = workspaces.find(w => w.project_ids.includes(p.project_id))?.id || 'default';
  $('cover-input').value='';
  showCover(p.project_id);
  dirty = false; engineHint(); buttons(); show('create'); message('Draft loaded.');
}
function engineHint() {
  const engine = form.elements.engine.value, e = engines.find(x => x.id === engine), isMini = engine === 'minimax_m3_comfy';
  $('minimax-guidance-row').hidden = !isMini;
  $('target-row').hidden = !isMini;
  if (isMini) {
    const targetId = form.elements.comfy_target_id.value;
    if (targetId) {
      const name = targetName(targetId) || targetId, probe = targetProbes[targetId];
      $('engine-hint').textContent = !probe ? `Target ${name} has not been tested; test it before rendering.` : probe.available ? `Target ${name} is available.` : `Target ${name} unavailable${probe.detail ? ': ' + probe.detail : '; test it again.'}`;
      return;
    }
    $('engine-hint').textContent = e ? (e.available ? `${e.label || e.id} default endpoint is ready.` : `${e.label || e.id} default endpoint unavailable: ${e.reason || e.error || 'check setup'}`) : 'Engine status unavailable; check setup.';
    return;
  }
  $('engine-hint').textContent = e ? (e.available ? `${e.label || e.id} is ready.` : `${e.label || e.id} unavailable: ${e.reason || e.error || 'check setup'}`) : 'Engine status unavailable; check setup.';
}
function show(view) {
  for (const name of ['create','library','workspaces','compute','listen']) { $(name+'-view').hidden = view !== name; $('nav-'+name).classList.toggle('active',view === name); }
}
function clock(value) {
  const total = Number.isFinite(value) && value > 0 ? value : 0;
  const minutes = Math.floor(total / 60);
  const seconds = Math.floor(total % 60);
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}
function playerVolume(on) {
  $('player-volume').value = String(on);
  $('player').volume = on;
}
function playerState() {
  const audio = $('player');
  $('player-play').textContent = audio.paused ? '▶' : '❚❚';
  $('player-play').setAttribute('aria-label', audio.paused ? 'Play' : 'Pause');
}
function playerProblem(message) {
  // The player must say when it cannot play: a button that does nothing looks like a broken app.
  const note = $('player-subtitle');
  if (!note) return;
  note.textContent = message;
  note.classList.add('error');
}
let demoCard = null;
async function demoMeta() {
  if (!demoCard) demoCard = await api('/demo');
  return demoCard;
}
function demoPeak(meta) {
  const value = meta && (meta.peak_dbfs ?? (meta.qc && meta.qc.peak_dbfs) ?? meta.peak);
  if (value === undefined || value === null || value === '') return '';
  const peak = Number(value);
  return Number.isFinite(peak) ? `${peak.toFixed(1)} dBFS` : String(value);
}
function demoFacts(meta) {
  const facts = [];
  if (meta && meta.word_recovery) facts.push(`words recovered ${meta.word_recovery}`);
  const peak = demoPeak(meta);
  if (peak) facts.push(`peak ${peak}`);
  return facts.join(' · ');
}
function showDemoFacts(meta) {
  const facts = demoFacts(meta);
  document.querySelectorAll('.demo-facts').forEach(node => { node.textContent = facts; });
}
async function playDemo(event) {
  const button = event && event.currentTarget;
  if (button) button.disabled = true;
  try {
    const meta = await demoMeta();
    showDemoFacts(meta);
    const caption = meta.caption || 'A real take made with this studio.';
    const facts = demoFacts(meta);
    play(STUDIO.api + '/demo/audio', meta.title || 'Demo song', facts ? `${caption} · ${facts}` : caption);
  } catch (err) {
    playerProblem('Could not load the demo: ' + err.message);
  } finally {
    if (button) button.disabled = false;
  }
}
// ---------------------------------------------------------------- waveform strip
// The peaks route already exists server-side; the footer asks it once per take and draws the
// buckets on a small canvas. If it is unavailable the range input is still the whole control.
const PEAK_BUCKETS = 256;
const peakCache = new Map();
let wave = null, waveSequence = 0, waveDragging = false;

function takeRef(url) {
  const hit = /\/projects\/([^/?#]+)\/takes\/([^/?#]+)\//.exec(String(url || ''));
  return hit ? { project: decodeURIComponent(hit[1]), take: decodeURIComponent(hit[2]) } : null;
}
function hideWave() {
  wave = null;
  const canvas = $('player-wave'), track = $('player-track');
  if (canvas) canvas.hidden = true;
  if (track) track.classList.remove('has-wave');
}
function drawWave() {
  const canvas = $('player-wave'), track = $('player-track');
  if (!canvas || !wave || !wave.values || !wave.values.length) return;
  const ctx = canvas.getContext && canvas.getContext('2d');
  if (!ctx) return;
  const width = Math.max(1, Math.round(canvas.clientWidth || (track && track.clientWidth) || 0));
  const height = 48;
  const dpr = Math.max(1, Number(window.devicePixelRatio) || 1);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const styles = getComputedStyle(document.documentElement);
  const accent = styles.getPropertyValue('--accent').trim() || '#d8e89f';
  const muted = styles.getPropertyValue('--muted').trim() || '#999e90';
  const audio = $('player');
  const span = wave.duration || (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0);
  const progress = span > 0 ? Math.max(0, Math.min(1, (audio.currentTime || 0) / span)) : 0;
  const played = progress * width, mid = height / 2, maxAmp = height / 2 - 3;
  const step = width / wave.values.length;
  for (let i = 0; i < wave.values.length; i++) {
    const bucket = wave.values[i] || {};
    const amp = Math.max(Math.abs(Number(bucket.max) || 0), Math.abs(Number(bucket.min) || 0));
    const bar = Math.max(1, Math.min(maxAmp, amp * maxAmp));
    const x = i * step;
    ctx.fillStyle = x < played ? accent : muted;
    ctx.fillRect(x, mid - bar, Math.max(1, step - 0.5), bar * 2);
  }
  ctx.fillStyle = accent;
  ctx.fillRect(Math.max(0, Math.min(width - 1, played)), 0, 1, height);
}
function showWave() {
  const canvas = $('player-wave'), track = $('player-track');
  if (!canvas || !track || !wave || !wave.values || !wave.values.length) return;
  canvas.hidden = false;
  track.classList.add('has-wave');
  drawWave();
}
function seekWave(event) {
  const canvas = $('player-wave'), audio = $('player');
  if (!canvas || !wave) return;
  const span = wave.duration || (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0);
  if (!span) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, ((event.clientX || 0) - (rect.left || 0)) / (rect.width || canvas.clientWidth || 1)));
  audio.currentTime = ratio * span;
  const seek = $('player-seek');
  if (Number(seek.max) < span) seek.max = String(span);
  seek.value = String(audio.currentTime);
  $('player-elapsed').textContent = clock(audio.currentTime);
  drawWave();
}
function setWaveTake(url, editId) {
  const ref = takeRef(url);
  const sequence = ++waveSequence;
  hideWave();
  if (!ref) return;
  const key = `${ref.project}/${ref.take}${editId ? '@' + editId : ''}`;
  const cached = peakCache.get(key);
  if (cached) { wave = { key, values: cached.values, duration: cached.duration }; showWave(); return; }
  const query = `?buckets=${PEAK_BUCKETS}` + (editId ? '&edit=' + encodeURIComponent(editId) : '');
  api(`/projects/${encodeURIComponent(ref.project)}/takes/${encodeURIComponent(ref.take)}/peaks${query}`)
    .then(data => {
      if (sequence !== waveSequence) return;
      if (!Array.isArray(data.peaks) || !data.peaks.length) return;
      const entry = { values: data.peaks, duration: Number(data.duration_seconds) || 0 };
      peakCache.set(key, entry);
      wave = { key, values: entry.values, duration: entry.duration };
      showWave();
    })
    .catch(() => { /* the plain seek bar is the fallback, not an error screen */ });
}
function play(url, title, detail, editId) {
  const audio = $('player');
  audio.src = mediaUrl(url);
  audio.load();
  $('player-title').textContent = title;
  $('player-subtitle').textContent = detail;
  $('player-subtitle').classList.remove('error');
  $('player-seek').value = '0';
  $('player-seek').max = '0';
  $('player-elapsed').textContent = '0:00';
  $('player-total').textContent = '0:00';
  setWaveTake(url, editId);
  audio.play().catch(error => playerProblem(
    error && error.name === 'NotAllowedError'
      ? 'Press play once — the browser will not start audio on its own.'
      : 'Could not start playback: ' + (error && error.message ? error.message : 'unknown reason')
  ));
}
function wirePlayer() {
  const audio = $('player');
  let resumeAt = null;
  $('player-play').addEventListener('click', () => {
    if (!audio.src) return;
    if (audio.paused) audio.play().catch(() => {}); else audio.pause();
  });
  $('player-back').addEventListener('click', () => { audio.currentTime = Math.max(0, (audio.currentTime || 0) - 10); });
  $('player-forward').addEventListener('click', () => {
    const end = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : Infinity;
    audio.currentTime = Math.min(end, (audio.currentTime || 0) + 10);
  });
  $('player-seek').addEventListener('input', event => {
    const at = Number(event.target.value);
    if (Number.isFinite(at)) audio.currentTime = at;
    $('player-elapsed').textContent = clock(audio.currentTime);
    drawWave();
  });
  $('player-volume').addEventListener('input', event => playerVolume(Number(event.target.value)));
  // The canvas is a seek surface, but the range input stays the keyboard control. Pointer capture
  // keeps a drag alive when the pointer leaves the 48px strip.
  const waveCanvas = $('player-wave');
  if (waveCanvas) {
    waveCanvas.addEventListener('pointerdown', event => {
      if (!wave) return;
      waveDragging = true;
      if (waveCanvas.setPointerCapture) { try { waveCanvas.setPointerCapture(event.pointerId); } catch (err) { /* not every engine */ } }
      seekWave(event);
      event.preventDefault();
    });
    waveCanvas.addEventListener('pointermove', event => { if (waveDragging) seekWave(event); });
    const endWaveDrag = () => { waveDragging = false; };
    waveCanvas.addEventListener('pointerup', endWaveDrag);
    waveCanvas.addEventListener('pointercancel', endWaveDrag);
    window.addEventListener('pointerup', endWaveDrag);
  }
  window.addEventListener('resize', drawWave);
  if (window.matchMedia) {
    try {
      const scheme = window.matchMedia('(prefers-color-scheme: dark)');
      if (scheme.addEventListener) scheme.addEventListener('change', drawWave);
      else if (scheme.addListener) scheme.addListener(drawWave);
    } catch (err) { /* a theme query is an enhancement */ }
  }
  if (window.MutationObserver) {
    new MutationObserver(drawWave).observe(document.documentElement,
      { attributes: true, attributeFilter: ['class', 'data-theme', 'style'] });
  }
  // The scrubber's range comes from the take's length. When a server streams without saying how
  // long the file is, a media element reports Infinity and the range has nothing to drag — so ask
  // the browser to resolve it, and if it cannot, say so instead of showing a dead bar.
  function syncSpan() {
    const seek = $('player-seek');
    if (Number.isFinite(audio.duration) && audio.duration > 0) {
      seek.max = String(audio.duration);
      seek.disabled = false;
      seek.title = 'Drag to move through the take';
      $('player-total').textContent = clock(audio.duration);
      return true;
    }
    seek.max = '0';
    seek.disabled = true;
    seek.title = 'This take has no known length yet — the position still counts while it plays';
    $('player-total').textContent = '--:--';
    if (audio.duration === Infinity) {
      // The classic trick: ask for an absurd position, and the browser reports the real length.
      resumeAt = audio.currentTime || 0;
      try { audio.currentTime = 1e101; } catch (err) { /* not every engine allows it */ }
    }
    return false;
  }
  audio.addEventListener('loadedmetadata', syncSpan);
  audio.addEventListener('durationchange', () => {
    if (!Number.isFinite(audio.duration)) return;
    syncSpan();
    // The oversized seek above parked the element at the end; put it back where it was.
    if (resumeAt !== null) { audio.currentTime = Math.min(resumeAt, audio.duration); resumeAt = null; }
  });
  audio.addEventListener('timeupdate', () => {
    if (!document.activeElement || document.activeElement.id !== 'player-seek') $('player-seek').value = String(audio.currentTime || 0);
    $('player-elapsed').textContent = clock(audio.currentTime);
    drawWave();
  });
  // A media element fires 'error' when the bytes cannot be fetched or decoded; silence there is
  // exactly the dead-button symptom this product keeps being judged on.
  audio.addEventListener('error', () => {
    const code = audio.error && audio.error.code;
    playerProblem(code === 4
      ? 'This take could not be played by the browser (unsupported or corrupt audio).'
      : 'The audio file could not be loaded.');
  });
  audio.addEventListener('play', playerState);
  audio.addEventListener('pause', playerState);
  audio.addEventListener('ended', () => { playerState(); $('player-elapsed').textContent = clock(audio.duration); });
  playerVolume(Number($('player-volume').value || 1));
  playerState();
}
async function showVersion(){
  // One source: the server's own stamp (BUILD_INFO.json when installed, live git in a checkout).
  // The server builds the line with version_stamp.chip_line, the same text every suite plugin shows
  // (VERSION-CHIP.md): "v1.0.0-dev.342 · 1a2b3c4d · 2026-11-13".
  const chip = $('app-version');
  if (!chip) return;
  let build;
  try {
    const data = await api('/about');
    build = data.build || { version: data.version };
  } catch (err) { return; }            // no guess beats a wrong number
  let line = build.line;
  if (!line) {
    const parts = ['v' + (build.version || 'unknown')];
    if (build.commit_short || build.commit) parts.push((build.commit_short || build.commit).slice(0, 8));
    if (build.commit_date) parts.push(String(build.commit_date).slice(0, 10));
    if (build.dirty) parts.push('modified');
    line = parts.join(' · ');
  }
  chip.hidden = false;
  chip.dataset.line = line;
  if (!chip.dataset.copied) chip.textContent = line;
  chip.classList.toggle('dirty', !!build.dirty);
  chip.setAttribute('aria-label', 'Copy version ' + line);
  chip.title = [
    'Hermes Music Studio ' + (build.version || 'unknown'),
    build.commit ? 'commit ' + build.commit : '',
    build.commit_date ? 'committed ' + build.commit_date : '',
    build.built_at ? 'installed ' + build.built_at : '',
    build.channel ? 'channel ' + build.channel : '',
    build.source ? 'read from ' + build.source : '',
    'Click to copy'
  ].filter(Boolean).join('\n');
  chip.onclick = async () => {
    // Say "copied" only when something was copied: a badge that lies is worse than no badge.
    const copied = await copyText('Hermes Music Studio ' + chip.dataset.line);
    chip.dataset.copied = copied ? '1' : 'failed';
    chip.textContent = copied ? 'copied ✓' : 'copy failed';
    setTimeout(() => { delete chip.dataset.copied; chip.textContent = chip.dataset.line; }, 1400);
  };
}
// Re-read every minute so an update or reload shows without restarting the app.
setInterval(() => { if (!document.hidden) showVersion(); }, 60000);

if ($('player-play')) wirePlayer();
showVersion();
function libMessage(text, bad = false) { const node = $('library-status'); if (!node) return; node.textContent = text; node.classList.toggle('error', bad); }
function el(tag, text, className) { const node = document.createElement(tag); if(text) node.textContent = text; if(className) node.className = className; return node; }
function belongsTo(p,id) { const w=workspaces.find(x=>x.id===id);return !!w && (w.project_ids.includes(p.project_id) || (id==='default' && !workspaces.some(x=>x.project_ids.includes(p.project_id)))); }

// One set of instructions, any engine you have. The chooser lists what is actually registered here,
// because a person who cannot run MiniMax should still be able to compare the two things they do have.
async function startCompare(p, card) {
  let panel = card.querySelector('.compare-panel');
  if (!panel) { panel = el('div', '', 'compare-panel'); card.append(panel); }
  panel.replaceChildren(el('p', 'hint', 'Checking which engines can render…'));

  let engines = [];
  try {
    engines = (await api('/compare/engines')).engines || [];
  } catch (err) {
    panel.replaceChildren(el('p', err.message, 'hint error'));
    return;
  }
  if (!engines.length) {
    panel.replaceChildren(el('p', 'Set up at least two engines in Compute, then compare.',
      'hint error'));
    return;
  }

  const picked = new Set(engines.filter(e => e.ready).map(e => e.engine_id));
  const box = el('div', '', 'compare-picker');
  engines.forEach(engine => {
    const row = el('label', '', 'compare-option');
    const tick = document.createElement('input');
    tick.type = 'checkbox';
    tick.checked = picked.has(engine.engine_id);
    tick.disabled = !engine.ready;
    tick.addEventListener('change', () => {
      if (tick.checked) picked.add(engine.engine_id); else picked.delete(engine.engine_id);
      render.disabled = picked.size < 2;
      render.textContent = picked.size < 2
        ? 'Pick two engines' : `Render ${picked.size} versions`;
    });
    const name = engine.label || engine.engine_id;
    row.append(tick, el('span', engine.ready ? name : `${name} — ${engine.detail || 'not ready'}`,
      engine.ready ? '' : 'hint'));
    box.append(row);
  });

  const render = el('button', picked.size < 2 ? 'Pick two engines' : `Render ${picked.size} versions`,
    'button primary');
  render.type = 'button';
  render.disabled = picked.size < 2;
  panel.replaceChildren(el('p', 'hint', 'Same draft, one render at a time. Pick which engines.'), box, render);

  render.addEventListener('click', async () => {
    render.disabled = true;
    panel.replaceChildren(el('p', 'hint', 'Starting…'));
    try {
      await api('/compare', { project_id: p.project_id, engines: [...picked] });
    } catch (err) {
      panel.replaceChildren(el('p', err.message, 'hint error'));
      return;
    }
    follow(p, panel);
  });
}

// Follow a comparison plan until it settles, then offer each finished take.
async function follow(p, panel) {
  const name = id => {
    const known = (window.__msCompare || {})[id];
    return known || (id === 'minimax_m3_comfy' ? 'MiniMax Music 3'
      : id === 'ace_local' ? 'ACE-Step' : id);
  };
  const tick = async () => {
    let plan;
    try { plan = (await api('/compare/' + encodeURIComponent(p.project_id))).plan; }
    catch (err) { panel.append(el('p', err.message, 'hint error')); return; }
    const rows = plan.engines || [];
    panel.replaceChildren(el('p', 'hint', plan.state === 'running'
      ? 'Comparison running · one render at a time.' : 'Comparison finished.'));
    rows.forEach(r => {
      const row = el('div', '', 'take');
      const bits = [name(r.engine_id), r.state];
      if (r.seconds) bits.push(r.seconds.toFixed(1) + 's');
      if (r.error) bits.push(r.error);
      row.append(el('span', bits.join(' · ')));
      if (r.state === 'succeeded' && r.take_id) {
        const playIt = el('button', '▶ Play'); playIt.type = 'button';
        playIt.addEventListener('click', () => play(
          STUDIO.api + `/projects/${encodeURIComponent(p.project_id)}/takes/${encodeURIComponent(r.take_id)}/audio`,
          p.title || 'Untitled', 'Comparison · ' + name(r.engine_id)));
        const exportIt = el('button', 'Export', 'button secondary'); exportIt.type = 'button';
        exportIt.addEventListener('click', async () => {
          try {
            const res = await api('/export', { project_id: p.project_id, take_id: r.take_id });
            panel.append(el('p', 'Saved to ' + res.path, 'hint'));
          } catch (err) { panel.append(el('p', err.message, 'hint error')); }
        });
        row.append(playIt, exportIt);
      }
      panel.append(row);
    });
    if (plan.state === 'running') { window.setTimeout(tick, 6000); return; }
    await refresh();
    const fresh = document.querySelector(`[data-project="${p.project_id}"]`);
    if (fresh) fresh.append(panel);
  };
  tick();
}

function demoButton() {
  const button = el('button','Hear a demo song','button secondary demo-button');
  button.type = 'button';
  button.addEventListener('click', playDemo);
  return button;
}
function library() {
  const container = $('library'); container.replaceChildren();
  if (!projects.length) {
    const empty = el('div','','library-empty');
    empty.append(el('p','No drafts yet. Create a song to see it here.'));
    const call = el('div','','demo-callout');
    call.append(demoButton(), el('span','','hint demo-facts'));
    const note = el('p','','hint');
    note.append(document.createTextNode('The demo is a real take made with this studio; set up an engine on the '));
    const compute = el('button','Compute','link-button'); compute.type = 'button';
    compute.addEventListener('click', () => show('compute'));
    note.append(compute, document.createTextNode(' page to make your own.'));
    empty.append(call, note);
    container.append(empty);
    return;
  }
  const inScope = projects.filter(p => !libraryFilter || belongsTo(p,libraryFilter));
  const query = (($('library-search') || {}).value || '').trim().toLowerCase();
  const order = (($('library-sort') || {}).value) || 'newest';
  const matches = inScope.filter(p => !query ||
    [p.title, p.caption, p.lyrics, p.idea].some(v => String(v || '').toLowerCase().includes(query)));
  const empty = matches.filter(isEmptyDraft), shown = matches.filter(p => !isEmptyDraft(p));
  shown.sort(LIBRARY_ORDERS[order] || LIBRARY_ORDERS.newest);
  const count = $('library-count');
  if (count) count.textContent = `${shown.length} of ${inScope.length} songs`;
  if (!shown.length) container.append(el('p', query ? 'No songs match.' : 'No songs with words or takes yet.', 'hint'));
  shown.concat(showEmptyDrafts ? empty : []).forEach(p => {
    const card = el('article', '', 'project'), head = el('div','','project-head'), title = el('h2',p.title || 'Untitled');
    title.classList.add('project-title'); title.tabIndex = 0; title.title = 'Open this draft';
    title.addEventListener('click', () => loadProject(p));
    title.addEventListener('keydown', e => { if (e.key === 'Enter') loadProject(p); });
    card.dataset.project = p.project_id;   // so a panel can find this card after a refresh
    const edit = el('button','Open draft','button secondary'); edit.type='button'; edit.addEventListener('click', () => loadProject(p));
    const ver = el('button','3 versions','button secondary'); ver.type='button'; ver.addEventListener('click', async () => {
      ver.disabled = true;
      try { await api('/versions',{project_id:p.project_id,count:3}); libMessage('Rendering 3 versions of this draft. Each take keeps its own seed.'); await refresh(); library(); }
      catch(err){ libMessage(err.message, true); } finally { ver.disabled = false; }
    });
    const cmp = el('button','Compare engines','button secondary'); cmp.type='button';
    cmp.addEventListener('click', () => startCompare(p, card));
    const dup = el('button','Duplicate','button secondary'); dup.type='button'; dup.addEventListener('click', async () => {
      dup.disabled = true;
      try { await api('/duplicate',{project_id:p.project_id}); libMessage(`Copied \u201c${p.title || 'Untitled'}\u201d with its takes.`); await refresh(); library(); }
      catch(err){ libMessage(err.message, true); } finally { dup.disabled = false; }
    });
    const del = el('button','Delete','button secondary'); del.type='button'; del.addEventListener('click', async () => {
      if (!confirm(`Delete \u201c${p.title || 'Untitled'}\u201d and all of its audio? This cannot be undone.`)) return;
      del.disabled = true;
      try { await api('/delete',{project_id:p.project_id}); libMessage('Deleted.'); await refresh(); library(); }
      catch(err){ libMessage(err.message, true); } finally { del.disabled = false; }
    });
    // Plain words → a different draft, from the page and not only from a chat (owner rule: a
    // capability is not done until it is a button). The result is shown, never assumed.
    const words = el('input'); words.type='text'; words.className='change-words';
    words.placeholder='say what to change: add a fiddle, go dark, half-time in the bridge';
    const changeIt = el('button','Change with words','button secondary'); changeIt.type='button';
    changeIt.addEventListener('click', async () => {
      const instruction = (words.value || '').trim();
      if (!instruction) { libMessage('Type what to change first.', true); return; }
      changeIt.disabled = true;
      try {
        const res = await api('/change', {project_id: p.project_id, instruction});
        libMessage('Changed: ' + (res.changed || []).join(' · ') + ' — approval revoked, review the draft.');
        words.value = '';
        await refresh(); library();
      } catch (err) { libMessage(err.message, true); } finally { changeIt.disabled = false; }
    });
    const identity=el('div','','project-identity'); if (p.has_cover !== false) { const cover=document.createElement('img'); cover.className='project-cover'; cover.alt=''; cover.loading='lazy'; cover.src=mediaUrl(STUDIO.api+'/studio/covers/'+encodeURIComponent(p.project_id)); cover.addEventListener('error',()=>cover.remove()); identity.append(cover); } identity.append(title); const actions = el('div','','project-actions'); actions.append(edit, ver, cmp, dup, del); head.append(identity,actions); card.append(head,el('p',p.caption || 'No style description yet.'));
    const changeRow = el('div','','project-change'); changeRow.append(words, changeIt); changeRow.hidden = true; card.append(changeRow);
    const changeToggle = el('button','Change with words…','button secondary'); changeToggle.type='button';
    changeToggle.setAttribute('aria-expanded','false');
    changeToggle.addEventListener('click', () => { changeRow.hidden = !changeRow.hidden;
      changeToggle.setAttribute('aria-expanded', String(!changeRow.hidden)); if (!changeRow.hidden) words.focus(); });
    actions.append(changeToggle);
    const memory = p.memory || {}; const memNotes = (memory.events || []).length;
    if (memNotes || memory.kept_take_id) {
      const lastNote = (memory.events || [])[memNotes - 1] || {};
      card.append(el('p', `${memNotes} note(s) in this song's memory` +
        (memory.kept_take_id ? ` · keeper ${String(memory.kept_take_id).slice(0,8)}` : '') +
        (lastNote.text ? ` · last: ${lastNote.text}` : ''), 'hint'));
    }
    const takes = p.takes || [];
    if (!takes.length) card.append(el('p','No takes yet.'));
    takes.forEach((t, i) => { const row = el('div','','take'); let detail = `Take ${i+1} · ${t.engine_id || p.engine || 'engine'} · ${t.state || 'unknown'} · seed ${t.requested_seed ?? t.seed ?? '—'}`;
      const targetLabel = takeTargetLabel(t);
      if (targetLabel) detail += ` · ${targetLabel}`;
      if (t.state === 'running') detail += ` · estimated ${Number(t.progress) || 0}% · estimated ETA ${formatEta(t.eta_seconds)}`;
      else if (t.state === 'queued') detail += ` · queued${t.queue_position ? ` (#${t.queue_position})` : ''}`;
      const label = el('span', detail); row.append(label);
      const takeCaps = takeCapabilities(t.engine_id || p.engine);
      const engineName = takeCaps ? (takeCaps.engine.label || takeCaps.engine.id)
        : (t.engine_id || p.engine || 'this engine');
      // Section render is a plain generate of the range, so it works on an engine without
      // repaint; only an engine that opts out of generate loses the control.
      const canRepaint = !takeCaps || takeCaps.capabilities.repaint !== false;
      const canExtend = !takeCaps || takeCaps.capabilities.continuation !== false;
      const canSection = !takeCaps || takeCaps.engine.supports_generate !== false;
      const missing = [!canRepaint && 'repaint a range', !canExtend && 'extend this take']
        .filter(Boolean).join(' or ');
      if (t.state === 'succeeded') {
        // Save your spot at any moment — and get back to the last one with one press.
        const saveSpot = el('button','Save spot','button secondary'); saveSpot.type='button';
        saveSpot.addEventListener('click', async () => {
          saveSpot.disabled = true;
          try {
            const res = await api('/checkpoint', {project_id: p.project_id});
            libMessage(`Saved (${res.saved} spot(s)). Keeper kept by ${res.fidelity || 'audio'}.`);
            await refresh(); library();
          } catch (err) { libMessage(err.message, true); } finally { saveSpot.disabled = false; }
        });
        const backSpot = el('button','Back to last spot','button secondary'); backSpot.type='button';
        backSpot.addEventListener('click', async () => {
          backSpot.disabled = true;
          try {
            const list = await api('/checkpoints/' + encodeURIComponent(p.project_id));
            const last = (list.checkpoints || []).slice(-1)[0];
            if (!last) { libMessage('No saved spot yet — press Save spot first.', true); return; }
            const res = await api('/checkpoint/restore', {project_id: p.project_id,
                                                          checkpoint_id: last.checkpoint_id});
            libMessage(`Back to ${res.restored}. The approval was revoked — review the draft.`);
            await refresh(); library();
          } catch (err) { libMessage(err.message, true); } finally { backSpot.disabled = false; }
        });
        row.append(saveSpot, backSpot);
        // Did this take do what was asked? Facts only; the reply names the failing check.
        const check = el('button','Check this take','button secondary'); check.type='button';
        check.addEventListener('click', async () => {
          check.disabled = true;
          try {
            const res = await api('/take-check', {project_id: p.project_id, take_id: t.take_id});
            const failed = (res.checks || []).filter(c => c.ok === false).map(c => c.name);
            libMessage(`${res.state}: ${res.summary}` +
              (failed.length ? ` — press Fix it to re-render with a new seed.` : ''), res.state === 'failed');
          } catch (err) { libMessage(err.message, true); } finally { check.disabled = false; }
        });
        const fix = el('button','Fix it','button secondary'); fix.type='button';
        fix.addEventListener('click', async () => {
          fix.disabled = true;
          try {
            const res = await api('/take-check', {project_id: p.project_id, take_id: t.take_id, retry: true});
            libMessage(res.retried
              ? `Re-rendering with seed ${res.seed}, because: ${(res.because || []).join(', ')}.`
              : `Nothing measurable failed: ${res.reason}.`);
            if (res.retried) { await refresh(); library(); }
          } catch (err) { libMessage(err.message, true); } finally { fix.disabled = false; }
        });
        row.append(check, fix);
        if (canRepaint || canSection) {
          const redo = el('button','Change this part','button secondary'); redo.type='button';
          redo.title = !missing
            ? 'Re-render this range as a new take, then splice it back'
            : `${engineName} cannot ${missing}; this part is rendered on its own and spliced back instead.`;
          redo.addEventListener('click', () => snippetRow(p, t, row,
            {repaint: canRepaint, engine: engineName, missing})); row.append(redo);
        }
        const fade = el('button','Fade out','button secondary'); fade.type='button';
        fade.addEventListener('click', async () => { try { await api('/fade',{project_id:p.project_id,take_id:t.take_id,seconds:1.5}); libMessage('Added a faded copy as a new take.'); await refresh(); library(); } catch(err){ libMessage(err.message, true); } });
        row.append(fade);
      }
      if (t.section_of && t.section_of.take_id) {
        const splice = el('button','Splice into song','button primary'); splice.type='button';
        splice.addEventListener('click', async () => { try { await api('/section/splice',{project_id:p.project_id,take_id:t.section_of.take_id,candidate_take_id:t.take_id}); libMessage('Spliced: a new take of the whole song, with only that part replaced.'); await refresh(); library(); } catch(err){ libMessage(err.message, true); } });
        row.append(splice);
      }
      const killTake = el('button','Delete take','button secondary'); killTake.type='button'; killTake.addEventListener('click', async () => { if(!confirm(`Delete take ${i+1} and its audio?`)) return; try { await api('/take/delete',{project_id:p.project_id,take_id:t.take_id}); libMessage('Take deleted.'); await refresh(); library(); } catch(err){ libMessage(err.message, true); } }); row.append(killTake);
      if (t.state === 'succeeded') { const button = el('button','▶ Play'); button.type='button'; button.addEventListener('click',() => play(STUDIO.api+`/projects/${encodeURIComponent(p.project_id)}/takes/${encodeURIComponent(t.take_id)}/audio`,p.title || 'Untitled',`Take ${i+1}`,t.playable_edit_id)); row.append(button);
      const exp=el('button','Export','button secondary');exp.type='button';
      exp.addEventListener('click', async()=>{try{const folder=(($('settings-export-dir')||{}).value||'').trim();const d=await api('/export',{project_id:p.project_id,take_id:t.take_id,title:p.title||'Music Studio take',folder:folder||undefined});libMessage(`Exported to ${d.path}`);}catch(err){libMessage(err.message,true);}});
      row.append(exp);
      const add=el('button','Add to REVamp');add.type='button';add.onclick=async()=>{try{add.disabled=true;const chosen=(($('revamp-playlist')||{}).value||'').trim();await api('/studio/revamp/add-take',{project_id:p.project_id,take_id:t.take_id,...(chosen?{playlist_id:chosen}:{})});add.textContent='Added ✓';$('revamp-status').textContent=`${p.title || 'Untitled'} · take ${i+1} is in REVamp.`;show('listen');loadRevamp();}catch(err){add.disabled=false;$('revamp-status').textContent=err.message;show('listen');}};row.append(add); }
      tidyTakeRow(row); card.append(row); }); container.append(card);
  });
  if (empty.length) {
    const toggle = el('button', showEmptyDrafts ? `Hide ${empty.length} empty drafts` : `Show ${empty.length} empty drafts`,
                      'button secondary library-empties');
    toggle.type = 'button';
    toggle.addEventListener('click', () => { showEmptyDrafts = !showEmptyDrafts; library(); });
    container.append(toggle);
  }
}

// A draft with nothing in it yet: no words, no style, no takes. Kept, but folded away.
function isEmptyDraft(p) {
  return !String(p.caption || '').trim() && !String(p.lyrics || '').trim() && !String(p.idea || '').trim()
    && !(p.takes || []).length;
}
let showEmptyDrafts = false;
const LIBRARY_ORDERS = {
  newest: (a, b) => (b.updated_at || 0) - (a.updated_at || 0),
  oldest: (a, b) => (a.updated_at || 0) - (b.updated_at || 0),
  title: (a, b) => String(a.title || 'Untitled').localeCompare(String(b.title || 'Untitled')),
  takes: (a, b) => (b.takes || []).length - (a.takes || []).length,
};
// Play and Export stay on the row; every other take action moves into a "More" menu. The buttons
// are moved, not rebuilt, so each keeps exactly the handler it was made with.
function tidyTakeRow(row) {
  const keep = b => /Play$/.test(b.textContent.trim()) || b.textContent.trim() === 'Export';
  const extra = [...row.children].filter(n => n.tagName === 'BUTTON' && !keep(n));
  if (extra.length < 2) return;
  const more = el('details', '', 'take-more'), summary = el('summary', 'More ▾'), menu = el('div', '', 'take-menu');
  menu.append(...extra); more.append(summary, menu); row.append(more);
}
function formatEta(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return 'estimating';
  const value = Math.max(0, Math.round(Number(seconds)));
  if (value < 5) return 'almost done';
  if (value < 60) return `${value}s`;
  const minutes = Math.floor(value / 60), rest = value % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}
function targetName(id) {
  return targets.find(t => t.id === id)?.name || id || '';
}
function takeTargetLabel(take) {
  const snapshot = take.snapshot || {};
  const id = snapshot.comfy_target_id || take.comfy_target_id;
  const name = snapshot.comfy_target_name || take.comfy_target_name;
  const url = snapshot.comfy_target_url || take.comfy_target_url;
  if (name || url) return `target ${name || url}${name && url ? ` (${url})` : ''}`;
  if (id) return `target ${targetName(id)}`;
  if ((take.engine_id || '') === 'minimax_m3_comfy') return 'target default endpoint';
  return '';
}
function normalizeEngineId(id) { return id === 'ace' ? 'ace_local' : id; }
function engineFor(id) {
  const want = normalizeEngineId(id);
  return want ? engines.find(e => normalizeEngineId(e.id) === want) : null;
}
// An engine that does not declare an operation must not be offered it: the server refuses the
// render anyway, so a visible control would be a button that can only fail. Old take records with
// no engine id keep the previous behaviour (unknown capabilities are not a reason to hide a control).
function takeCapabilities(engineId) {
  const engine = engineFor(engineId);
  return engine ? {engine, capabilities: engine.capabilities || {}} : null;
}
function findActiveTake() {
  const live = take => take && (take.state === 'running' || take.state === 'queued');
  if (current) {
    const project = projects.find(p => p.project_id === current.project_id);
    const take = project && (project.takes || []).find(t => t.take_id === project.active_take_id);
    if (live(take)) return {project, take};
  }
  let best = null;
  for (const project of projects) {
    const take = (project.takes || []).find(t => t.take_id === project.active_take_id);
    if (!live(take)) continue;
    if (!best || (take.state === 'running' && best.take.state !== 'running')) best = {project, take};
  }
  return best;
}
function gpuForTake(take) {
  if (!hardware || !hardware.pid_to_gpu) return null;
  for (const pid of [take.pid, take.ace_pid]) {
    if (pid === null || pid === undefined) continue;
    const index = hardware.pid_to_gpu[String(pid)];
    if (index === null || index === undefined) continue;
    const device = (hardware.devices || []).find(d => d.index === index);
    if (!device) continue;
    return {index, name: device.name, pid};
  }
  return null;
}
function renderActiveTake() {
  const panel = $('active-take'), active = findActiveTake();
  if (!active) { panel.hidden = true; return; }
  const {project, take} = active, running = take.state === 'running';
  const progress = running ? Math.max(0, Math.min(99, Number(take.progress) || 0)) : 0;
  panel.hidden = false;
  $('active-title').textContent = (project.title || 'Untitled') + ' · Take ' + ((project.takes || []).findIndex(t => t.take_id === take.take_id) + 1);
  $('active-state').textContent = running ? 'Rendering' : 'Queued';
  $('active-progress-bar').style.width = progress + '%';
  $('active-progress-bar').parentElement.setAttribute('aria-valuenow', String(progress));
  $('active-progress').textContent = running ? `estimated ${progress}% complete · estimated ETA ${formatEta(take.eta_seconds)}` : `Waiting for the GPU${take.queue_position ? ` · queue #${take.queue_position}` : ''}.`;
  const engine = take.engine_id || project.engine || 'engine', target = takeTargetLabel(take);
  $('active-detail').textContent = `Engine: ${engine}${target ? ` · ${target}` : ''}`;
  const gpu = running ? gpuForTake(take) : null;
  $('active-gpu').hidden = !running;
  if (gpu) $('active-gpu').textContent = `GPU ${gpu.index}: ${gpu.name} · PID ${gpu.pid} matched in nvidia-smi`;
  else if (running) $('active-gpu').textContent = 'GPU not identified';
}
async function loadHardware(force = false) {
  if (!force && Date.now() - hardwareFetchedAt < 5000) return hardware;
  try { hardware = await api('/studio/hardware'); hardwareFetchedAt = Date.now(); hardwareProblem = ''; }
  catch (e) {
    // "No GPU" and "the request failed" look identical on screen unless we keep them apart.
    hardware = null;
    hardwareProblem = e && e.message ? e.message : 'the hardware request failed';
  }
  drawHardware();
  return hardware;
}
function scheduleRefresh(delay) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => { refresh(); }, delay);
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const data = await api('/status');
    engines = data.engines || []; projects = data.projects || [];
    const ready = engines.filter(e => e.available).length;
    $('engine-status').textContent = ready ? `${ready} engine${ready===1?'':'s'} ready` : 'No engine ready';
    engineHint(); buttons(); library();
    const live = projects.some(p => (p.takes || []).some(t => t.state === 'running' || t.state === 'queued'));
    const running = projects.some(p => (p.takes || []).some(t => t.state === 'running'));
    if (running) await loadHardware();
    renderActiveTake();
    scheduleRefresh(live ? 2500 : 10000);
  } catch (e) {
    $('engine-status').textContent = 'Studio unavailable'; message(e.message, true);
    scheduleRefresh(10000);
  } finally { refreshing = false; }
}
form.addEventListener('input',() => {dirty=true; revision++; buttons();});
form.elements.engine.addEventListener('change',engineHint);
form.elements.comfy_target_id.addEventListener('change',()=>{targetProbes={};engineHint();buttons();});
form.elements.minimax_guidance.addEventListener('input',()=>{$('minimax-guidance-value').value=form.elements.minimax_guidance.value;});
form.addEventListener('submit', async e => { e.preventDefault(); if (busy) return; const savedRevision=revision; const draft=fields(); const workspace=$('workspace-select').value; busy=true; buttons(); try { const data=current ? await api('/update',{project_id:current.project_id,base_updated_at:current.updated_at,draft}) : await api('/create',{draft}); current=data.project; dirty=revision!==savedRevision; await api('/studio/assign',{project_id:current.project_id,workspace_id:workspace}); message(dirty ? 'The earlier draft was saved. Save your newer edits before approval.' : 'Draft saved. Review it before approval.'); await refreshWorkspaces(); await refresh(); } catch(err){message(err.message,true);} finally {busy=false; buttons();} });
$('approve').addEventListener('click', async () => { if(busy || !current || dirty) return; busy=true; buttons(); try { const data=await api('/approve',{project_id:current.project_id,base_updated_at:current.updated_at}); current=data.project; message(dirty ? 'The saved draft was approved; newer edits still need saving.' : 'Draft approved. You can render when ready.'); await refresh(); } catch(err){message(err.message,true);} finally {busy=false; buttons();} });
// The decision point: four lines and 25 seconds, instead of three minutes of a whole song the
// person has not heard yet. Polls its own take and plays it the moment it is ready.
$('preview-chorus').addEventListener('click', async () => {
  if (busy || !current || dirty) return;
  busy = true; buttons();
  try {
    const res = await api('/preview/chorus', { project_id: current.project_id, seconds: 25 });
    const preview = res.preview || {};
    message('Rendering the chorus — about half a minute of GPU time.');
    await refresh();
    const takeId = (preview.take || {}).take_id;
    const poll = async () => {
      try {
        const status = await api('/status');
        const project = (status.projects || []).find(p => p.project_id === preview.preview_project_id);
        const take = project && (project.takes || []).find(t => t.take_id === takeId);
        if (!take) { window.setTimeout(poll, 4000); return; }
        if (take.state === 'succeeded') {
          play(STUDIO.api + `/projects/${encodeURIComponent(preview.preview_project_id)}/takes/${encodeURIComponent(takeId)}/audio`,
            (current.title || 'Untitled') + ' — chorus', 'Preview · 25 s');
          message('That is the chorus. Like it? “Build the full song” renders the whole thing.');
          await refresh();
          return;
        }
        if (take.state === 'failed') { message(take.error || 'The chorus preview failed.', true); return; }
        window.setTimeout(poll, 4000);
      } catch (err) { message(err.message, true); }
    };
    poll();
  } catch (err) { message(err.message, true); }
  finally { busy = false; buttons(); }
});

$('render').addEventListener('click', async () => { if(busy || !current || dirty || !current.approved) return; busy=true; buttons(); try { const seed = crypto.getRandomValues(new Uint32Array(1))[0]; await api('/generate',{project_id:current.project_id,engine_id:form.elements.engine.value,seed}); message('Take queued. Follow its progress in your library.'); await refresh(); } catch(err){message(err.message,true);} finally {busy=false; buttons();} });
$('new-song').addEventListener('click', () => { if (dirty && !window.confirm('Discard unsaved changes?')) return; current=null; form.reset(); dirty=false; showCover(null); buttons(); engineHint(); message('New draft.'); show('create'); });
for (const [id, event] of [['library-search','input'], ['library-sort','change']]) {
  const node = $(id); if (node) node.addEventListener(event, () => {
    if (id === 'library-sort') { try { localStorage.setItem('studio.librarySort', node.value); } catch (e) {} }
    library(); });
}
try { const saved = localStorage.getItem('studio.librarySort'); if (saved && $('library-sort')) $('library-sort').value = saved; } catch (e) {}
$('nav-create').addEventListener('click',() => show('create')); $('nav-library').addEventListener('click',() => {libraryFilter=null;library();show('library');});
$('nav-workspaces').addEventListener('click',()=>{drawWorkspaces();show('workspaces');});
$('nav-compute').addEventListener('click',async()=>{show('compute');await refreshTargets();await loadHardware(true);});
function showCover(id) {
  const image=$('cover-preview'), placeholder=$('cover-placeholder');
  if(!id) { image.hidden=true; placeholder.hidden=false; image.removeAttribute('src'); return; }
  image.onload=()=>{image.hidden=false;placeholder.hidden=true;};
  image.onerror=()=>{image.hidden=true;placeholder.hidden=false;};
  image.src=mediaUrl(STUDIO.api+'/studio/covers/'+encodeURIComponent(id)+'?v='+Date.now());
}
function ensureTargetOption(id) {
  if (!id) return;
  const select = form.elements.comfy_target_id;
  if (![...select.options].some(o => o.value === id)) {
    const option = el('option', `Missing target ${targetName(id)}`);
    option.value = id;
    select.append(option);
  }
}
function fillTargetSelect() {
  const select = form.elements.comfy_target_id, previous = select.value;
  select.replaceChildren();
  const fallback = el('option', 'Default endpoint (engine-configured)');
  fallback.value = '';
  select.append(fallback);
  targets.forEach(t => { const option = el('option', t.name); option.value = t.id; select.append(option); });
  if (previous) ensureTargetOption(previous);
  select.value = previous || '';
}
function drawTargets() {
  const list = $('target-list');
  list.replaceChildren();
  if (!targets.length) {
    list.append(el('p', 'No saved targets. MiniMax renders will use the engine-configured default endpoint.'));
    return;
  }
  targets.forEach(t => {
    const card = el('article', '', 'project target-card'), head = el('div', '', 'project-head');
    const info = el('div'), actions = el('div', '', 'actions target-actions');
    info.append(el('b', t.name), el('p', t.url));
    const test = el('button', 'Test', 'button secondary'), remove = el('button', 'Delete', 'button secondary');
    test.type = remove.type = 'button';
    test.onclick = () => probeTarget(t, test, result);
    remove.onclick = () => deleteTarget(t);
    actions.append(test, remove);
    head.append(info, actions);
    const result = el('p', '', 'hint target-result');
    card.append(head, result);
    list.append(card);
  });
}
async function refreshTargets() {
  try {
    const data = await api('/studio/targets');
    targets = data.targets || [];
    for (const id of Object.keys(targetProbes)) if (!targets.some(t => t.id === id)) delete targetProbes[id];
    fillTargetSelect();
    drawTargets();
    $('target-message').textContent = targets.length ? `${targets.length} saved target${targets.length === 1 ? '' : 's'}.` : 'No saved targets; the engine default endpoint is used.';
    $('target-message').classList.remove('error');
  } catch (e) {
    targets = [];
    fillTargetSelect();
    drawTargets();
    $('target-message').textContent = 'Targets unavailable: ' + e.message;
    $('target-message').classList.add('error');
  }
}
async function probeTarget(t, button, result) {
  button.disabled = true;
  result.textContent = 'Testing…';
  result.classList.remove('error');
  try {
    const data = await api('/studio/targets/' + encodeURIComponent(t.id) + '/probe', undefined, 'POST');
    const status = data.status || {}, available = status.available === true;
    targetProbes[t.id] = {available, detail: status.detail || '', state: status.state || 'unknown'};
    result.textContent = `${available ? 'Available' : 'Unavailable'} · ${status.state || 'unknown'}${status.detail ? ' · ' + status.detail : ''}`;
    const comfyDevice = status.extra && status.extra.comfy_device;
    if (comfyDevice) result.textContent += ' · Server-reported device: ' + comfyDevice;
    result.classList.toggle('error', !available);
    if (form.elements.comfy_target_id.value === t.id) { engineHint(); buttons(); }
  } catch (e) {
    targetProbes[t.id] = {available: false, detail: e.message, state: 'error'};
    result.textContent = e.message;
    result.classList.add('error');
    if (form.elements.comfy_target_id.value === t.id) { engineHint(); buttons(); }
  } finally { button.disabled = false; }
}
async function deleteTarget(t) {
  if (!window.confirm(`Delete target "${t.name}"? Drafts using it must choose another endpoint.`)) return;
  try {
    await api('/studio/targets/' + encodeURIComponent(t.id), undefined, 'DELETE');
    delete targetProbes[t.id];
    if (form.elements.comfy_target_id.value === t.id) {
      ensureTargetOption(t.id);
      dirty = true; revision++; buttons(); engineHint();
      message('Target deleted; this draft needs another endpoint before rendering.');
    }
    await refreshTargets();
  } catch (e) {
    $('target-message').textContent = e.message;
    $('target-message').classList.add('error');
  }
}
function drawHardware() {
  const list = $('hardware-list');
  list.replaceChildren();
  if (!hardware) {
    list.append(el('p', hardwareProblem
      ? 'Could not read the hardware inventory: ' + hardwareProblem
      : 'Hardware inventory not loaded.', hardwareProblem ? 'hint error' : 'hint'));
    return;
  }
  if (hardware.error) list.append(el('p', 'nvidia-smi: ' + hardware.error, 'hint'));
  if (!(hardware.devices || []).length) { list.append(el('p', 'No local GPU devices reported.')); return; }
  hardware.devices.forEach(device => {
    const card = el('article', '', 'project hardware-card');
    card.append(el('b', `GPU ${device.index ?? '—'} · ${device.name || 'Unknown'}`));
    card.append(el('p', `${device.memory_used_mb ?? '—'} / ${device.memory_total_mb ?? '—'} MiB · ${device.utilization_percent ?? '—'}% utilization`));
    const processes = (hardware.processes || []).filter(p => p.gpu_uuid === device.uuid || p.gpu_index === device.index);
    if (processes.length) processes.forEach(p => card.append(el('p', `PID ${p.pid} · ${p.process_name || 'unknown'} · ${p.used_memory_mb ?? '—'} MiB`, 'hint')));
    else card.append(el('p', 'No compute process attributed by nvidia-smi.', 'hint'));
    list.append(card);
  });
}
async function refreshHardware() {
  try {
    await loadHardware(true);
    $('hardware-message').textContent = hardware?.available ? 'Local GPU inventory loaded. GPU attribution comes only from nvidia-smi PIDs.' : (hardware?.error || 'GPU inventory unavailable.');
    $('hardware-message').classList.toggle('error', !hardware?.available);
  } catch (e) {
    $('hardware-message').textContent = 'Hardware unavailable: ' + e.message;
    $('hardware-message').classList.add('error');
  }
}
async function refreshWorkspaces() {
  try { const d=await api('/studio/workspaces'); workspaces=d.workspaces || []; const select=$('workspace-select'), previous=select.value; select.replaceChildren();
    workspaces.forEach(w=>{const o=el('option',w.name);o.value=w.id;select.append(o);});
    if(workspaces.some(w=>w.id===previous))select.value=previous; drawWorkspaces();
  } catch(e) { $('workspace-list').textContent='Workspaces unavailable: '+e.message; }
}
function drawWorkspaces() {
  const list=$('workspace-list');list.replaceChildren();
  workspaces.forEach(w=>{const card=el('article','','project workspace-card'), label=el('div');label.append(el('b',w.name),el('p',`${projects.filter(p=>belongsTo(p,w.id)).length} songs`)); const open=el('button','View songs','button secondary');open.type='button';open.onclick=()=>{libraryFilter=w.id;library();show('library');}; card.append(label,open);list.append(card);});
}
$('workspace-form').addEventListener('submit',async e=>{e.preventDefault(); try {const result=await api('/studio/workspaces',{name:$('workspace-name').value});$('workspace-name').value='';await refreshWorkspaces();$('workspace-select').value=result.workspace.id;} catch(err){$('workspace-list').textContent=err.message;}});
$('simple-mode').onclick=()=>{$('custom-fields').hidden=true;$('simple-mode').classList.add('active');$('custom-mode').classList.remove('active');$('mode-help').textContent='A little direction is enough to begin.';};
$('custom-mode').onclick=()=>{$('custom-fields').hidden=false;$('custom-mode').classList.add('active');$('simple-mode').classList.remove('active');$('mode-help').textContent='Shape the details of your song.';};
$('metadata-toggle').onclick=()=>{const box=$('metadata-fields');box.hidden=!box.hidden;$('metadata-toggle').setAttribute('aria-expanded',String(!box.hidden));};
$('idea-style').oninput=()=>{$('idea-style-value').value=$('idea-style').value;};
async function loadModels() {
  try { const d=await api('/studio/models'); modelCatalog=Array.isArray(d.providers)?d.providers:[]; const select=$('idea-provider');select.replaceChildren();const placeholder=el('option','Select a provider');placeholder.value='';select.append(placeholder);modelCatalog.forEach(p=>{const o=el('option',p.name || p.slug);o.value=p.slug;select.append(o);});
    if(!modelCatalog.length)throw new Error('No configured models returned.'); if(modelCatalog.some(p=>p.slug===d.provider))select.value=d.provider; $('idea-connection').textContent='Choose a configured Hermes model.';fillModels(d.model);
  } catch(e) { $('idea-connection').textContent='Hermes model connection unavailable. Check the Hermes installation and setup guide.'; }
}
function fillModels(preferred){const p=modelCatalog.find(x=>x.slug===$('idea-provider').value), select=$('idea-model');select.replaceChildren();const placeholder=el('option','Select a model');placeholder.value='';select.append(placeholder);(p?.models || []).forEach(m=>{const id=typeof m==='string'?m:(m.id || m.name);if(!id)return;const option=el('option',id);option.value=id;select.append(option);});if(typeof preferred==='string' && [...select.options].some(o=>o.value===preferred))select.value=preferred;}
$('idea-provider').onchange=()=>fillModels();
function addIdea(role, text){const p=el('p',text,role==='user'?'human':'assistant');$('idea-messages').append(p);$('idea-messages').scrollTop=$('idea-messages').scrollHeight;}
// A standalone host has no model of its own: the model lives in the Hermes plugin process. When the
// page is framed by the desktop pane, it asks the pane (postMessage) and the pane calls its own
// plugin route — the one place ctx.llm exists. In a plain browser tab there is no pane to ask, and
// the message says where drafting does work.
function paneDraft(request) {
  return new Promise((resolve, reject) => {
    const parentWindow = window.parent;
    if (!parentWindow || parentWindow === window) {
      reject(new Error("Drafting needs Hermes' model. Use the Music Studio pane in Hermes Desktop, ask in a chat, or call the music_studio tool with action='draft'."));
      return;
    }
    const id = 'draft-' + Math.random().toString(36).slice(2);
    const timer = setTimeout(() => {
      window.removeEventListener('message', onReply);
      reject(new Error('The pane did not answer the draft request in time.'));
    }, 60000);
    function onReply(event) {
      const data = event && event.data;
      if (!data || data.type !== 'hermes-music-studio:draft-result' || data.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener('message', onReply);
      if (data.ok) resolve({ draft: data.draft, content: data.content, written_by: 'ctx.llm' });
      else reject(new Error(data.error || 'drafting failed'));
    }
    window.addEventListener('message', onReply);
    parentWindow.postMessage({ type: 'hermes-music-studio:draft', id,
      prompt: request.prompt, tone: request.tone, style: request.style }, '*');
  });
}

async function idea(action) {
  const input=$('idea-input').value.trim(); if(input){conversation.push({role:'user',content:input});addIdea('user',input);$('idea-input').value='';}
  if(!conversation.some(m=>m.role==='user')){$('idea-connection').textContent='Tell me a little about the song first.';return;}
  const provider=$('idea-provider-manual').value.trim() || $('idea-provider').value, model=$('idea-model-manual').value.trim() || $('idea-model').value;
  $('idea-send').disabled=true;$('idea-apply').disabled=true;$('idea-connection').textContent='Thinking…';
  const tone=$('idea-tone').value.toLowerCase(), style=Number($('idea-style').value);
  try {const d = provider && model
      ? await api('/studio/ideas',{provider,model,messages:conversation.slice(-20),action,tone,style})
      : await paneDraft({action, tone, style, prompt: conversation.map(m => m.content).join('\n')});
    if(action==='chat'){conversation.push({role:'assistant',content:d.content.slice(0,4000)});addIdea('assistant',d.content);}
    else {if((form.elements.caption.value || form.elements.lyrics.value) && !window.confirm('Replace the current draft fields with the suggested draft?'))return;
      const draft=d.draft || {};for(const key of ['title','caption','lyrics','bpm','keyscale','duration_seconds']) if(draft[key]!==undefined && form.elements[key])form.elements[key].value=draft[key];
      if(typeof draft.instrumental==='boolean')form.elements.instrumental.checked=draft.instrumental;
      dirty=true;revision++;buttons();message('Suggested draft added. Review and save it.');show('create');}
    $('idea-connection').textContent = provider && model
      ? 'Using ' + provider + ' / ' + model + '.'
      : 'Drafted by the Hermes agent (its own model).';
  } catch(e) {$('idea-connection').textContent=e.message;}
  finally {$('idea-send').disabled=false;$('idea-apply').disabled=false;}
}
$('idea-send').onclick=()=>idea('chat');$('idea-apply').onclick=()=>idea('draft');
$('cover-save').onclick=async()=>{const file=$('cover-input').files[0];if(!current){$('cover-message').textContent='Save the song first.';return;}if(!file){$('cover-message').textContent='Choose an image first.';return;}if(file.size>2*1024*1024){$('cover-message').textContent='Image must be 2 MiB or smaller.';return;} const projectId=current.project_id;
  try {const encoded=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});await api('/studio/covers/'+encodeURIComponent(projectId),{mime:file.type,data:encoded});if(current?.project_id===projectId)showCover(projectId);$('cover-message').textContent='Cover saved.';}catch(e){$('cover-message').textContent=e.message;}};
async function fillRevampPlaylists() {
  const select = $('revamp-playlist');
  if (!select) return;
  const keep = select.value;
  try {
    const data = await api('/studio/revamp/playlists');
    const rows = data.playlists || [];
    select.replaceChildren();
    const mine = el('option', 'Music Studio');
    mine.value = '';
    select.append(mine);
    rows.forEach(row => {
      const name = row && (row.name || row.id);
      if (!name || name === 'Music Studio') return;
      const option = el('option', name);
      option.value = row.id || name;
      select.append(option);
    });
    if ([...select.options].some(o => o.value === keep)) select.value = keep;
  } catch (err) { /* REVamp is optional; the list simply stays on the studio playlist */ }
}

let revampItems = [];
async function loadRevamp(){
  const box = $('revamp-added');
  try {
    const status = await api('/studio/revamp/status').catch(() => ({}));
    if (status.available !== true) {
      $('revamp-status').textContent = status.reason || 'REVamp is not installed. Your takes still play and export from here.';
      revampItems = [];
      box.replaceChildren(el('p','Nothing added yet. Open a draft in Library and press “Add to REVamp”.'));
      return;
    }
    $('revamp-status').textContent = status.player_connected
      ? 'REVamp connected. Press Play here, or open REVamp to hear it there.'
      : 'REVamp is available. Open its player in Hermes to hear playback.';
    fillRevampPlaylists();
    const data = await api('/studio/revamp/added').catch(() => ({ items: [] }));
    const items = data.items || [];
    revampItems = items;
    box.replaceChildren();
    if (!items.length) {
      box.append(el('p','Nothing added yet. Open a draft in Library and press “Add to REVamp”.'));
      return;
    }
    items.forEach(item => {
      const row = el('article', '', 'project');
      row.append(el('h2', item.title || 'Music Studio take'));
      row.append(el('p', item.artist || 'Hermes Music Studio', 'hint'));
      const playHere = el('button','▶ Play here','button secondary'); playHere.type='button';
      playHere.onclick = () => {
        const url = (item.playback && item.playback.url) || '';
        if (url) { play(url, item.title || 'Music Studio take', 'From your studio library'); return; }
        // A rendered button that does nothing is the failure mode this product keeps being
        // judged on, so it explains itself instead of sitting there inert.
        $('revamp-status').textContent = 'REVamp has no playable file for this item yet; export the take and refresh.';
      };
      const remove = el('button','Remove','button secondary'); remove.type='button';
      remove.onclick = async () => { try { await api('/studio/revamp/remove',{item_id:item.id}); await loadRevamp(); $('revamp-status').textContent='Removed from REVamp.'; } catch (err) { $('revamp-status').textContent = err.message; } };
      row.append(playHere, remove);
      box.append(row);
    });
  } catch (err) {
    box.replaceChildren(el('p', err.message, 'message error'));
  }
}

if ($('revamp-create')) $('revamp-create').addEventListener('click', async () => {
  const name = ($('revamp-new-name').value || '').trim();
  if (!name) { $('revamp-status').textContent = 'Give the playlist a name first.'; return; }
  // The takes are exported to the person's folder FIRST; the playlist then points at those
  // files. A playlist that points at nothing is worse than no playlist.
  const takes = revampItems
    .map(item => String(item.id || ''))
    .filter(id => id.startsWith('studio:'))
    .map(id => { const parts = id.split(':'); return { project_id: parts[1], take_id: parts[2] }; })
    .filter(t => t.project_id && t.take_id);
  if (!takes.length) { $('revamp-status').textContent = 'Add a take first — the playlist is built from the takes you added.'; return; }
  $('revamp-status').textContent = `Exporting ${takes.length} take(s), then building “${name}”…`;
  try {
    const folder = (($('settings-export-dir') || {}).value || '').trim();
    const data = await api('/studio/revamp/playlist', { name, folder: folder || undefined, takes });
    await fillRevampPlaylists();
    const created = data.playlist || {};
    if (created.id) $('revamp-playlist').value = created.id;
    $('revamp-new-name').value = '';
    $('revamp-status').textContent = `“${name}” created in REVamp — ${(data.files || []).length} file(s) in ${data.folder}.`;
  } catch (err) { $('revamp-status').textContent = err.message; }
});
$('nav-listen').onclick=()=>{show('listen');loadRevamp();};
if ($('play-sample')) $('play-sample').addEventListener('click', playDemo);
// The old playlist picker is gone: a take goes to the studio's own REVamp list.
$('manage-targets').onclick=async()=>{show('compute');await refreshTargets();await loadHardware(true);};
$('target-form').addEventListener('submit', async e => {
  e.preventDefault();
  const name = $('target-name').value.trim(), url = $('target-url').value.trim();
  if (!name || !url) { $('target-message').textContent = 'Name and URL are required.'; return; }
  $('target-message').textContent = 'Saving target…';
  try {
    await api('/studio/targets', {name, url});
    $('target-name').value = ''; $('target-url').value = '';
    await refreshTargets();
    $('target-message').textContent = `Saved "${name}". Start ComfyUI on that host before rendering.`;
  } catch (err) {
    $('target-message').textContent = err.message;
    $('target-message').classList.add('error');
  }
});
$('hardware-refresh').onclick=refreshHardware;
refreshTargets(); refresh(); refreshWorkspaces(); loadModels();


// -- render settings and library folder ------------------------------------
function fillSelect(node, values, current) {
  node.replaceChildren(...values.map(value => { const option = document.createElement('option'); option.value = value; option.textContent = value; option.selected = value === current; return option; }));
}
function renderLibraryPath(lib) {
  const mib = (Number(lib.bytes || 0) / 1048576).toFixed(1);
  $('library-path').textContent = `${lib.active}${lib.moved ? ' (custom folder)' : ''} · ${lib.projects} project(s) · ${lib.audio_files} audio file(s) · ${mib} MiB · default: ${lib.default}`;
}
async function loadSettings() {
  const data = await api('/settings');
  const knobs = data.settings.minimax;
  $('settings-comfy-url').value = data.settings.comfy_url || '';
  if ($('settings-scan-hosts')) $('settings-scan-hosts').value = data.settings.scan_hosts || '';
  if ($('settings-export-dir')) $('settings-export-dir').value = data.settings.export_dir || '';
  $('settings-steps').value = knobs.steps;
  $('settings-cfg').value = knobs.cfg;
  $('settings-cfg-scale').value = knobs.cfg_scale;
  $('settings-top-k').value = knobs.top_k;
  fillSelect($('settings-sampler'), data.samplers, knobs.sampler);
  fillSelect($('settings-scheduler'), data.schedulers, knobs.scheduler);
  renderLibraryPath(data.library);
  return data;
}
async function saveSettings(event) {
  event.preventDefault();
  const body = { comfy_url: $('settings-comfy-url').value.trim(),
    minimax: { steps: Number($('settings-steps').value), cfg: Number($('settings-cfg').value),
      cfg_scale: Number($('settings-cfg-scale').value), top_k: Number($('settings-top-k').value),
      sampler: $('settings-sampler').value, scheduler: $('settings-scheduler').value } };
  try {
    const data = await api('/settings', body);
    renderLibraryPath(data.library);
    $('settings-message').textContent = 'Saved. The next render uses these values.';
  } catch (err) { $('settings-message').textContent = err.message; }
}
function renderChecks(result) {
  const list = $('settings-checks'); list.replaceChildren();
  const card = el('article', '', 'project');
  card.append(el('h2', result.url || 'endpoint'));
  const checks = (result.status && result.status.checks) || [];
  if (!checks.length) card.append(el('p', (result.status && (result.status.detail || result.status.state)) || 'No checks reported.', 'hint'));
  checks.forEach(check => card.append(el('p', `${check.ok ? '✓' : '✗'} ${check.name}${check.detail ? ': ' + check.detail : ''}`, 'hint')));
  list.append(card);
  $('settings-message').textContent = result.status && result.status.available
    ? 'Endpoint ready.' : 'Endpoint not ready yet — read the checks above.';
}
async function testEndpoint() {
  $('settings-message').textContent = 'Testing endpoint…';
  try { renderChecks(await api('/settings/test', { url: $('settings-comfy-url').value.trim() })); }
  catch (err) { $('settings-message').textContent = err.message; }
}
async function findRenderers() {
  const box = $('discover-results');
  box.replaceChildren();
  $('settings-message').textContent = 'Looking for a ComfyUI on this machine and the network…';
  try {
    const data = await api('/discover', {});
    if (!data.found || !data.found.length) {
      $('settings-message').textContent = 'No ComfyUI answered. Start ComfyUI (port 8188), or type its address and press Test endpoint. To scan other machines, list them in MUSIC_STUDIO_SCAN_HOSTS.';
      return;
    }
    data.found.forEach(item => {
      const card = el('article', '', 'project');
      card.append(el('h2', item.url));
      card.append(el('p', item.minimax ? 'ComfyUI with MiniMax Music 3 — ready to render.'
        : 'ComfyUI answers, but the MiniMax Music 3 nodes are not installed.', 'hint'));
      const use = el('button', item.minimax ? 'Use this' : 'Use this anyway', 'button secondary');
      use.type = 'button';
      use.addEventListener('click', () => { $('settings-comfy-url').value = item.url; $('settings-message').textContent = `Endpoint set to ${item.url}. Press Test endpoint to check it.`; });
      card.append(use);
      box.append(card);
    });
    $('settings-message').textContent = `${data.found.length} of ${data.tested} addresses answered.`;
  } catch (err) { $('settings-message').textContent = err.message; }
}
async function relocateLibrary(mode) {
  const path = $('library-target').value.trim();
  if (!path) { $('library-message').textContent = 'Enter a folder first.'; return; }
  if (mode === 'move' && !confirm(`Move the library to ${path}? The old copies are deleted after the switch.`)) return;
  try {
    const data = await api('/library/relocate', { path, mode });
    renderLibraryPath(data.library);
    $('library-message').textContent = `${mode === 'move' ? 'Moved' : 'Copied'} to ${data.library.active} (${data.copied} item(s)).`;
    await refresh(); library();
  } catch (err) { $('library-message').textContent = err.message; }
}
async function resetLibrary() {
  try {
    const data = await api('/library/reset');
    renderLibraryPath(data.library);
    $('library-message').textContent = 'Using the default folder again.';
    await refresh(); library();
  } catch (err) { $('library-message').textContent = err.message; }
}
$('settings-form').addEventListener('submit', saveSettings);
$('settings-test').addEventListener('click', testEndpoint);
if ($('discover-renderers')) $('discover-renderers').addEventListener('click', findRenderers);
$('library-copy').addEventListener('click', () => relocateLibrary('copy'));
$('library-move').addEventListener('click', () => relocateLibrary('move'));
$('library-reset').addEventListener('click', resetLibrary);
$('nav-compute').addEventListener('click', () => { loadSettings().catch(err => { $('settings-message').textContent = err.message; }); });
loadSettings().catch(err => { const node = $('settings-message'); if (node) node.textContent = err.message; });

// Change eight seconds without touching the rest of the song. Every control is on the row: three
// typed dialogs (`window.prompt`) used to stand between the person and the edit, which is why "it is
// hard to make changes to any field". When the replacement finishes it is spliced back automatically,
// so the audio outside the range is the parent's own bytes rather than a re-render. An engine without
// repaint renders the range as a plain generate of that section; the splice is the same.
function snippetRow(project, take, row, options = {}) {
  if (row.querySelector('.snippet-form')) return;      // one form per take, not a stack of them
  const canRepaint = options.repaint !== false;
  const form = el('div', '', 'snippet-form');
  if (!canRepaint) {
    form.append(el('p', `${options.engine || 'This engine'} cannot ${options.missing || 'repaint a range'}; it renders the part on its own and the rest of the song is copied back.`,
      'hint snippet-note'));
  }
  const start = el('input'); start.type = 'number'; start.min = '0'; start.step = '0.5';
  start.value = '0'; start.className = 'snippet-start'; start.placeholder = 'from (s)';
  const end = el('input'); end.type = 'number'; end.min = '1'; end.step = '0.5';
  const total = Number(take.audio_seconds || (take.qc || {}).file_s || 0);
  end.value = String(Math.min(8, Math.max(3, Math.round(total || 8))));
  end.className = 'snippet-end'; end.placeholder = 'to (s)';

  const parts = project.sections || [];
  let picker = null;
  if (parts.length) {
    picker = document.createElement('select');
    picker.className = 'snippet-part';
    const first = document.createElement('option');
    first.value = ''; first.textContent = 'Pick a part…';
    picker.append(first);
    parts.forEach((sec, i) => {
      const option = document.createElement('option');
      option.value = String(i);
      option.textContent = `${sec.label || sec.name || ('part ' + (i + 1))} ${Math.round(sec.start_s)}–${Math.round(sec.end_s)}s`;
      picker.append(option);
    });
    picker.addEventListener('change', () => {
      const chosen = parts[Number(picker.value)];
      if (!chosen) return;
      start.value = String(chosen.start_s); end.value = String(chosen.end_s);
    });
  }

  const words = el('input'); words.type = 'text'; words.className = 'snippet-words';
  words.placeholder = 'make this more intense, add a climax, softer';
  const go = el('button', 'Render the new part', 'button secondary'); go.type = 'button';

  go.addEventListener('click', async () => {
    const from = Number(start.value), to = Number(end.value);
    const instruction = (words.value || '').trim();
    if (!(from >= 0 && to > from)) { libMessage('Give a range: from 0, to 8, for example.', true); return; }
    if (canRepaint && to - from < 3) { libMessage('The engine repaints at least 3 seconds at a time.', true); return; }
    if (!instruction) { libMessage('Say what this part should become.', true); return; }
    go.disabled = true;
    try {
      const queued = canRepaint
        ? await api('/snippet', { project_id: project.project_id, take_id: take.take_id,
          start_s: from, end_s: to, instruction })
        : await api('/section/render', { project_id: project.project_id, take_id: take.take_id,
          start_s: from, end_s: to, caption: instruction });
      const childId = (queued.take || {}).take_id;
      libMessage(canRepaint
        ? `Repainting ${from}–${to}s. The rest of the song is copied back when it finishes.`
        : `Rendering ${from}–${to}s on its own. The rest of the song is copied back when it finishes.`);
      await refresh(); library();
      snippetWait(project, childId, 0, canRepaint);
    } catch (err) { libMessage(err.message, true); } finally { go.disabled = false; }
  });

  form.append(...(picker ? [picker] : []), start, end, words, go);
  row.append(form);
}

// Poll the child take, then splice it back. A person who asked for eight seconds should not have to
// find a second button afterwards, and a splice that never happens leaves a take nobody asked for.
async function snippetWait(project, childId, tries, canRepaint = true) {
  if (!childId) return;
  if (tries > 60) { libMessage('That render is taking longer than usual — check the takes above.', true); return; }
  await new Promise(done => setTimeout(done, 6000));
  let take = null;
  try {
    const status = await api('/status');
    const fresh = (status.projects || []).find(x => x.project_id === project.project_id);
    take = fresh && (fresh.takes || []).find(x => x.take_id === childId);
  } catch (err) { /* keep polling; a transient status failure is not a failed render */ }
  if (!take) return snippetWait(project, childId, tries + 1, canRepaint);
  if (take.state === 'failed') {
    libMessage(`${canRepaint ? 'The repaint' : 'The section render'} failed: ` +
      (take.error || 'no reason recorded'), true);
    await refresh(); library();
    return;
  }
  if (take.state !== 'succeeded') return snippetWait(project, childId, tries + 1, canRepaint);
  try {
    await api('/snippet/splice', { project_id: project.project_id, take_id: childId });
    libMessage('Done — only that part changed; the rest of the song was copied back untouched.');
  } catch (err) { libMessage('Rendered, but splicing failed: ' + err.message, true); }
  await refresh(); library();
}

// -- about, companion plugin, contributions --------------------------------
let aboutCache = null;

function aboutMessage(text, bad = false) {
  const node = $('about-message');
  if (!node) return;
  node.textContent = text;
  node.classList.toggle('error', bad);
}
function openLink(url) {
  if (!url) return;
  // Inside the Hermes pane this page is a sandboxed frame: it has no bridge and must not reach for one.
  // Ask the pane instead — it holds `ctx.os.openExternal`, which is the public seam (review item 2).
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'hermes-music-studio:open-external', url: String(url) }, '*');
      return;
    }
  } catch (err) { /* fall through to a plain tab in the standalone browser view */ }
  window.open(url, '_blank', 'noopener');
}
async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (err) { /* fall through to the textarea */ }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch (err) { return false; }
}
async function loadAbout() {
  const data = await api('/about');
  aboutCache = data;
  $('revamp-tagline').textContent = data.revamp.tagline;
  $('revamp-install').textContent = data.revamp.install;
  $('revamp-requires').textContent = data.revamp.requires;
  const states = {
    running: 'REVamp is running on this machine.',
    installed: 'REVamp is installed here but not running.',
    'mcp-only': 'REVamp is wired as an MCP server only — install the plugin to get the player.',
    absent: 'REVamp is not installed here yet.'
  };
  $('revamp-state').textContent = states[data.revamp.state] || String(data.revamp.state);
  $('tests-command').textContent = data.paths.tests;
  $('contribute-note').textContent = data.visibility === 'private'
    ? 'The repository is private, so the guide and the issue tracker only open for people the owner invited. Copying diagnostics works for anyone.'
    : '';
  return data;
}
$('copy-revamp-install').addEventListener('click', async () => {
  const ok = await copyText($('revamp-install').textContent);
  aboutMessage(ok ? 'Install command copied — run it in a terminal.' : 'Copy failed; select the command above.');
});
$('copy-tests').addEventListener('click', async () => {
  const ok = await copyText($('tests-command').textContent);
  aboutMessage(ok ? 'Test command copied.' : 'Copy failed; select the command above.');
});
$('open-revamp-repo').addEventListener('click', () => openLink(aboutCache && aboutCache.revamp && aboutCache.revamp.repo));
$('open-contribute').addEventListener('click', () => openLink(aboutCache && aboutCache.paths && aboutCache.paths.contribute));
$('open-issue').addEventListener('click', () => openLink(aboutCache && aboutCache.paths && aboutCache.paths.issues));
$('copy-diagnostics').addEventListener('click', async () => {
  try {
    const data = await api('/about/diagnostics');
    const box = $('diagnostics-out');
    box.hidden = false;
    box.firstElementChild.textContent = data.text;
    const ok = await copyText(data.text);
    aboutMessage(ok ? 'Diagnostics copied — paste them into the issue.' : 'Copy failed; the report is shown above.');
  } catch (err) { aboutMessage(err.message, true); }
});
loadAbout().catch(err => aboutMessage(err.message, true));

function closeStudio() {
  if (!window.confirm('Close Music Studio? The window stops and the studio host shuts down. Your library and drafts stay on disk.')) return;
  const done = () => {
    document.body.replaceChildren();
    const screen = document.createElement('main');
    screen.className = 'closed-screen';
    const title = document.createElement('h1');
    title.textContent = 'Studio closed.';
    const line = document.createElement('p');
    line.textContent = 'The studio host has stopped and your library is untouched. You can close this window.';
    const reopen = document.createElement('a');
    reopen.href = location.href;
    reopen.textContent = 'Start it again';
    reopen.className = 'button primary';
    screen.append(title, line, reopen);
    document.body.append(screen);
    window.setTimeout(() => { try { window.close(); } catch (err) {} }, 400);
  };
  // Tell the Hermes pane first: a frame around a dead host is a blank window, so the pane
  // closes itself instead of framing nothing.
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'hermes-music-studio:closed' }, '*');
    }
  } catch (err) { /* no parent, or a parent that does not listen */ }
  // Whether the request answers or the socket dies with the process, the page says the same
  // thing: the studio is closing.
  api('/host/close', {}).then(done).catch(done);
}
if ($('close-studio')) $('close-studio').addEventListener('click', closeStudio);

if ($('open-revamp')) $('open-revamp').addEventListener('click', () => openLink('http://127.0.0.1:8114/'));

// ---------------------------------------------------------------- set up an engine
// Three paths on one screen, each of which tests before it saves. A setup step that cannot work
// says which check failed instead of returning a shrug.
async function setupMessage(text, isError) {
  const node = $('setup-message');
  if (!node) return;
  node.textContent = text || '';
  node.classList.toggle('error', !!isError);
}

function renderSetupChecks(box, checks) {
  if (!box) return;
  box.replaceChildren();
  (checks || []).forEach(check => {
    const row = el('p', check.ok ? 'hint' : 'hint error',
      `${check.ok ? '✓' : '✗'} ${check.name}${check.detail ? ' — ' + check.detail : ''}`);
    box.append(row);
  });
}

if ($('setup-panel')) {
  document.querySelectorAll('.setup-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const path = tab.dataset.path;
      ['server', 'hf', 'cloud'].forEach(name => {
        const body = $('setup-' + name);
        if (body) body.hidden = name !== path;
      });
      document.querySelectorAll('.setup-tab').forEach(other =>
        other.classList.toggle('primary', other === tab));
    });
  });

  if ($('setup-server-test')) $('setup-server-test').addEventListener('click', async () => {
    const url = ($('setup-server-url').value || '').trim();
    await setupMessage('Testing…');
    try {
      // A ComfyUI answers with a node list; an ACE-Step server does not. Try the richer check
      // first, then the ACE one, so the same box serves both.
      let result = await api('/studio/setup/comfy', { url }).catch(err => ({ detail: err.message }));
      let kind = 'ComfyUI';
      if (!result || result.ok !== true) {
        const ace = await api('/studio/setup/ace', { url }).catch(err => ({ detail: err.message }));
        if (ace && ace.ok) { result = ace; kind = 'ACE-Step'; }
      }
      const box = $('setup-checks');
      if (box) renderSetupChecks(box, result.checks);
      if (result.ok) {
        await setupMessage(`${kind} is ready${result.saved ? ' and saved' : ''}. ${result.detail || ''}`);
        if (typeof loadStatus === 'function') loadStatus();
      } else {
        await setupMessage(result.detail || 'That address did not answer as a renderer.', true);
      }
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });

  const hfPoll = async () => {
    const box = $('setup-hf-status');
    const data = await api('/studio/setup/hf').catch(() => null);
    if (box && data && data.status) {
      renderSetupChecks(box, [{ name: data.status.model_id || 'no download yet',
        ok: !data.status.running, detail: data.status.detail }]);
      if (data.status.done) {
        await setupMessage(`Downloaded. ${data.status.detail}`);
        return;
      }
      if (data.status.running) {
        window.setTimeout(hfPoll, 2000);
        return;
      }
      if ((data.status.detail || '').startsWith('download failed')) {
        await setupMessage(data.status.detail, true);
      }
    }
  };

  if ($('setup-hf-plan')) $('setup-hf-plan').addEventListener('click', async () => {
    const model_id = ($('setup-hf-id').value || '').trim();
    try {
      const plan = await api('/studio/setup/hf', { model_id });
      const box = $('setup-hf-status');
      if (box) {
        box.replaceChildren();
        const lines = [
          `Download folder: ${plan.plan.download_to}`,
          plan.plan.local_comfy
            ? `Local ComfyUI found: ${plan.plan.local_comfy} — copy it into models/diffusion_models`
            : 'No local ComfyUI found: copy the weights into that machine’s models folder yourself',
          plan.plan.where_it_goes,
          plan.plan.size_hint,
        ];
        lines.forEach(line => box.append(el('p', 'hint', line)));
      }
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });

  if ($('setup-hf-start')) $('setup-hf-start').addEventListener('click', async () => {
    const model_id = ($('setup-hf-id').value || '').trim();
    try {
      await api('/studio/setup/hf', { model_id, start: true });
      await setupMessage('Download started. This can take a while; the status below follows it.');
      hfPoll();
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });

  const renderCloud = async () => {
    const box = $('setup-cloud-list');
    if (!box) return;
    const data = await api('/studio/setup/cloud').catch(() => ({ endpoints: [] }));
    box.replaceChildren();
    (data.endpoints || []).forEach(entry => {
      box.append(el('p', 'hint',
        `${entry.name} · ${entry.url}${entry.model ? ' · ' + entry.model : ''}${entry.has_key ? ' · key saved' : ''}`));
    });
    if (!(data.endpoints || []).length) box.append(el('p', 'hint', 'No hosted endpoint saved yet.'));
  };

  if ($('setup-cloud-test')) $('setup-cloud-test').addEventListener('click', async () => {
    const name = ($('setup-cloud-name').value || 'default').trim() || 'default';
    try {
      await api('/studio/setup/cloud', {
        name, url: ($('setup-cloud-url').value || '').trim(),
        key: $('setup-cloud-key').value || '',
        model: ($('setup-cloud-model') || {}).value || '',
      });
      const result = await api('/studio/setup/cloud/test', { name });
      await setupMessage(result.detail, !result.ok);
      await renderCloud();
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });
  renderCloud();
}

// ---------------------------------------------------------------- version and update
// The chip in the top bar already proves which build answers. This panel is the same fact with the
// next build beside it, and one button that installs it.
const versionState = { latest: null, installed: null, polling: null };

async function refreshVersion() {
  const line = $('version-line');
  try {
    const data = await api('/update');
    versionState.installed = data.installed || {};
    versionState.latest = data.latest || null;
    if (line) {
      const here = data.installed || {};
      const parts = ['v' + (here.version || 'unknown')];
      parts.push(here.commit_short || 'no commit');
      line.textContent = 'Installed: ' + parts.join(' · ') +
        (data.latest ? ` · published: ${data.latest.commit_short} (${(data.latest.message || '').slice(0, 60)})` : '');
    }
    const update = $('version-update');
    if (update) update.disabled = !data.update_available;
    const undo = $('version-undo');
    if (undo) undo.disabled = !versionState.installed.commit;
    await setupMessage(data.detail || '', data.ok === false);
    return data;
  } catch (err) {
    if (line) line.textContent = 'Could not read the version: ' + err.message;
    return null;
  }
}

async function watchUpdateJob() {
  const message = $('version-message');
  const logBox = $('version-log');
  const poll = async () => {
    const job = await api('/update/job').catch(() => null);
    if (!job) return;
    if (logBox && job.log) {
      logBox.hidden = false;
      logBox.querySelector('code').textContent = job.log;
    }
    if (job.state === 'running') {
      if (message) message.textContent = 'Installing… the studio restarts itself when it finishes.';
      window.setTimeout(poll, 2000);
      return;
    }
    if (job.state === 'idle') return;
    if (message) {
      message.textContent = job.mode === 'undo'
        ? 'Went back to the previous build. Reloading…'
        : 'Installed. Reloading…';
    }
    window.setTimeout(() => window.location.reload(), 1500);
  };
  poll();
}

if ($('version-panel')) {
  refreshVersion();
  if ($('version-check')) $('version-check').addEventListener('click', refreshVersion);
  if ($('version-update')) $('version-update').addEventListener('click', async () => {
    if (!window.confirm('Install the newest published build now? The studio restarts when it finishes.')) return;
    try {
      await api('/update/apply', {});
      await setupMessage('Update started.');
      watchUpdateJob();
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });
  if ($('version-undo')) $('version-undo').addEventListener('click', async () => {
    if (!window.confirm('Go back to the build that was installed before the last update?')) return;
    try {
      await api('/update/undo', {});
      await setupMessage('Rolling back.');
      watchUpdateJob();
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });
}
// An open "More" menu closes when you click anywhere else, like any menu.
document.addEventListener('click', e => {
  document.querySelectorAll('details.take-more[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
});
