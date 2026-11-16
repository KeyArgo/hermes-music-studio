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
let current = null, dirty = false, busy = false, revision = 0, engines = [], projects = [], workspaces = [], conversation = [], libraryFilter = null, modelCatalog = [], targets = [], workflows = [], targetProbes = {}, hardware = null, hardwareProblem = '', hardwareFetchedAt = 0, refreshing = false, pollTimer = null;
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
async function api(path, data, method, options) {
  const opts = method ? {method} : data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)};
  if (method && data !== undefined) { opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(data); }
  // A request started from pagehide is cancelled with the page unless the browser is told to
  // keep it alive. Only the caller knows whether this is a last-gasp save, so it is opt-in.
  if (options && options.keepalive) opts.keepalive = true;
  opts.headers = withToken(opts.headers);
  const res = await fetch(STUDIO.api + path, opts);
  const value = await res.json();
  // Suggest may answer with a string or null. Only an object carries success, detail, or error.
  const failed = !!(value && typeof value === 'object' && value.success === false);
  if (!res.ok || failed) {
    const err = new Error((value && (value.detail || value.error)) || 'Request failed');
    err.commands = value && value.commands;
    throw err;
  }
  return value;
}
// The desktop pane frames this page without allow-modals, so window.confirm() silently says no
// there. One in-page dialog keeps every guarded action working in the pane and in a browser tab.
function askConfirm(message, options = {}) {
  const confirmLabel = options.confirmLabel || 'Confirm', danger = options.danger === true;
  return new Promise(resolve => {
    const prior = document.activeElement;
    const overlay = el('div', '', 'confirm-overlay');
    const dialog = el('div', '', 'confirm-dialog' + (danger ? ' confirm-danger' : ''));
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    const text = el('p', String(message), 'confirm-message');
    const actions = el('div', '', 'confirm-actions');
    const cancel = el('button', 'Cancel', 'button secondary confirm-cancel');
    cancel.type = 'button';
    const confirm = el('button', confirmLabel, 'button confirm-confirm' + (danger ? ' danger' : ' primary'));
    confirm.type = 'button';
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      document.removeEventListener('keydown', onKey, true);
      overlay.remove();
      if (prior && prior.isConnected && typeof prior.focus === 'function') {
        try { prior.focus(); } catch (err) { /* the old focus target is gone; nothing to restore */ }
      }
      resolve(value);
    };
    function onKey(event) {
      // Enter is left to the focused button (Cancel by default), so a stray Enter never deletes.
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
    }
    cancel.addEventListener('click', () => finish(false));
    confirm.addEventListener('click', () => finish(true));
    overlay.addEventListener('click', event => { if (event.target === overlay) finish(false); });
    actions.append(cancel, confirm);
    dialog.append(text, actions);
    overlay.append(dialog);
    document.body.append(overlay);
    document.addEventListener('keydown', onKey, true);
    cancel.focus();
  });
}
function selectedEngine() {
  return engines.find(e => e.id === form.elements.engine.value) || null;
}
// ComfyUI engines are MiniMax and uploaded workflows. ACE-Step is not one of them, so a missing
// ComfyUI must not be announced while ACE is the engine on screen.
function isComfyEngine(id) {
  const engineId = id == null ? form.elements.engine.value : id;
  if (engineId === 'minimax_m3_comfy' || String(engineId).startsWith('comfy_workflow:')) return true;
  const engine = engines.find(e => e.id === engineId);
  if (!engine) return false;
  return [engine.label, ...(engine.requirements || [])].join(' ').toLowerCase().includes('comfy');
}
function engineReady() {
  const engineId = form.elements.engine.value;
  const targetId = form.elements.comfy_target_id ? form.elements.comfy_target_id.value : '';
  if (engineId === 'minimax_m3_comfy' && targetId) return targetProbes[targetId]?.available === true;
  return !!selectedEngine()?.available;
}
// The reason a person can act on. /status sends selectable_reason; `reason` is not a field.
function engineProblem() {
  const engineId = form.elements.engine.value;
  const engine = selectedEngine();
  if (engineId === 'minimax_m3_comfy' && form.elements.comfy_target_id.value) {
    const targetId = form.elements.comfy_target_id.value;
    const name = targetName(targetId) || targetId;
    const probe = targetProbes[targetId];
    if (!probe) return `Target ${name} has not been tested; test it before rendering.`;
    if (!probe.available) return `Target ${name} unavailable${probe.detail ? ': ' + probe.detail : '; test it again.'}`;
    return '';
  }
  if (!engine) return 'Engine status unavailable; check setup.';
  if (engine.available) return '';
  return engine.selectable_reason || (engine.status && engine.status.detail) || engine.error || 'check setup';
}
function engineStatusLine() {
  const engine = selectedEngine();
  const isMini = form.elements.engine.value === 'minimax_m3_comfy';
  const problem = engineProblem();
  if (isMini && form.elements.comfy_target_id.value) {
    if (!problem) {
      const name = targetName(form.elements.comfy_target_id.value) || form.elements.comfy_target_id.value;
      return `Target ${name} is available.`;
    }
    return problem;
  }
  if (!engine) return problem || 'Engine status unavailable; check setup.';
  if (!problem) return isMini ? `${engine.label || engine.id} default endpoint is ready.` : `${engine.label || engine.id} is ready.`;
  return isMini
    ? `${engine.label || engine.id} default endpoint unavailable: ${problem}`
    : `${engine.label || engine.id} unavailable: ${problem}`;
}
function draftNeedsWords() {
  return !form.elements.caption.value.trim() && !form.elements.lyrics.value.trim();
}
function buttons() {
  const submit = form.querySelector('[type=submit]');
  if (submit) submit.disabled = busy;
  $('new-song').disabled = busy;
  $('nav-library').disabled = busy;
  $('nav-workspaces').disabled = busy;
  const navEngines = $('nav-engines') || $('nav-compute');
  if (navEngines) navEngines.disabled = busy;
  $('nav-listen').disabled = busy;
  const ready = engineReady();
  const wordsMissing = draftNeedsWords();
  const renderBtn = $('render');
  const reason = $('generate-reason');
  let why = '';
  if (!ready) {
    // The same button is the way out: setup, not a disabled Generate that explains nothing.
    renderBtn.textContent = 'Set up a music engine';
    renderBtn.disabled = false;
    why = engineProblem();
  } else if (busy) {
    renderBtn.textContent = 'Generate song';
    renderBtn.disabled = true;
    why = 'Rendering…';
  } else if (wordsMissing) {
    renderBtn.textContent = 'Generate song';
    renderBtn.disabled = true;
    why = 'Write a style or lyrics first';
  } else {
    renderBtn.textContent = 'Generate song';
    renderBtn.disabled = false;
  }
  if (reason) {
    reason.textContent = why;
    reason.hidden = !why;
  }
  renderBtn.title = why;
  if (why) renderBtn.setAttribute('aria-describedby', 'generate-reason');
  else renderBtn.removeAttribute('aria-describedby');
  const chorus = $('preview-chorus');
  const chorusReason = $('chorus-reason');
  if (chorus) {
    // A chorus preview still needs a saved draft: it renders four lines of that saved text.
    let chorusWhy = '';
    if (busy) chorusWhy = 'Rendering…';
    else if (!ready) chorusWhy = why || 'Set up a music engine before previewing the chorus.';
    else if (wordsMissing) chorusWhy = 'Write a style or lyrics first';
    else if (!current || dirty) chorusWhy = 'Save the draft before previewing the chorus.';
    chorus.disabled = !!(busy || !ready || wordsMissing || !current || dirty);
    chorus.title = chorus.disabled ? chorusWhy : '';
    if (chorusReason) {
      const duplicate = !!(why && chorusWhy === why);
      chorusReason.hidden = !chorusWhy || duplicate;
      chorusReason.textContent = duplicate ? '' : chorusWhy;
    }
  }
  if ($('render-takes')) $('render-takes').disabled = busy;
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
let presets = [], presetId = '';
function presetKey(preset) { return preset.preset_id != null ? preset.preset_id : preset.name; }
function builtinPreset(preset) { return !!preset && (preset.builtin === true || preset.built_in === true); }
function updatePresetDelete() {
  const found = presets.find(p => String(presetKey(p)) === String(presetId));
  $('preset-delete').hidden = !found || builtinPreset(found);
}
function renderPresets() {
  const select = $('preset-select');
  select.replaceChildren();
  const blank = el('option', 'Choose a preset…');
  blank.value = '';
  select.append(blank);
  for (const preset of presets) {
    const option = el('option', preset.name || presetKey(preset));
    option.value = String(presetKey(preset));
    select.append(option);
  }
  const known = presets.some(p => String(presetKey(p)) === String(presetId));
  select.value = known ? String(presetId) : '';
  presetId = select.value;
  updatePresetDelete();
}
async function loadPresets() {
  try {
    const data = await api('/presets');
    presets = Array.isArray(data.presets) ? data.presets : [];
    renderPresets();
  } catch (err) {
    presets = []; presetId = '';
    renderPresets();
    $('preset-select').title = 'Presets are unavailable right now.';
  }
}
function applyPreset(id) {
  const found = presets.find(p => String(presetKey(p)) === String(id));
  if (!found) return;
  presetId = String(id);
  const source = found.draft || found.params || found;
  const caption = source.caption != null ? source.caption : source.style;
  let changed = false;
  if (typeof caption === 'string') { form.elements.caption.value = caption; changed = true; }
  if (typeof source.instrumental === 'boolean') { form.elements.instrumental.checked = source.instrumental; changed = true; }
  if (source.bpm !== undefined && source.bpm !== null && source.bpm !== '') { form.elements.bpm.value = source.bpm; changed = true; }
  if (typeof source.keyscale === 'string' && source.keyscale) { form.elements.keyscale.value = source.keyscale; changed = true; }
  if (typeof source.vocal_language === 'string' && source.vocal_language) { form.elements.vocal_language.value = source.vocal_language; changed = true; }
  if (source.duration_seconds !== undefined && source.duration_seconds !== null && source.duration_seconds !== '') { form.elements.duration_seconds.value = source.duration_seconds; changed = true; }
  if (changed) { dirty = true; revision++; buttons(); }
  updatePresetDelete();
  message(`Applied preset “${found.name || presetKey(found)}”. Save the draft to keep it.`);
}
async function savePreset() {
  const name = $('preset-name').value.trim();
  if (!name) { message('Name the preset first.', true); $('preset-name').focus(); return; }
  const draft = fields();
  try {
    const data = await api('/presets', {name, caption: draft.caption, bpm: draft.bpm,
      keyscale: draft.keyscale, vocal_language: draft.vocal_language, instrumental: draft.instrumental});
    presets = Array.isArray(data.presets) ? data.presets : presets;
    presetId = name;
    $('preset-save-row').hidden = true;
    $('preset-name').value = '';
    renderPresets();
    message(`Saved preset “${name}”.`);
  } catch (err) { message(err.message, true); }
}
async function deletePreset() {
  const found = presets.find(p => String(presetKey(p)) === String(presetId));
  if (!found) { message('Choose a preset to delete.', true); return; }
  if (builtinPreset(found)) { message('Built-in presets cannot be deleted.', true); return; }
  const key = presetKey(found);
  try {
    const data = await api('/presets/' + encodeURIComponent(key), undefined, 'DELETE');
    presets = Array.isArray(data.presets) ? data.presets : presets.filter(p => String(presetKey(p)) !== String(key));
    presetId = '';
    renderPresets();
    message(`Deleted preset “${found.name || key}”.`);
  } catch (err) { message(err.message, true); }
}
async function loadProject(p) {
  if (dirty && !(await askConfirm('Discard unsaved changes?', {confirmLabel:'Discard', danger:true}))) return;
  followed = null;
  current = p;
  try { localStorage.setItem('studio.lastProject', p.project_id); } catch (e) {}
  ensureEngineOption(p.engine);
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
  const isMini = form.elements.engine.value === 'minimax_m3_comfy';
  $('minimax-guidance-row').hidden = !isMini;
  $('target-row').hidden = !isMini;
  $('engine-hint').textContent = engineStatusLine();
}
function show(view) {
  for (const name of ['create','library','workspaces','engines','listen']) {
    const viewEl = $(name+'-view') || (name === 'engines' ? $('compute-view') : null);
    if (viewEl) viewEl.hidden = view !== name;
    const navEl = $('nav-'+name) || (name === 'engines' ? $('nav-compute') : null);
    if (navEl) navEl.classList.toggle('active',view === name);
  }
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
function demoTitle(meta) {
  const named = meta && (meta.title || meta.name);
  if (typeof named === 'string' && named.trim()) return named.trim();
  const caption = meta && typeof meta.caption === 'string' ? meta.caption.trim() : '';
  if (caption) {
    const words = caption.split(/[,;.]/)[0].trim().split(/\s+/).filter(Boolean).slice(0, 6);
    if (words.length) {
      const phrase = words.join(' ');
      return phrase.charAt(0).toUpperCase() + phrase.slice(1);
    }
  }
  return 'Demo song';
}
async function playDemo(event) {
  const button = event && event.currentTarget;
  if (button) button.disabled = true;
  try {
    const meta = await demoMeta();
    showDemoFacts(meta);
    const caption = meta.caption || 'A real take made with this studio.';
    const facts = demoFacts(meta);
    play(STUDIO.api + '/demo/audio', demoTitle(meta), facts ? `${caption} · ${facts}` : caption);
  } catch (err) {
    playerProblem('Could not load the demo: ' + err.message);
  } finally {
    if (button) button.disabled = false;
  }
}
// ---------------------------------------------------------------- waveform strip
// The peaks route already exists server-side; the footer asks it once per take (and once for the
// demo) and draws the buckets on a small canvas. If it is unavailable the range input is the control.
const PEAK_BUCKETS = 256;
const peakCache = new Map();
let wave = null, waveSequence = 0, waveDragging = false;

function takeRef(url) {
  const raw = String(url || '');
  if (/\/demo\/audio(?:[?#]|$)/.test(raw)) return { demo: true };
  const hit = /\/projects\/([^/?#]+)\/takes\/([^/?#]+)\//.exec(raw);
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
  const key = ref.demo ? 'demo' : `${ref.project}/${ref.take}${editId ? '@' + editId : ''}`;
  const cached = peakCache.get(key);
  if (cached) { wave = { key, values: cached.values, duration: cached.duration }; showWave(); return; }
  const query = `?buckets=${PEAK_BUCKETS}` + (editId && !ref.demo ? '&edit=' + encodeURIComponent(editId) : '');
  const path = ref.demo ? `/demo/peaks${query}`
    : `/projects/${encodeURIComponent(ref.project)}/takes/${encodeURIComponent(ref.take)}/peaks${query}`;
  api(path)
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
let libraryRefreshPending = false;
// The poll rebuilds the library from /status, but a rebuild would throw away an inline Rename,
// change-words box, snippet/extend form or open More menu — and the text typed into it.
function libraryHasOpenEditor() {
  const container = $('library');
  if (!container) return false;
  if (container.contains(document.activeElement)) return true;
  if (container.querySelector('.rename-input, .snippet-form, .extend-form, details.take-more[open]')) return true;
  return [...container.querySelectorAll('.project-change')].some(node => !node.hidden);
}
function renderLibrary() {
  if (libraryHasOpenEditor()) { libraryRefreshPending = true; return; }
  libraryRefreshPending = false;
  library();
}
function refreshLibraryWhenIdle() {
  if (!libraryRefreshPending || libraryHasOpenEditor()) return;
  libraryRefreshPending = false;
  library();
}
// Focus and menu changes close an editor after the event, so look again once it has settled.
for (const type of ['focusout', 'click', 'keyup']) {
  document.addEventListener(type, () => setTimeout(refreshLibraryWhenIdle, 0));
}
function library() {
  libraryRefreshPending = false;
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
    const rename = el('button','Rename','button secondary'); rename.type='button'; rename.addEventListener('click', () => {
      const input = el('input'); input.type='text'; input.className='rename-input'; input.value = p.title || '';
      title.replaceWith(input); input.focus(); input.select();
      let settled = false;
      const restore = () => { if (settled) return; settled = true; input.replaceWith(title); };
      input.addEventListener('keydown', async e => {
        if (e.key === 'Escape') { e.preventDefault(); restore(); title.focus(); return; }
        if (e.key !== 'Enter' || settled) return;
        e.preventDefault();
        const value = input.value.trim();
        if (!value) { libMessage('A title is required.', true); return; }
        settled = true;
        try {
          // /rename, not /update: a title is not part of a render, so it must not un-approve the draft.
          await api('/rename',{project_id:p.project_id,title:value});
          libMessage('Renamed.'); await refresh(); library();
        } catch(err){ settled = false; input.replaceWith(title); libMessage(err.message, true); }
      });
    });
    const del = el('button','Delete','button secondary'); del.type='button'; del.addEventListener('click', async () => {
      if (!(await askConfirm(`Delete \u201c${p.title || 'Untitled'}\u201d and all of its audio? This cannot be undone.`, {confirmLabel:'Delete', danger:true}))) return;
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
    const identity=el('div','','project-identity'); if (p.has_cover !== false) { const cover=document.createElement('img'); cover.className='project-cover'; cover.alt=''; cover.loading='lazy'; cover.src=mediaUrl(STUDIO.api+'/studio/covers/'+encodeURIComponent(p.project_id)); cover.addEventListener('error',()=>cover.remove()); identity.append(cover); } identity.append(title); const actions = el('div','','project-actions'); actions.append(edit, ver, cmp, dup, rename, del); head.append(identity,actions); card.append(head,el('p',p.caption || 'No style description yet.'));
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
    takes.forEach((t, i) => { const row = el('div','','take'); row.dataset.take = t.take_id; let detail = `Take ${i+1} · ${t.engine_id || p.engine || 'engine'} · ${t.state || 'unknown'} · seed ${t.requested_seed ?? t.seed ?? '—'}`;
      const targetLabel = takeTargetLabel(t);
      if (targetLabel) detail += ` · ${targetLabel}`;
      if (t.state === 'running') detail += ` · estimated ${Number(t.progress) || 0}% · estimated ETA ${formatEta(t.eta_seconds)}`;
      else if (t.state === 'queued') detail += ` · queued${t.queue_position ? ` (#${t.queue_position})` : ''}`;
      const label = el('span', detail); row.append(label);
      if (t.parent_take_id) {
        const parentIndex = takes.findIndex(item => item.take_id === t.parent_take_id);
        if (parentIndex >= 0) {
          const lineage = el('button', `from take ${parentIndex + 1} · ${t.kind || 'generate'}`, 'take-lineage');
          lineage.type = 'button';
          lineage.title = `Made from take ${parentIndex + 1} — show that take`;
          lineage.addEventListener('click', () => {
            const parentRow = [...card.querySelectorAll('.take')].find(node => node.dataset.take === t.parent_take_id);
            if (!parentRow) return;
            if (typeof parentRow.scrollIntoView === 'function') parentRow.scrollIntoView({behavior: 'smooth', block: 'center'});
            parentRow.classList.add('take-highlight');
            clearTimeout(parentRow.__highlightTimer);
            parentRow.__highlightTimer = window.setTimeout(() => parentRow.classList.remove('take-highlight'), 1600);
          });
          row.append(lineage);
        }
      }
      if (t.state === 'succeeded') {
        const evidence = takeEvidenceBadges(t, p);
        if (evidence.length) {
          const wrap = el('span', '', 'take-evidence');
          wrap.append(...evidence);
          row.append(wrap);
        }
      }
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
        // An engine that declares continuation=false cannot extend, so the menu says so plainly
        // instead of offering a button that the server would refuse.
        const extend = el('button', canExtend ? 'Extend…' : `Extend (not on ${engineName})`, 'button secondary');
        extend.type = 'button';
        if (canExtend) {
          extend.title = 'Add 10–60 seconds to the end as a new take';
          extend.addEventListener('click', () => extendRow(p, t, row));
        } else {
          extend.disabled = true;
          extend.title = `${engineName} cannot extend a take (continuation is not supported by this engine).`;
        }
        row.append(extend);
      }
      if (t.section_of && t.section_of.take_id) {
        const splice = el('button','Splice into song','button primary'); splice.type='button';
        splice.addEventListener('click', async () => { try { await api('/section/splice',{project_id:p.project_id,take_id:t.section_of.take_id,candidate_take_id:t.take_id}); libMessage('Spliced: a new take of the whole song, with only that part replaced.'); await refresh(); library(); } catch(err){ libMessage(err.message, true); } });
        row.append(splice);
      }
      const killTake = el('button','Delete take','button secondary'); killTake.type='button'; killTake.addEventListener('click', async () => { if(!(await askConfirm(`Delete take ${i+1} and its audio?`, {confirmLabel:'Delete', danger:true}))) return; try { await api('/take/delete',{project_id:p.project_id,take_id:t.take_id}); libMessage('Take deleted.'); await refresh(); library(); } catch(err){ libMessage(err.message, true); } }); row.append(killTake);
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
// Play, Export and the lineage link stay on the row; every other take action moves into a "More"
// menu. The buttons are moved, not rebuilt, so each keeps exactly the handler it was made with.
function tidyTakeRow(row) {
  const keep = b => /Play$/.test(b.textContent.trim()) || b.textContent.trim() === 'Export'
    || b.classList.contains('take-lineage');
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
// What the take-check/ASR pass actually measured, and nothing else: an unknown value gets no
// badge at all. The words come from qc.vocal_evidence only when a real ASR run wrote it.
function takeEvidenceBadges(take, project) {
  const badges = [];
  const evidence = (take.qc || {}).vocal_evidence || {};
  const asr = evidence.source === 'asr'
    && Number.isInteger(evidence.words_recognised) && Number.isInteger(evidence.total)
    && evidence.total > 0 && evidence.words_recognised >= 0
    && evidence.words_recognised <= evidence.total;
  if (asr) {
    const words = `${evidence.words_recognised}/${evidence.total}`;
    const tone = evidence.words_recognised === evidence.total ? 'good'
      : evidence.words_recognised > 0 ? 'warn' : 'bad';
    const badge = el('span', `words ${words}`, `take-badge ${tone}`);
    badge.title = `Take check: the ASR pass recognised ${evidence.words_recognised} of ${evidence.total} words in this take.`;
    badges.push(badge);
  }
  const snapshot = take.snapshot || {};
  const instrumental = snapshot.instrumental !== undefined ? snapshot.instrumental : project.instrumental;
  let vocal = null;
  if (instrumental === true) {
    vocal = {text: 'instrumental', tone: '', title: 'This take was rendered as instrumental, so no vocal was requested.'};
  } else if (evidence.status === 'confirmed') {
    vocal = {text: 'vocals ✓', tone: 'good',
      title: asr ? `Take check: the ASR pass recognised ${evidence.words_recognised}/${evidence.total} words, so a vocal is confirmed.`
        : 'Take check: an ASR pass confirmed a vocal in this take.'};
  } else if (evidence.status === 'not_confirmed') {
    vocal = {text: 'no vocals heard', tone: 'warn',
      title: asr ? `Take check: the ASR pass recognised ${evidence.words_recognised}/${evidence.total} words, below the vocal threshold.`
        : 'Take check: an ASR pass did not confirm a vocal in this take.'};
  }
  if (vocal) {
    const badge = el('span', vocal.text, `take-badge${vocal.tone ? ' ' + vocal.tone : ''}`);
    badge.title = vocal.title;
    badges.push(badge);
  }
  return badges;
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
// The take this page just asked for. Progress and the finished player stay on Create until
// the person opens something else.
let followed = null;
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
function paintActive(project, take) {
  const panel = $('active-take'), running = take.state === 'running';
  const playback = $('active-playback');
  if (playback) playback.hidden = true;
  const progress = running ? Math.max(0, Math.min(99, Number(take.progress) || 0)) : 0;
  panel.hidden = false;
  $('active-title').textContent = (project.title || 'Untitled') + ' · Take ' + ((project.takes || []).findIndex(t => t.take_id === take.take_id) + 1);
  $('active-state').textContent = running ? 'Rendering' : 'Queued';
  $('active-progress-bar').style.width = progress + '%';
  $('active-progress-bar').parentElement.setAttribute('aria-valuenow', String(progress));
  const elapsed = running && take.started_at ? Math.max(0, Math.round(Date.now() / 1000 - take.started_at)) : null;
  $('active-progress').textContent = running
    ? `estimated ${progress}% complete · ${elapsed != null ? formatEta(elapsed) + ' working · ' : ''}` +
      `estimated ETA ${formatEta(take.eta_seconds)} left`
    : `Waiting for the GPU${take.queue_position ? ` · queue #${take.queue_position}` : ''}.`;
  const engine = take.engine_id || project.engine || 'engine', target = takeTargetLabel(take);
  $('active-detail').textContent = `Engine: ${engine}${target ? ` · ${target}` : ''}`;
  const gpu = running ? gpuForTake(take) : null;
  $('active-gpu').hidden = !running;
  if (gpu) $('active-gpu').textContent = `GPU ${gpu.index}: ${gpu.name} · PID ${gpu.pid} matched in nvidia-smi`;
  else if (running) $('active-gpu').textContent = 'GPU not identified';
}
function playTake(project, take, index) {
  play(STUDIO.api + `/projects/${encodeURIComponent(project.project_id)}/takes/${encodeURIComponent(take.take_id)}/audio`,
    project.title || 'Untitled', `Take ${index}`, take.playable_edit_id);
}
function showFinishedTake(project, takes) {
  const panel = $('active-take');
  const succeeded = takes.filter(t => t.state === 'succeeded');
  const take = succeeded[succeeded.length - 1];
  if (!panel || !take) return;
  const index = Math.max(1, (project.takes || []).findIndex(t => t.take_id === take.take_id) + 1);
  panel.hidden = false;
  $('active-title').textContent = `${project.title || 'Untitled'} · Take ${index}`;
  $('active-state').textContent = 'Ready';
  $('active-progress-bar').style.width = '100%';
  $('active-progress-bar').parentElement.setAttribute('aria-valuenow', '100');
  $('active-progress').textContent = succeeded.length > 1 ? `${succeeded.length} takes are ready to play.` : 'Finished. Play it here.';
  $('active-detail').textContent = '';
  $('active-gpu').hidden = true;
  const box = $('active-playback');
  if (!box) return;
  box.hidden = false;
  box.replaceChildren();
  const playBtn = el('button', '▶ Play', 'button secondary');
  playBtn.type = 'button';
  playBtn.id = 'active-play';
  playBtn.addEventListener('click', () => playTake(project, take, index));
  const lib = el('button', 'Open in Library', 'link-button');
  lib.type = 'button';
  lib.id = 'active-library';
  lib.addEventListener('click', () => { libraryFilter = null; library(); show('library'); });
  box.append(playBtn, lib);
  if (followed && !followed.played) {
    followed.played = true;
    playTake(project, take, index);
    message('Your song is ready. It is playing here, and it is in the Library.');
  }
}
function showFailedTake(takes) {
  const panel = $('active-take');
  const take = takes.find(t => t.state === 'failed') || takes[0];
  if (!panel) return;
  panel.hidden = false;
  const box = $('active-playback');
  if (box) box.hidden = true;
  $('active-state').textContent = 'Failed';
  $('active-title').textContent = 'Render failed';
  $('active-progress-bar').style.width = '0%';
  $('active-progress').textContent = (take && take.error) || 'The render failed.';
  $('active-detail').textContent = '';
  $('active-gpu').hidden = true;
  if (followed && !followed.failed) {
    followed.failed = true;
    message($('active-progress').textContent, true);
  }
}
function renderActiveTake() {
  const panel = $('active-take');
  if (!panel) return;
  if (followed) {
    const project = projects.find(p => p.project_id === followed.projectId);
    const mine = project ? (project.takes || []).filter(t => followed.ids.includes(t.take_id)) : [];
    const live = mine.find(t => t.state === 'running') || mine.find(t => t.state === 'queued');
    if (live && project) { paintActive(project, live); return; }
    if (mine.length && mine.every(t => t.state === 'succeeded' || t.state === 'failed')) {
      if (mine.some(t => t.state === 'succeeded')) { showFinishedTake(project, mine); return; }
      showFailedTake(mine);
      return;
    }
  }
  const playback = $('active-playback');
  if (playback) playback.hidden = true;
  const active = findActiveTake();
  if (!active) { panel.hidden = true; return; }
  paintActive(active.project, active.take);
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
// Plug-and-play. A fresh install has no ComfyUI address, so every engine reads unavailable and the
// person is told to go hunting in Compute. Ask the local network once instead, and adopt the only
// answer; if there are several, say so and let them choose. Never overwrites a saved address.
let autoDetected = false;
async function autoDetectEngine() {
  // Only a ComfyUI engine is looking for ComfyUI. ACE-Step has its own setup, and telling
  // that person that no ComfyUI answered sends them to the wrong place.
  if (autoDetected || !isComfyEngine(form.elements.engine.value)) return;
  autoDetected = true;
  try {
    const configured = (await api('/settings')).settings || {};
    if (configured.comfy_url) return;
    const found = await api('/discover', {});
    const urls = (found.found || []).map(item => item.url || item.base_url).filter(Boolean);
    if (!urls.length) { message('No ComfyUI answered on this machine. Open Compute to point the studio at one.'); return; }
    if (urls.length > 1) { message(`Found ${urls.length} ComfyUI servers — choose one in Compute.`); return; }
    await api('/settings', {comfy_url: urls[0]});
    message(`Found ComfyUI at ${urls[0]} and set it up.`);
    await refresh();
  } catch (e) { /* discovery is a convenience, never a failure the person must read */ }
}
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const data = await api('/status');
    engines = data.engines || []; projects = data.projects || [];
    const ready = engines.filter(e => e.available).length;
    if (!ready && !autoDetected) autoDetectEngine();
    $('engine-status').textContent = ready ? `${ready} engine${ready===1?'':'s'} ready` : 'No engine ready';
    engineHint(); buttons(); renderLibrary();
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
$('preset-select').addEventListener('change', e => {
  presetId = e.target.value;
  if (presetId) applyPreset(presetId); else updatePresetDelete();
});
$('preset-save').addEventListener('click', () => {
  const row = $('preset-save-row');
  row.hidden = !row.hidden;
  if (!row.hidden) { $('preset-name').value = ''; $('preset-name').focus(); }
});
$('preset-save-confirm').addEventListener('click', savePreset);
$('preset-save-cancel').addEventListener('click', () => { $('preset-save-row').hidden = true; $('preset-name').value = ''; });
$('preset-name').addEventListener('input', e => e.stopPropagation());
$('preset-name').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); savePreset(); } });
$('preset-delete').addEventListener('click', deletePreset);
form.elements.engine.addEventListener('change', () => {
  engineHint();
  buttons();
  if (!isComfyEngine(form.elements.engine.value)) {
    const node = $('form-message');
    if (node && /No ComfyUI answered|Found \d+ ComfyUI|Found ComfyUI at/.test(node.textContent)) node.textContent = '';
    return;
  }
  if (!engines.some(e => e.available)) autoDetectEngine();
});
form.elements.comfy_target_id.addEventListener('change',()=>{targetProbes={};engineHint();buttons();});
form.elements.minimax_guidance.addEventListener('input',()=>{$('minimax-guidance-value').value=form.elements.minimax_guidance.value;});
// Autosave. Two failures this prevents: closing the pane mid-thought loses the draft, and a
// person cannot tell whether their typing reached the library. Debounced, silent, never an
// approval — a saved draft is still unapproved until Generate song approves it.
let autosaveTimer = null, autosaving = false, lastSavedAt = 0;
function draftHasContent(d) { return Boolean((d.title || '').trim() || (d.caption || '').trim() || (d.lyrics || '').trim()); }
function draftStateLabel(text) { const pill = $('draft-state'); if (pill) pill.textContent = text; }
// The pane frames this page without allow-same-origin, so touching localStorage throws there.
// Everything below goes through try/catch: the server save is what matters, and storage is only
// the bonus that survives a reload in an ordinary browser tab.
function saveBackup(draft) {
  try { localStorage.setItem('studio.draftBackup', JSON.stringify({project_id: current ? current.project_id : null, draft, at: Date.now()})); } catch (e) {}
}
function readBackup() {
  try { const raw = localStorage.getItem('studio.draftBackup'); return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
}
// undefined means storage itself is unavailable; null means it works but remembers nothing.
function readLastProject() {
  try { return localStorage.getItem('studio.lastProject'); } catch (e) { return undefined; }
}
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(autosave, 1200);
}
async function saveDraft(draft, keepalive, unguarded) {
  autosaving = true;
  const savedRevision = revision;
  // `unguarded` drops the stale-draft token. Only the leaving save uses it, and only while an
  // earlier save of this same draft is still in the air: that save is about to move the token,
  // so the leaving one would be refused as stale and the last keystrokes lost with the page.
  const update = {project_id: current && current.project_id, draft};
  if (!unguarded && current) update.base_updated_at = current.updated_at;
  try {
    const data = current
      ? await api('/update', update, undefined, {keepalive})
      : await api('/create', {draft}, undefined, {keepalive});
    current = data.project;
    dirty = revision !== savedRevision;
    lastSavedAt = Date.now();
    // Keep the backup while newer keystrokes are still unsaved: they exist nowhere else yet.
    try { localStorage.setItem('studio.lastProject', current.project_id); if (!dirty) localStorage.removeItem('studio.draftBackup'); } catch (e) {}
    draftStateLabel('Saved ' + new Date(lastSavedAt).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}));
    buttons();
  } catch (err) {
    // Keep the words on this machine until the server accepts them, and say so instead of
    // pretending they are safe.
    saveBackup(draft);
    draftStateLabel('Not saved — ' + (err && err.message ? err.message : 'retry'));
  } finally { autosaving = false; }
}
async function autosave() {
  // Busy or already saving is not a reason to drop the last keystrokes: come back shortly.
  if (busy || autosaving) { scheduleAutosave(); return; }
  const draft = fields();
  if (!draftHasContent(draft)) return;
  await saveDraft(draft, false);
}
// Leaving is the moment that matters most: a normal fetch started here dies with the page, so
// this one is keepalive. A create already in the air will land on its own, and sending a second
// one would turn a single draft into two songs.
function flushNow() {
  clearTimeout(autosaveTimer);
  autosaveTimer = null;
  if (autosaving && !current) return;
  if (!dirty) return;
  const draft = fields();
  if (!draftHasContent(draft)) return;
  saveDraft(draft, true, autosaving);
}
form.addEventListener('input', () => {
  dirty = true;
  // Written synchronously on every keystroke: if the page dies before the debounce fires, this
  // is the only copy of the words.
  saveBackup(fields());
  draftStateLabel('Saving…');
  scheduleAutosave();
});
window.addEventListener('pagehide', flushNow);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushNow(); });

function isStale(err) {
  return /stale draft/i.test((err && err.message) || '');
}
function staleExplanation() {
  return 'This draft changed before it could be approved (stale draft), so nothing was rendered. Open the latest version from the Library and press Generate song again.';
}
// The token /approve compares with !== inside the write lock, so it has to be the number the
// save just returned — not a string, and not the token from before this save.
function versionToken(project) {
  const token = project && project.updated_at;
  if (typeof token === 'number' && Number.isFinite(token)) return token;
  const number = typeof token === 'string' && token.trim() !== '' ? Number(token) : NaN;
  if (!Number.isFinite(number)) throw new Error('The saved draft has no version number, so it was not approved or rendered.');
  return number;
}
async function persistDraft() {
  const savedRevision = revision;
  const draft = fields();
  const workspace = $('workspace-select').value;
  const data = current
    ? await api('/update', {project_id: current.project_id, base_updated_at: current.updated_at, draft})
    : await api('/create', {draft});
  if (!data || !data.project || !data.project.project_id) throw new Error('The draft was not saved.');
  current = data.project;
  dirty = revision !== savedRevision;
  try {
    localStorage.setItem('studio.lastProject', current.project_id);
    if (!dirty) localStorage.removeItem('studio.draftBackup');
  } catch (e) {}
  await api('/studio/assign', {project_id: current.project_id, workspace_id: workspace});
  return current;
}
function noteFollowed(projectId, payload) {
  const list = payload && Array.isArray(payload.takes) ? payload.takes
    : (payload && payload.take ? [payload.take] : []);
  const ids = list.map(take => take && take.take_id).filter(Boolean);
  if (!ids.length) { followed = null; return; }
  followed = {projectId, ids, played: false, failed: false};
}
// ACE-Step local is configured in the setup panel. MiniMax and custom workflows use Compute.
function opensAceSetup(id) {
  return id === 'ace_local' || id === 'ace';
}
async function openEngineSetup() {
  const id = form.elements.engine.value;
  const ace = opensAceSetup(id);
  show('compute');
  const view = $('compute-view');
  if (view) view.dataset.setup = ace ? 'ace' : 'compute';
  try { await refreshTargets(); } catch (err) {}
  try { await refreshWorkflows(); } catch (err) {}
  try { await loadHardware(true); } catch (err) {}
  try { await loadSettings(); } catch (err) {}
  try { loadDoctor(); } catch (err) {}
  if (ace) {
    const tab = document.querySelector('.setup-tab[data-path="server"]');
    if (tab) tab.click();
    const panel = $('setup-panel');
    if (panel && typeof panel.scrollIntoView === 'function') panel.scrollIntoView({block: 'start'});
  }
  const focusId = ace ? 'setup-server-url'
    : (String(id).startsWith('comfy_workflow:') ? 'workflow-name' : 'settings-comfy-url');
  const field = $(focusId);
  if (field && typeof field.focus === 'function') field.focus();
}
async function generateSong() {
  if (!engineReady()) { await openEngineSetup(); return; }
  if (busy || draftNeedsWords()) return;
  busy = true; buttons();
  try {
    message('Saving your draft…');
    await persistDraft();
    if (dirty) {
      message('Newer edits arrived while the draft was saving, so nothing was rendered. Press Generate song again.', true);
      return;
    }
    const token = versionToken(current);
    let approved;
    try {
      approved = await api('/approve', {project_id: current.project_id, base_updated_at: token});
    } catch (err) {
      if (isStale(err)) { message(staleExplanation(), true); return; }
      throw err;
    }
    if (approved && approved.project) current = approved.project;
    const count = renderTakes(), engine_id = form.elements.engine.value;
    let queued;
    if (count > 1) {
      queued = await api('/batch', {project_id: current.project_id, count, engine_id});
      message(`Rendering ${count} takes, one after another. Follow their progress here, or open the Library.`);
    } else {
      const seed = crypto.getRandomValues(new Uint32Array(1))[0];
      queued = await api('/generate', {project_id: current.project_id, engine_id, seed});
      message('Rendering your song. Progress shows here, and the finished take plays on this page.');
    }
    noteFollowed(current.project_id, queued);
    renderActiveTake();
    await refresh();
  } catch (err) {
    if (isStale(err)) message(staleExplanation(), true);
    else message(err.message, true);
  } finally { busy = false; buttons(); }
}
form.addEventListener('submit', async e => {
  e.preventDefault();
  if (busy) return;
  busy = true; buttons();
  try {
    await persistDraft();
    message(dirty ? 'The earlier draft was saved. Save your newer edits before generating.' : 'Draft saved.');
    await refreshWorkspaces();
    await refresh();
  } catch (err) {
    message(isStale(err)
      ? 'This draft changed on the server (stale draft), so it was not overwritten. Open the latest version from the Library.'
      : err.message, true);
  } finally { busy = false; buttons(); }
});
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
          message('That is the chorus. Like it? “Generate song” renders the whole thing.');
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

// How many takes one render makes. One is the plain generate path; more asks the
// serial batch route for the same engine, and the existing queue display labels each take.
const takesField = $('render-takes');
const renderTakes = () => Math.max(1, Math.min(4, Number(takesField && takesField.value) || 1));
if (takesField) {
  try { const saved = Number(localStorage.getItem('studio.takes')); if (saved >= 1 && saved <= 4) takesField.value = String(saved); } catch (e) {}
  takesField.addEventListener('change', () => { try { localStorage.setItem('studio.takes', takesField.value); } catch (e) {} });
}
$('render').addEventListener('click', () => { generateSong(); });
$('new-song').addEventListener('click', async () => { if (dirty && !(await askConfirm('Discard unsaved changes?', {confirmLabel:'Discard', danger:true}))) return; followed=null; current=null; form.reset(); dirty=false; showCover(null); buttons(); engineHint(); try { localStorage.removeItem('studio.lastProject'); localStorage.removeItem('studio.draftBackup'); } catch (e) {}
  clearTimeout(autosaveTimer); draftStateLabel('New idea'); message('New draft.'); show('create'); });
for (const [id, event] of [['library-search','input'], ['library-sort','change']]) {
  const node = $(id); if (node) node.addEventListener(event, () => {
    if (id === 'library-sort') { try { localStorage.setItem('studio.librarySort', node.value); } catch (e) {} }
    library(); });
}
try { const saved = localStorage.getItem('studio.librarySort'); if (saved && $('library-sort')) $('library-sort').value = saved; } catch (e) {}
const navCreate = $('nav-create');
if (navCreate) navCreate.addEventListener('click',() => show('create'));
const navLibrary = $('nav-library');
if (navLibrary) navLibrary.addEventListener('click',() => {libraryFilter=null;library();show('library');});
const navWorkspaces = $('nav-workspaces');
if (navWorkspaces) navWorkspaces.addEventListener('click',()=>{drawWorkspaces();show('workspaces');});
const navEngines = $('nav-engines');
if (navEngines) navEngines.addEventListener('click',async()=>{show('engines');await loadEngines();wireEnginesPage();});
const navCompute = $('nav-compute');
if (navCompute && navCompute !== navEngines) {
  navCompute.addEventListener('click',async()=>{show('engines');await loadEngines();wireEnginesPage();});
}
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
    fillWorkflowTargetSelect();
    drawTargets();
    $('target-message').textContent = targets.length ? `${targets.length} saved target${targets.length === 1 ? '' : 's'}.` : 'No saved targets; the engine default endpoint is used.';
    $('target-message').classList.remove('error');
  } catch (e) {
    targets = [];
    fillTargetSelect();
    fillWorkflowTargetSelect();
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
  if (!(await askConfirm(`Delete target "${t.name}"? Drafts using it must choose another endpoint.`, {confirmLabel:'Delete', danger:true}))) return;
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

function workflowEngineId(workflow) { return 'comfy_workflow:' + workflow.id; }
function fillWorkflowTargetSelect() {
  const select = $('workflow-target');
  if (!select) return;
  const previous = select.value;
  select.replaceChildren();
  if (!targets.length) {
    const option = el('option', 'No saved targets — add one first');
    option.value = '';
    select.append(option);
    return;
  }
  targets.forEach(t => { const option = el('option', t.name); option.value = t.id; select.append(option); });
  if (previous && targets.some(t => t.id === previous)) select.value = previous;
}
// A workflow the Music engines wizard made belongs to a listed engine: show it as
// "<model> on <server>". Uploads under More options keep the "Custom workflow:" prefix.
function wizardWorkflowServer(workflow) {
  const id = workflow && workflow.id;
  for (const engine of enginesList || []) {
    if ((engine && Array.isArray(engine.workflows) ? engine.workflows : []).some(w => w && w.id === id)) {
      return engine.name || engine.url || '';
    }
  }
  return null;
}
function workflowMenuLabel(workflow) {
  const name = workflow && workflow.name ? String(workflow.name) : '';
  const server = wizardWorkflowServer(workflow);
  if (server !== null) return server ? `${name} on ${server}` : name;
  return 'Custom workflow: ' + name;
}
function ensureEngineOption(id) {
  if (!id) return;
  const select = $('engine-select');
  if (!select || [...select.options].some(o => o.value === id)) return;
  const workflow = workflows.find(w => workflowEngineId(w) === id);
  const option = el('option', workflow ? workflowMenuLabel(workflow) : 'Missing workflow ' + id);
  option.value = id;
  select.append(option);
}
function fillWorkflowEngineOptions() {
  const select = $('engine-select');
  if (!select) return;
  const previous = select.value;
  const add = (value, text) => { const option = el('option', text); option.value = value; select.append(option); };
  select.replaceChildren();
  add('ace_local', 'ACE-Step 1.5 (this computer)');
  add('minimax_m3_comfy', 'MiniMax Music 3');
  workflows.forEach(w => add(workflowEngineId(w), workflowMenuLabel(w)));
  if (previous && [...select.options].some(o => o.value === previous)) select.value = previous;
  else if (previous) { add(previous, 'Missing workflow ' + previous); select.value = previous; }
}
function drawWorkflows() {
  const list = $('workflow-list');
  if (!list) return;
  list.replaceChildren();
  if (!workflows.length) {
    list.append(el('p', 'No custom workflows yet. Upload a ComfyUI Export (API) JSON to render with any graph that server runs.'));
    return;
  }
  workflows.forEach(w => {
    const card = el('article', '', 'project target-card'), head = el('div', '', 'project-head');
    const info = el('div'), actions = el('div', '', 'actions target-actions');
    const endpoint = targetName(w.target_id) || w.target_id;
    info.append(el('b', w.name), el('p', `Endpoint: ${endpoint}`),
      el('p', 'Placeholders: ' + ((w.placeholders || []).map(p => '{{' + p + '}}').join(', ') || 'none')));
    const test = el('button', 'Test', 'button secondary'), remove = el('button', 'Delete', 'button secondary');
    test.type = remove.type = 'button';
    const result = el('p', '', 'hint target-result');
    test.onclick = () => probeWorkflow(w, test, result);
    remove.onclick = () => deleteWorkflow(w);
    actions.append(test, remove);
    head.append(info, actions);
    card.append(head, result);
    list.append(card);
  });
}
async function refreshWorkflows() {
  try {
    const data = await api('/studio/workflows');
    workflows = data.workflows || [];
    try {
      const listed = await api('/studio/engines');
      enginesList = listed.engines || [];
      renderEngineList();
    } catch (err) { /* keep the engine names already loaded */ }
    fillWorkflowTargetSelect();
    drawWorkflows();
    fillWorkflowEngineOptions();
    if ($('workflow-message')) {
      $('workflow-message').textContent = workflows.length
        ? `${workflows.length} custom workflow${workflows.length === 1 ? '' : 's'} saved.`
        : 'No custom workflows yet; the built-in engines still work.';
      $('workflow-message').classList.remove('error');
    }
  } catch (e) {
    workflows = [];
    drawWorkflows();
    fillWorkflowEngineOptions();
    if ($('workflow-message')) {
      $('workflow-message').textContent = 'Workflows unavailable: ' + e.message;
      $('workflow-message').classList.add('error');
    }
  }
}
async function probeWorkflow(w, button, result) {
  button.disabled = true;
  result.textContent = 'Testing…';
  result.classList.remove('error');
  try {
    const data = await api('/studio/workflows/' + encodeURIComponent(w.id) + '/test', undefined, 'POST');
    const status = data.status || {}, available = status.available === true;
    const placeholders = (data.placeholders || []).map(p => '{{' + p + '}}').join(', ') || 'none';
    result.textContent = `${available ? 'Available' : 'Unavailable'} · ${status.state || 'unknown'}`
      + `${status.detail ? ' · ' + status.detail : ''} · placeholders: ${placeholders}`;
    result.classList.toggle('error', !available);
  } catch (e) {
    result.textContent = e.message;
    result.classList.add('error');
  } finally {
    button.disabled = false;
  }
}
async function deleteWorkflow(w) {
  if (!(await askConfirm(`Delete workflow "${w.name}"?`, {confirmLabel:'Delete', danger:true}))) return;
  try {
    await api('/studio/workflows/' + encodeURIComponent(w.id), undefined, 'DELETE');
    await refreshWorkflows();
    $('workflow-message').textContent = `Deleted "${w.name}".`;
  } catch (e) {
    $('workflow-message').textContent = e.message;
    $('workflow-message').classList.add('error');
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
// Load saved model choice from localStorage
function loadModelPreference() {
  try { return JSON.parse(localStorage.getItem('studio.modelChoice')) || {}; } catch (e) { return {}; }
}
function saveModelPreference() {
  try { localStorage.setItem('studio.modelChoice', JSON.stringify({
    provider: $('idea-provider').value, model: $('idea-model').value
  })); } catch (e) {}
}
async function loadModels() {
  try {
    const d = await modelChoices();
    modelCatalog = Array.isArray(d.providers) ? d.providers : [];
    const pref = loadModelPreference();
    fillProviders();
    if (!modelCatalog.length) {
      $('idea-connection').textContent = 'Drafting goes through Hermes; write an idea and press Discuss idea or Fill my draft.';
      fillModels();
      return;
    }
    $('idea-connection').textContent = 'Your Hermes model is used unless you pick another provider and model.';
    // Try to restore saved preference, otherwise use first provider
    if (pref.provider && modelCatalog.some(p => p.slug === pref.provider)) {
      $('idea-provider').value = pref.provider;
      fillModels(pref.model);
    } else {
      fillModels();
    }
  } catch (e) {
    $('idea-connection').textContent = 'Hermes model connection unavailable. Check the Hermes installation and setup guide.';
  }
}
// One filter over both lists: 400+ models across several providers is not something a person
// scrolls. It matches the provider name, the model id, and 'free'.
function ideaQuery() { const input = $('idea-filter'); return (input?.value || '').trim().toLowerCase(); }
function modelMatches(p, id) {
  const q = ideaQuery();
  if (!q) return true;
  return (id || '').toLowerCase().includes(q) || (p?.slug || '').toLowerCase().includes(q) ||
    (p?.name || '').toLowerCase().includes(q) || (q === 'free' && /(^|[-_/])free([-_/]|$)/i.test(id || ''));
}
function fillProviders(preferred) {
  const select = $('idea-provider');
  if (!select) return;
  const had = select.value;
  select.replaceChildren();
  const defaultOption = el('option', 'Your Hermes model (recommended)');
  defaultOption.value = '';
  select.append(defaultOption);
  const q = ideaQuery();
  const rows = modelCatalog.filter(p => !q || (p.models || []).some(m => modelMatches(p, typeof m === 'string' ? m : (m.id || m.name))));
  rows.forEach(p => {
    const label = p.name || p.slug;
    const ready = p.configured === true ? ' (ready)' : '';
    const o = el('option', label + ready);
    o.value = p.slug;
    select.append(o);
  });
  if (typeof preferred === 'string' && rows.some(p => p.slug === preferred)) select.value = preferred;
  else if (rows.some(p => p.slug === had)) select.value = had;
  else if (rows.length === 1) select.value = rows[0].slug;
}
function fillModels(preferred) {
  fillProviders();
  const p = modelCatalog.find(x => x.slug === $('idea-provider').value);
  const select = $('idea-model');
  select.replaceChildren();
  const placeholder = el('option', 'Select a model');
  placeholder.value = '';
  select.append(placeholder);
  (p?.models || []).forEach(m => {
    const id = typeof m === 'string' ? m : (m.id || m.name);
    if (!id) return;
    if (!modelMatches(p, id)) return;
    const option = el('option', id);
    option.value = id;
    select.append(option);
  });
  if (typeof preferred === 'string' && [...select.options].some(o => o.value === preferred)) select.value = preferred;
  saveModelPreference();
}
$('idea-provider').onchange = () => { fillModels(); saveModelPreference(); };
$('idea-model').onchange = () => { saveModelPreference(); };
$('idea-filter').oninput = () => { fillProviders(); fillModels(); };
$('idea-filter').onkeydown = e => {
  if (e.key === 'Enter' && $('idea-model').options.length > 1 && !$('idea-model').value) {
    $('idea-model').selectedIndex = 1;
    fillModels($('idea-model').value);
  }
};
function addIdea(role, text){const p=el('p',text,role==='user'?'human':'assistant');$('idea-messages').append(p);$('idea-messages').scrollTop=$('idea-messages').scrollHeight;}
function addIdeaError(message, commands) {
  const div = el('div', '', 'assistant error-message');
  const msg = el('p', message);
  div.append(msg);
  if (Array.isArray(commands) && commands.length > 0) {
    const cmds = el('pre', commands.join('\n'), 'commands');
    const btn = el('button', 'Copy', 'button secondary copy-cmd');
    btn.type = 'button';
    btn.onclick = async () => {
      const text = commands.join('\n');
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
          btn.textContent = 'Copied!';
          setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
        } else {
          cmds.select?.();
          document.execCommand('copy');
        }
      } catch (e) {
        cmds.select?.();
        try { document.execCommand('copy'); } catch (e2) {}
      }
    };
    div.append(cmds, btn);
  }
  $('idea-messages').append(div);
  $('idea-messages').scrollTop = $('idea-messages').scrollHeight;
}
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
      else {
        const err = new Error(data.error || 'drafting failed');
        if (Array.isArray(data.commands)) err.commands = data.commands;
        reject(err);
      }
    }
    window.addEventListener('message', onReply);
    const message = { type: 'hermes-music-studio:draft', id,
      prompt: request.prompt, tone: request.tone, style: request.style };
    if (request.provider) message.provider = request.provider;
    if (request.model) message.model = request.model;
    parentWindow.postMessage(message, '*');
  });
}

// The model list has the same split as drafting: Hermes' providers are known in the plugin process,
// which the pane reaches with ctx.rest. Framed, ask the pane; in a plain tab, ask the host (which can
// only offer "Your Hermes model").
function paneModels() {
  return new Promise((resolve, reject) => {
    const parentWindow = window.parent;
    if (!parentWindow || parentWindow === window) { reject(new Error('not framed')); return; }
    const id = 'models-' + Math.random().toString(36).slice(2);
    const timer = setTimeout(() => { window.removeEventListener('message', onReply); reject(new Error('the pane did not answer')); }, 8000);
    function onReply(event) {
      const data = event && event.data;
      if (!data || data.type !== 'hermes-music-studio:models-result' || data.id !== id) return;
      clearTimeout(timer);
      window.removeEventListener('message', onReply);
      if (data.ok && data.choices) resolve(data.choices); else reject(new Error(data.error || 'no model list'));
    }
    window.addEventListener('message', onReply);
    parentWindow.postMessage({ type: 'hermes-music-studio:models', id }, '*');
  });
}
async function modelChoices() {
  try { return await paneModels(); } catch (e) { return api('/studio/models'); }
}

function applySuggestedDraft(draft) {
  const source = draft || {};
  for(const key of ['title','caption','lyrics','bpm','keyscale','duration_seconds']) if(source[key]!==undefined && form.elements[key])form.elements[key].value=source[key];
  if(typeof source.instrumental==='boolean')form.elements.instrumental.checked=source.instrumental;
  dirty=true;revision++;buttons();
}

async function confirmDraftReplace() {
  if (!(form.elements.caption.value || form.elements.lyrics.value)) return true;
  return askConfirm('Replace the current draft fields with the suggested draft?', {confirmLabel:'Replace'});
}
async function idea(action) {
  const input=$('idea-input').value.trim(); if(input){conversation.push({role:'user',content:input});addIdea('user',input);$('idea-input').value='';}
  if(!conversation.some(m=>m.role==='user')){$('idea-connection').textContent='Tell me a little about the song first.';return;}
  const provider=$('idea-provider-manual').value.trim() || $('idea-provider').value, model=$('idea-model-manual').value.trim() || $('idea-model').value;
  $('idea-send').disabled=true;$('idea-apply').disabled=true;$('idea-connection').textContent='Thinking…';
  const tone=$('idea-tone').value.toLowerCase(), style=Number($('idea-style').value);
  let replyNote = '';
  // Every draft goes through the pane: the host has no model, and a chosen provider/model is an
  // override Hermes grants (or refuses, with the commands to allow it) in the plugin process.
  try {const d = await paneDraft({action, tone, style, prompt: conversation.map(m => m.content).join('\n'),
      provider: provider && model ? provider : '', model: provider && model ? model : ''});
    if(action==='chat' && typeof d.content === 'string'){
      conversation.push({role:'assistant',content:d.content.slice(0,4000)});addIdea('assistant',d.content);
    } else if(action==='chat'){
      // The pane's /studio/draft route returns only a draft, with no chat text. Fill the form
      // from it and say so instead of throwing on the missing `content`. A draft that is already
      // written is not replaced until the person confirms, same as Fill my draft.
      if (!(await confirmDraftReplace())) return;
      applySuggestedDraft(d.draft);
      replyNote = 'The Hermes agent returned a draft, not a chat reply; it is in your draft fields. Review and save it.';
      addIdea('assistant', replyNote); message(replyNote); show('create');
    } else {
      if (!(await confirmDraftReplace())) return;
      applySuggestedDraft(d.draft);
      message('Suggested draft added. Review and save it.');show('create');
    }
    $('idea-connection').textContent = replyNote || (provider && model
      ? 'Using ' + provider + ' / ' + model + '.'
      : 'Drafted by the Hermes agent (its own model).');
  } catch(e) {
    if (e.commands && Array.isArray(e.commands) && e.commands.length > 0) {
      addIdeaError(e.message + '. Then restart Hermes.', e.commands);
      $('idea-connection').textContent = 'Check the commands above and try again after restarting Hermes.';
    } else {
      $('idea-connection').textContent = e.message;
      addIdea('assistant', 'Error: ' + e.message);
    }
  }
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
    // The route wraps REVamp's own reply, so the rows are data.playlists.playlists.
    const raw = data.playlists;
    const rows = Array.isArray(raw) ? raw : (raw && Array.isArray(raw.playlists) ? raw.playlists : []);
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
$('manage-targets').onclick=async()=>{show('compute');await refreshTargets();await refreshWorkflows();await loadHardware(true);};
$('target-form').addEventListener('submit', async e => {
  e.preventDefault();
  const name = $('target-name').value.trim(), url = $('target-url').value.trim();
  if (!name || !url) { $('target-message').textContent = 'Name and URL are required.'; return; }
  $('target-message').textContent = 'Saving target…';
  try {
    await api('/studio/targets', {name, url});
    $('target-name').value = ''; $('target-url').value = '';
    await refreshTargets();
    fillWorkflowTargetSelect();
    $('target-message').textContent = `Saved "${name}". Start ComfyUI on that host before rendering.`;
  } catch (err) {
    $('target-message').textContent = err.message;
    $('target-message').classList.add('error');
  }
});
$('workflow-form').addEventListener('submit', async e => {
  e.preventDefault();
  const name = $('workflow-name').value.trim(), target = $('workflow-target').value;
  const file = ($('workflow-file').files || [])[0];
  if (!name || !target || !file) { $('workflow-message').textContent = 'Name, endpoint and a workflow JSON file are required.'; return; }
  $('workflow-message').classList.remove('error');
  $('workflow-message').textContent = 'Reading workflow…';
  try {
    const text = await file.text();
    let workflow;
    try { workflow = JSON.parse(text); } catch (err) { throw new Error('that file is not valid JSON'); }
    $('workflow-message').textContent = 'Validating workflow…';
    const data = await api('/studio/workflows', {name, target_id: target, workflow});
    $('workflow-name').value = ''; $('workflow-file').value = '';
    await refreshWorkflows();
    const found = ((data.workflow || {}).placeholders || []).map(p => '{{' + p + '}}').join(', ');
    $('workflow-message').textContent = `Saved "${name}". Fillable: ${found || 'none'}.`;
  } catch (err) {
    $('workflow-message').textContent = err.message;
    $('workflow-message').classList.add('error');
  }
});
$('hardware-refresh').onclick=refreshHardware;
refreshTargets(); refreshWorkflows(); refresh(); refreshWorkspaces(); loadModels(); loadPresets();


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
  // Every field in this form is saved: scan hosts and the export folder sat in the form but were
  // never sent, so they could not be saved from here at all.
  const body = { comfy_url: $('settings-comfy-url').value.trim(),
    ...($('settings-scan-hosts') ? { scan_hosts: $('settings-scan-hosts').value.trim() } : {}),
    ...($('settings-export-dir') ? { export_dir: $('settings-export-dir').value.trim() } : {}),
    minimax: { steps: Number($('settings-steps').value), cfg: Number($('settings-cfg').value),
      cfg_scale: Number($('settings-cfg-scale').value), top_k: Number($('settings-top-k').value),
      sampler: $('settings-sampler').value, scheduler: $('settings-scheduler').value } };
  try {
    const data = await api('/settings', body);
    renderLibraryPath(data.library);
    $('settings-message').textContent = 'Saved. The next render uses these values.';
  } catch (err) { $('settings-message').textContent = err.message; }
}
// The doctor's per-engine next step belongs under that engine's own row, not in
// a generic error banner: the health detail names what failed, the fix says what
// to set. One list, no extra buttons.
function ensureDoctorBox() {
  let box = $('engine-doctor');
  if (box) return box;
  const panel = $('setup-panel');
  if (!panel) return null;
  box = el('div', '', 'doctor-list');
  box.id = 'engine-doctor';
  const message = $('setup-message');
  panel.insertBefore(box, message || null);
  return box;
}
function drawDoctor(rows) {
  const box = ensureDoctorBox();
  if (!box) return;
  box.replaceChildren(el('p', 'Engine doctor', 'kicker'));
  if (!rows.length) { box.append(el('p', 'No engines registered.', 'hint')); return; }
  rows.forEach(row => {
    const item = el('div', '', 'doctor-row');
    item.dataset.engine = row.id;
    item.append(el('p', `${row.ready ? '✓' : '✗'} ${row.label || row.id} · ${row.state || 'unknown'}${row.detail ? ': ' + row.detail : ''}`,
      row.ready ? 'hint' : 'hint error'));
    if (row.fix) item.append(el('p', row.fix, 'hint doctor-fix'));
    box.append(item);
  });
}
async function loadDoctor() {
  const box = ensureDoctorBox();
  if (!box) return;
  try { drawDoctor((await api('/doctor')).engines || []); }
  catch (err) { box.replaceChildren(el('p', 'Engine checks unavailable: ' + err.message, 'hint error')); }
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
  if (mode === 'move' && !(await askConfirm(`Move the library to ${path}? The old copies are deleted after the switch.`, {confirmLabel:'Move', danger:true}))) return;
  try {
    const data = await api('/library/relocate', { path, mode });
    renderLibraryPath(data.library);
    $('library-message').textContent = `${mode === 'move' ? 'Moved' : 'Copied'} to ${data.library.active} (${data.copied} item(s)).`;
    await refresh(); library();
  } catch (err) { $('library-message').textContent = err.message; }
}
async function resetLibrary() {
  try {
    const data = await api('/library/reset', {});
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
const settingsNavBtn = $('nav-compute');
if (settingsNavBtn) settingsNavBtn.addEventListener('click', () => { loadSettings().catch(err => { $('settings-message').textContent = err.message; }); loadDoctor(); });
loadSettings().catch(err => { const node = $('settings-message'); if (node) node.textContent = err.message; });
loadDoctor();

// Add more song after a take that already succeeded. The backend owns the overlap and out-paint;
// the pane only asks how many seconds and, optionally, what should happen next. The limits mirror
// EXTEND_MIN_SECONDS/EXTEND_MAX_SECONDS in music_studio/store.py.
function extendRow(project, take, row) {
  if (row.querySelector('.extend-form')) return;      // one form per take, not a stack of them
  const form = el('div', '', 'snippet-form extend-form');
  const seconds = el('input'); seconds.type = 'number'; seconds.min = '10'; seconds.max = '60';
  seconds.step = '1'; seconds.value = '30'; seconds.className = 'extend-seconds';
  seconds.placeholder = 'seconds'; seconds.title = 'How many seconds to add (10–60)';
  const words = el('input'); words.type = 'text'; words.className = 'extend-words';
  words.placeholder = 'what happens next (optional): a long fade, an outro';
  const go = el('button', 'Extend the take', 'button secondary'); go.type = 'button';

  go.addEventListener('click', async () => {
    const add = Number(seconds.value);
    if (!Number.isInteger(add) || add < 10 || add > 60) {
      libMessage('Add a whole number of seconds from 10 to 60.', true); return;
    }
    const caption = (words.value || '').trim();
    go.disabled = true;
    try {
      await api('/extend', { project_id: project.project_id, take_id: take.take_id,
        extend_seconds: add, ...(caption ? { caption } : {}) });
      libMessage(`Extending by ${add}s. The new take appears above as it renders.`);
      await refresh(); library();
    } catch (err) { libMessage(err.message, true); } finally { go.disabled = false; }
  });

  form.append(seconds, words, go);
  row.append(form);
}

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
    const data = await api('/about/diagnostics', {});
    const box = $('diagnostics-out');
    box.hidden = false;
    box.firstElementChild.textContent = data.text;
    const ok = await copyText(data.text);
    aboutMessage(ok ? 'Diagnostics copied — paste them into the issue.' : 'Copy failed; the report is shown above.');
  } catch (err) { aboutMessage(err.message, true); }
});
loadAbout().catch(err => aboutMessage(err.message, true));

async function closeStudio() {
  if (!(await askConfirm('Close Music Studio? The window stops and the studio host shuts down. Your library and drafts stay on disk.', {confirmLabel:'Close studio', danger:true}))) return;
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
        `${entry.name} · ${entry.url}${entry.model ? ' · ' + entry.model : ''}${entry.has_key ? ' · key saved in Hermes' : ''}`));
    });
    if (!(data.endpoints || []).length) box.append(el('p', 'hint', 'No hosted endpoint saved yet.'));
    if (data.message) box.append(el('p', 'hint', data.message));
  };

  if ($('setup-cloud-test')) $('setup-cloud-test').addEventListener('click', async () => {
    const name = ($('setup-cloud-name').value || 'default').trim() || 'default';
    try {
      const saved = await api('/studio/setup/cloud', {
        name, url: ($('setup-cloud-url').value || '').trim(),
        key: $('setup-cloud-key').value || '',
        model: ($('setup-cloud-model') || {}).value || '',
      });
      if (saved.message) await setupMessage(saved.message, true);
      const result = await api('/studio/setup/cloud/test', { name });
      await setupMessage(result.message || result.detail, !result.ok);
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
    if (!(await askConfirm('Install the newest published build now? The studio restarts when it finishes.', {confirmLabel:'Install'}))) return;
    try {
      await api('/update/apply', {});
      await setupMessage('Update started.');
      watchUpdateJob();
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });
  if ($('version-undo')) $('version-undo').addEventListener('click', async () => {
    if (!(await askConfirm('Go back to the build that was installed before the last update?', {confirmLabel:'Undo update'}))) return;
    try {
      await api('/update/undo', {});
      await setupMessage('Rolling back.');
      watchUpdateJob();
    } catch (err) {
      await setupMessage(err.message, true);
    }
  });
}
// ---------------------------------------------------------------- engines page
let enginesList = [], testingServer = false, downloadJobId = null, enginesPageWired = false, installPollTimer = null, installPollGen = 0, engineTestGen = 0, lastEngineTest = null;
function renderEngineList() {
  const container = $('engine-list');
  if (!container) return;
  container.replaceChildren();
  if (!enginesList.length) {
    const empty = el('p', 'No music engines set up yet. Add one to get started.', 'hint');
    container.append(empty);
    return;
  }
  for (const eng of enginesList) {
    const row = el('div', '', 'engine-row');
    const info = el('div', '', 'engine-info');
    const name = el('div', eng.name || eng.url, 'engine-name');
    const addr = el('div', eng.url || '(unknown address)', 'engine-address');
    info.append(name, addr);

    const pill = el('span', '', 'engine-pill');
    if (eng.status === 'ready') { pill.textContent = 'Ready'; pill.classList.add('ready'); }
    else if (eng.status === 'missing_files') { pill.textContent = 'Missing files'; pill.classList.add('missing-files'); }
    else if (eng.status === 'cant_reach') { pill.textContent = "Can't reach"; pill.classList.add('cant-reach'); }
    else { pill.textContent = 'Not tested'; pill.classList.add('not-tested'); }

    const testBtn = el('button', 'Test', 'button secondary');
    testBtn.type = 'button';
    testBtn.addEventListener('click', async () => {
      await testEngine(eng);
    });

    const removeBtn = el('button', 'Remove', 'button secondary');
    removeBtn.type = 'button';
    removeBtn.addEventListener('click', async () => {
      if (await askConfirm(`Remove ${eng.name || eng.url}?`, {confirmLabel: 'Remove', danger: true})) {
        await removeEngine(eng.id);
      }
    });

    row.append(info, pill, testBtn, removeBtn);
    container.append(row);
  }
}
async function loadEngines() {
  try {
    const data = await api('/studio/engines');
    enginesList = data.engines || [];
    renderEngineList();
    fillWorkflowEngineOptions();
  } catch (err) {
    // Silent fail; show empty list
    enginesList = [];
    renderEngineList();
  }
}
async function testEngine(eng) {
  const gen = ++engineTestGen;
  const kind = (eng && eng.kind) || 'comfyui';
  const url = ((eng && eng.url) || '').trim();
  testingServer = true;
  const result = $('test-result');
  if (result) result.hidden = true;

  let data;
  try {
    data = await api('/studio/engines/test', { kind: kind, url: url });
  } catch (err) {
    data = {
      ok: false,
      message: err && err.message ? err.message : 'That address could not be tested.',
      problem: 'bad_address',
    };
  }
  testingServer = false;
  if (gen !== engineTestGen) return;
  lastEngineTest = { kind: kind, url: url, data: data };
  showTestResult(data);
  syncServerKind();
}
function showTestResult(data) {
  const result = $('test-result');
  const trouble = $('troubleshooting');
  if (!result || !trouble) return;

  result.hidden = false;
  trouble.hidden = true;
  trouble.replaceChildren();

  result.replaceChildren();
  const message = data && data.message != null ? String(data.message) : '';
  result.append(el('p', message, 'test-message'));

  if (data.models && Array.isArray(data.models)) {
    const list = el('ul', '', 'model-checklist');
    for (const model of data.models) {
      const item = el('li', '', 'model-item');
      const status = model && model.status != null ? String(model.status) : '';
      const name = model && (model.label || model.id) != null ? String(model.label || model.id) : '';
      const icon = el('span', status === 'ready' ? '✓' : '–', 'status-icon');
      const label = el('span', name + ' (' + status + ')');
      item.append(icon, label);
      list.append(item);
    }
    result.append(list);
  }

  if (data.problem && TROUBLESHOOTING[data.problem]) {
    trouble.hidden = false;
    const card = TROUBLESHOOTING[data.problem];
    trouble.replaceChildren(
      el('h4', card.title),
      el('p', card.text)
    );
  }
}
const TROUBLESHOOTING = {
  bad_address: { title: 'Wrong address', text: 'Nothing answered. Include http:// and the port, like http://192.168.1.50:8188.' },
  no_answer: { title: 'Firewall blocking', text: "The other computer's firewall is blocking the port. Allow 8188, or 8000, for your local network." },
  not_comfy: { title: 'Not ComfyUI', text: 'That address answered but is not ComfyUI or ACE-Step server. Check the address.' },
  not_ace: { title: 'Not an ACE server', text: 'That address is not an ACE-Step server.' },
  missing_files: { title: 'Missing model files', text: 'ComfyUI knows the model, and these files are not on it. Put them in that computer\'s models folder, then test again.' },
  no_gpu: { title: 'No GPU available', text: 'The graphics card that matters is on the machine running the model. This PC does not need one when the server is elsewhere.' }
};
async function removeEngine(engineId) {
  try {
    await api(`/studio/engines/${encodeURIComponent(engineId)}`, undefined, 'DELETE');
    await loadEngines();
  } catch (err) {
    // Silent fail
  }
}
function installMessage(text, bad = false) {
  const node = $('install-message');
  if (!node) return;
  node.textContent = text || '';
  node.classList.toggle('error', !!bad);
}
function suggestedModelsDir(data) {
  if (typeof data === 'string') return data.trim();
  if (data && typeof data.models_dir === 'string') return data.models_dir.trim();
  return '';
}
// Decimal gigabytes, so the sizes match the 14.7 GB on the download button.
function bytesToGb(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '0.0 GB';
  return (n / 1e9).toFixed(1) + ' GB';
}
function spaceLine(label, value) {
  const row = el('div', '', 'space-item');
  row.append(el('span', label, 'label'), el('span', value, 'value'));
  return row;
}
function showResume(on) {
  const btn = $('resume-download-btn');
  if (btn) {
    btn.hidden = !on;
    const row = btn.parentElement;
    if (row && row.classList.contains('control-row')) row.hidden = !on;
  }
  const help = $('resume-help-btn');
  if (help) help.hidden = !on;
}
function showCancelDownload(on) {
  const btn = $('cancel-download-btn');
  if (!btn) return;
  btn.hidden = !on;
  const row = btn.parentElement;
  if (row && row.classList.contains('control-row')) row.hidden = !on;
}
function showGpuLine(text) {
  const node = $('install-gpu');
  if (!node) return;
  node.textContent = text || '';
  node.hidden = !text;
}
function gpuLineText(hw) {
  if (!hw || typeof hw !== 'object') return '';
  const devices = Array.isArray(hw.devices) ? hw.devices : [];
  if (!devices.length) {
    if (hw.error) return String(hw.error);
    return 'No graphics card reported on this computer.';
  }
  return devices.map(device => {
    const name = device.name || 'Graphics card';
    const index = device.index == null || device.index === '' ? '' : String(device.index);
    let line = index === '' ? name : ('GPU ' + index + ' · ' + name);
    if (device.memory_total_mb != null) {
      const used = device.memory_used_mb == null ? '—' : String(device.memory_used_mb);
      line += ' · ' + used + ' / ' + device.memory_total_mb + ' MiB';
    }
    return line;
  }).join('; ');
}
async function loadInstallGpu() {
  try {
    return await api('/studio/hardware');
  } catch (err) {
    const missing = /not found/i.test((err && err.message) || '');
    if (missing) return null;
    return { error: err && err.message ? err.message : 'Could not read the graphics card.', devices: [] };
  }
}
function renderInstallPlan(plan) {
  const box = $('space-result');
  const downloadBtn = $('download-ace-btn');
  const data = plan || {};
  if (box) {
    box.hidden = false;
    box.replaceChildren();
    const files = Array.isArray(data.files) ? data.files : [];
    let downloadBytes = 0;
    for (const file of files) {
      const name = file && file.name ? file.name : 'file';
      const present = !!(file && file.present);
      if (!present) downloadBytes += Number(file && file.size) || 0;
      const size = bytesToGb(file && file.size);
      box.append(spaceLine(name, present ? (size + ' · already there') : size));
    }
    box.append(spaceLine('Total to download', bytesToGb(downloadBytes)));
    box.append(spaceLine('Free space', bytesToGb(data.free_bytes)));
    if (data.message) {
      const msg = el('p', data.message, 'message space-message' + (data.ok === false ? ' error' : ''));
      box.append(msg);
    }
  }
  if (downloadBtn) {
    downloadBtn.hidden = false;
    const row = downloadBtn.parentElement;
    if (row && row.classList.contains('control-row')) row.hidden = false;
    downloadBtn.disabled = !(data.ok === true && data.enough_space === true);
  }
}
async function openInstallWizard() {
  const wizard = $('install-wizard');
  if (wizard) wizard.hidden = false;
  const step2 = $('install-step-2');
  const step3 = $('install-step-3');
  if (step2) step2.hidden = false;
  if (step3) step3.hidden = false;
  installMessage('');
  const field = $('models-dir');
  try {
    const data = await api('/studio/install/suggest');
    const dir = suggestedModelsDir(data);
    if (field && dir) field.value = dir;
  } catch (err) {
    installMessage(err.message, true);
  }
}
async function checkInstallSpace() {
  const field = $('models-dir');
  const modelsDir = field ? field.value.trim() : '';
  installMessage('');
  const gpuPromise = loadInstallGpu();
  try {
    const plan = await api('/studio/install/plan', { models_dir: modelsDir });
    renderInstallPlan(plan);
  } catch (err) {
    installMessage(err.message, true);
    const downloadBtn = $('download-ace-btn');
    if (downloadBtn) downloadBtn.disabled = true;
  }
  const hw = await gpuPromise;
  showGpuLine(hw ? gpuLineText(hw) : '');
}
function stopInstallPoll() {
  installPollGen += 1;
  if (installPollTimer) {
    clearTimeout(installPollTimer);
    installPollTimer = null;
  }
}
function applyInstallStatus(status) {
  const state = status && status.state;
  const box = $('download-progress');
  if (box) box.hidden = false;
  const doneBytes = Number(status && status.done_bytes) || 0;
  const totalBytes = Number(status && status.total_bytes) || 0;
  const pct = totalBytes > 0 ? Math.max(0, Math.min(100, Math.round((doneBytes / totalBytes) * 100))) : 0;
  const bar = $('install-progress-bar');
  if (bar) {
    bar.style.width = pct + '%';
    const track = bar.parentElement;
    if (track) track.setAttribute('aria-valuenow', String(pct));
  }
  const file = (status && status.file) || '';
  const filesDone = status && status.files_done != null ? status.files_done : 0;
  const filesTotal = status && status.files_total != null ? status.files_total : 0;
  const msg = (status && status.message) || '';
  const parts = [];
  if (file) parts.push(file);
  parts.push(String(filesDone) + ' of ' + String(filesTotal));
  if (msg) parts.push(msg);
  const progressText = $('progress-text');
  if (progressText) progressText.textContent = parts.join(' · ');
  const doneBox = $('install-done');
  if (state === 'done') {
    showResume(false);
    showCancelDownload(false);
    if (doneBox) doneBox.hidden = false;
    installMessage('');
    return true;
  }
  if (state === 'failed' || state === 'cancelled') {
    showResume(true);
    showCancelDownload(false);
    if (doneBox) doneBox.hidden = true;
    if (state === 'failed') installMessage((status && (status.error || status.message)) || 'The download failed.', true);
    else installMessage((status && status.message) || 'Download cancelled. Press Resume download to continue.');
    return true;
  }
  showResume(false);
  showCancelDownload(true);
  if (doneBox) doneBox.hidden = true;
  installMessage('');
  return false;
}
async function pollInstallJob(jobId, gen) {
  if (gen !== installPollGen) return;
  let status;
  try {
    status = await api('/studio/install/jobs/' + encodeURIComponent(jobId));
  } catch (err) {
    if (gen !== installPollGen) return;
    installMessage(err.message, true);
    showResume(true);
    return;
  }
  if (gen !== installPollGen) return;
  const terminal = applyInstallStatus(status);
  if (gen !== installPollGen || terminal) return;
  installPollTimer = setTimeout(() => { pollInstallJob(jobId, gen); }, 1000);
}
async function startInstallDownload() {
  const field = $('models-dir');
  const modelsDir = field ? field.value.trim() : '';
  installMessage('');
  let data;
  try {
    data = await api('/studio/install/jobs', { models_dir: modelsDir });
  } catch (err) {
    installMessage(err.message, true);
    return;
  }
  const jobId = data && data.job_id;
  if (!jobId) {
    installMessage('The download did not start.', true);
    return;
  }
  downloadJobId = jobId;
  const progress = $('download-progress');
  if (progress) progress.hidden = false;
  const downloadBtn = $('download-ace-btn');
  if (downloadBtn) {
    downloadBtn.hidden = true;
    const row = downloadBtn.parentElement;
    if (row && row.classList.contains('control-row')) row.hidden = true;
  }
  showResume(false);
  showCancelDownload(true);
  const doneBox = $('install-done');
  if (doneBox) doneBox.hidden = true;
  stopInstallPoll();
  const gen = installPollGen;
  await pollInstallJob(jobId, gen);
}
async function cancelInstallDownload() {
  if (!downloadJobId) return;
  try {
    await api('/studio/install/jobs/' + encodeURIComponent(downloadJobId) + '/cancel', undefined, 'POST');
  } catch (err) {
    installMessage(err.message, true);
    return;
  }
  stopInstallPoll();
  const gen = installPollGen;
  await pollInstallJob(downloadJobId, gen);
}
async function lookLocal() {
  installMessage('');
  try {
    const data = await api('/studio/install/look-local');
    showLookLocalResult(data);
  } catch (err) {
    installMessage(err.message, true);
  }
}
function testedKindUrl() {
  const form = $('server-form');
  const picked = form && form.elements['engine-kind'];
  const kind = picked && picked.value ? picked.value : 'comfyui';
  const urlField = $('server-url');
  return { kind: kind, url: urlField ? urlField.value.trim() : '' };
}
function readyModelIds(data) {
  const models = data && Array.isArray(data.models) ? data.models : [];
  const ids = [];
  for (const model of models) {
    if (!model || model.status !== 'ready' || typeof model.id !== 'string') continue;
    const id = model.id.trim();
    if (!id || ids.indexOf(id) !== -1) continue;
    ids.push(id);
  }
  return ids;
}
// ComfyUI is usable only when this test named a ready model. ACE-Step's check
// has no model list; ok means that server itself is ready.
function serverAddReady() {
  const current = testedKindUrl();
  if (current.kind === 'hosted' || !lastEngineTest) return false;
  if (lastEngineTest.kind !== current.kind || lastEngineTest.url !== current.url) return false;
  const data = lastEngineTest.data;
  if (!data || data.ok !== true) return false;
  if (current.kind === 'ace_server') {
    const models = Array.isArray(data.models) ? data.models : [];
    if (!models.length) return true;
    return readyModelIds(data).length > 0;
  }
  if (current.kind === 'comfyui') return readyModelIds(data).length > 0;
  return false;
}
function syncServerKind() {
  const hosted = testedKindUrl().kind === 'hosted';
  const testBtn = $('test-server-btn');
  if (testBtn) {
    testBtn.hidden = hosted;
    const row = testBtn.closest('.wizard-actions');
    if (row) row.hidden = hosted;
  }
  const keyLabel = $('api-key-label');
  if (keyLabel) keyLabel.hidden = !hosted;
  const modelLabel = $('server-model-label');
  if (modelLabel) modelLabel.hidden = !hosted;
  const addBtn = $('add-engine-btn');
  if (addBtn) addBtn.disabled = hosted ? false : !serverAddReady();
}
function invalidateEngineTest() {
  engineTestGen += 1;
  lastEngineTest = null;
  const result = $('test-result');
  if (result) {
    result.hidden = true;
    result.replaceChildren();
  }
  const trouble = $('troubleshooting');
  if (trouble) {
    trouble.hidden = true;
    trouble.replaceChildren();
  }
  syncServerKind();
}
function clearHostedKey() {
  const keyEl = $('server-key');
  if (keyEl) keyEl.value = '';
}
function serverMessage(text, bad = false) {
  const node = $('server-message');
  if (!node) return;
  node.textContent = text == null ? '' : String(text);
  node.classList.toggle('error', !!bad && node.textContent !== '');
}
function moveMessage(text, bad = false) {
  const node = $('move-engines-message');
  if (!node) return;
  node.textContent = text == null ? '' : String(text);
  node.classList.toggle('error', !!bad && node.textContent !== '');
}
function engineNameList(value) {
  if (!Array.isArray(value)) return '';
  const names = [];
  for (const item of value) {
    if (item == null || item === '') continue;
    names.push(String(item));
  }
  return names.join(', ');
}
function dismissServerWizard() {
  const form = $('server-form');
  if (form) form.reset();
  const comfy = document.querySelector('input[name="engine-kind"][value="comfyui"]');
  if (comfy) comfy.checked = true;
  clearHostedKey();
  const modelEl = $('server-model');
  if (modelEl) modelEl.value = '';
  invalidateEngineTest();
  serverMessage('');
  const wizard = $('server-wizard');
  if (wizard) wizard.hidden = true;
}
async function finishEngineAdd() {
  dismissServerWizard();
  await loadEngines();
  await refreshWorkflows();
}
async function addServerEngine() {
  const current = testedKindUrl();
  const name = ($('server-name') && $('server-name').value || '').trim();
  serverMessage('');
  if (current.kind === 'hosted') {
    const keyEl = $('server-key');
    const key = keyEl ? keyEl.value : '';
    const modelEl = $('server-model');
    const model = modelEl ? modelEl.value.trim() : '';
    const saved = await api('/studio/setup/cloud', {
      name: name, url: current.url, key: key, model: model,
    });
    if (!saved || saved.ok !== true) {
      serverMessage((saved && (saved.message || saved.detail || saved.error)) || 'Could not save that hosted service.', true);
      return;
    }
    clearHostedKey();
    await finishEngineAdd();
    return;
  }
  if (!serverAddReady()) {
    serverMessage('Test this address first.', true);
    return;
  }
  if (current.kind === 'ace_server') {
    const saved = await api('/studio/setup/ace', { url: current.url });
    if (!saved || saved.ok !== true) {
      serverMessage((saved && (saved.detail || saved.message || saved.error)) || 'That address is not an ACE-Step server.', true);
      return;
    }
    await finishEngineAdd();
    return;
  }
  const models = readyModelIds(lastEngineTest && lastEngineTest.data);
  const saved = await api('/studio/engines', { kind: 'comfyui', name: name, url: current.url, models: models });
  if (saved && saved.ok === false) {
    serverMessage((saved.error || saved.detail || saved.message) || 'Could not add that engine.', true);
    return;
  }
  await finishEngineAdd();
}
function useLocalComfy(url) {
  const wizard = $('server-wizard');
  if (wizard) wizard.hidden = false;
  const kind = document.querySelector('input[name="engine-kind"][value="comfyui"]');
  if (kind) kind.checked = true;
  const field = $('server-url');
  if (field) field.value = url || '';
  clearHostedKey();
  invalidateEngineTest();
  testEngine({ kind: 'comfyui', url: url || '' });
}
function wireEnginesPage() {
  if (enginesPageWired) return;
  enginesPageWired = true;

  const addServerBtn = $('add-server-btn');
  const addLocalBtn = $('add-local-btn');
  const serverWizard = $('server-wizard');
  const installWizard = $('install-wizard');

  if (addServerBtn) addServerBtn.addEventListener('click', () => {
    if (serverWizard) serverWizard.hidden = false;
  });

  if (addLocalBtn) addLocalBtn.addEventListener('click', () => { openInstallWizard(); });

  // Close buttons
  const closeButtons = document.querySelectorAll('.close-wizard');
  if (closeButtons && closeButtons.length > 0) {
    closeButtons.forEach(btn => {
      btn.addEventListener('click', (e) => {
        const wizard = e.target.closest('.engine-wizard');
        if (!wizard) return;
        if (wizard.id === 'server-wizard') dismissServerWizard();
        else wizard.hidden = true;
      });
    });
  }

  // Info toggle
  const infoToggle = document.querySelector('.info-toggle');
  if (infoToggle) {
    const infoText = infoToggle.parentElement?.querySelector('.info-text');
    if (infoText) {
      infoToggle.addEventListener('click', () => {
        infoText.hidden = !infoText.hidden;
        infoToggle.setAttribute('aria-expanded', String(!infoText.hidden));
      });
    }
  }

  // Server form
  const serverForm = $('server-form');
  if (serverForm) {
    const testBtn = $('test-server-btn');
    const addBtn = $('add-engine-btn');
    const cancelBtn = $('cancel-server-btn');
    const kindInputs = serverForm.querySelectorAll('input[name="engine-kind"]');
    const urlField = $('server-url');

    if (kindInputs && kindInputs.length > 0) {
      kindInputs.forEach(input => {
        input.addEventListener('change', () => {
          if (testedKindUrl().kind !== 'hosted') clearHostedKey();
          invalidateEngineTest();
          serverMessage('');
        });
      });
    }
    if (urlField) urlField.addEventListener('input', () => {
      invalidateEngineTest();
      serverMessage('');
    });

    if (testBtn) testBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      const current = testedKindUrl();
      if (current.kind === 'hosted') return;
      serverMessage('');
      await testEngine({ kind: current.kind, url: current.url });
    });

    if (addBtn) addBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      try {
        await addServerEngine();
      } catch (err) {
        serverMessage(err && err.message ? err.message : 'Could not add that engine.', true);
      }
    });

    if (cancelBtn) cancelBtn.addEventListener('click', () => {
      dismissServerWizard();
    });
    syncServerKind();
  }

  const lookLocalBtn = $('look-local-btn');
  if (lookLocalBtn) lookLocalBtn.addEventListener('click', () => { lookLocal(); });
  const lookAgainBtn = $('look-again-btn');
  if (lookAgainBtn) lookAgainBtn.addEventListener('click', () => { lookLocal(); });
  const comfyLink = $('open-comfyui-link');
  if (comfyLink) comfyLink.addEventListener('click', () => openLink('https://www.comfy.org/download'));
  const checkSpaceBtn = $('check-space-btn');
  if (checkSpaceBtn) checkSpaceBtn.addEventListener('click', () => { checkInstallSpace(); });
  const downloadBtn = $('download-ace-btn');
  if (downloadBtn) downloadBtn.addEventListener('click', () => { startInstallDownload(); });
  const resumeBtn = $('resume-download-btn');
  if (resumeBtn) resumeBtn.addEventListener('click', () => { startInstallDownload(); });
  const cancelDownloadBtn = $('cancel-download-btn');
  if (cancelDownloadBtn) cancelDownloadBtn.addEventListener('click', () => { cancelInstallDownload(); });
  const cancelInstallBtn = $('cancel-install-btn');
  if (cancelInstallBtn) cancelInstallBtn.addEventListener('click', () => {
    if (installWizard) installWizard.hidden = true;
  });
  const modelsField = $('models-dir');
  if (modelsField) modelsField.addEventListener('input', () => {
    const btn = $('download-ace-btn');
    if (btn) btn.disabled = true;
  });

  const copyEnginesBtn = $('copy-engines-btn');
  if (copyEnginesBtn) copyEnginesBtn.addEventListener('click', async () => {
    moveMessage('');
    try {
      const data = await api('/studio/engines/export');
      const exported = data && typeof data.text === 'string' ? data.text : '';
      try {
        const clip = navigator.clipboard;
        if (!clip || typeof clip.writeText !== 'function') throw new Error('clipboard unavailable');
        await clip.writeText(exported);
        const fallback = $('copy-engines-fallback');
        if (fallback) fallback.hidden = true;
        moveMessage('Copied. On your other computer, open Music engines and press Paste engines. Keys are not included.');
      } catch (err) {
        const fallback = $('copy-engines-fallback');
        if (fallback) {
          fallback.hidden = false;
          fallback.readOnly = true;
          fallback.value = exported;
          fallback.focus();
          fallback.select();
        }
        moveMessage('Could not copy automatically. The text is selected so you can copy it. Keys are not included.', true);
      }
    } catch (err) {
      moveMessage(err && err.message ? err.message : 'Could not copy engines.', true);
    }
  });
  const pasteEnginesBtn = $('paste-engines-btn');
  if (pasteEnginesBtn) pasteEnginesBtn.addEventListener('click', () => {
    const box = $('paste-engines-box');
    if (box) box.hidden = false;
  });
  const importEnginesBtn = $('import-engines-btn');
  if (importEnginesBtn) importEnginesBtn.addEventListener('click', async () => {
    const area = $('paste-engines-text');
    const text = area ? area.value : '';
    try {
      const data = await api('/studio/engines/import', { text: text });
      const added = engineNameList(data && data.added);
      const skipped = engineNameList(data && data.skipped);
      let line = 'Added: ' + added + '  Already here: ' + skipped;
      const errors = engineNameList(data && data.errors);
      if (errors) line += '  ' + errors;
      moveMessage(line);
      await loadEngines();
      await refreshWorkflows();
    } catch (err) {
      moveMessage(err && err.message ? err.message : 'Could not add those engines.', true);
    }
  });
}
function showLookLocalResult(data) {
  const container = $('look-local-result');
  if (!container) return;
  container.hidden = false;
  container.replaceChildren();
  const found = data && Array.isArray(data.found) ? data.found : [];
  const ready = found.filter(server => server && server.ok === true && server.url);
  if (!ready.length) {
    container.append(el('p', 'No ComfyUI is running on this computer yet. Start ComfyUI, then look again.', 'hint'));
    return;
  }
  ready.forEach((server, index) => {
    const div = el('div', '', 'found-server');
    div.append(el('div', 'Found: ' + server.url, 'server-label'));
    const helpId = 'help-use-comfy-' + index;
    const row = el('div', '', 'control-row');
    const btn = el('button', 'Use this ComfyUI', 'button secondary');
    btn.type = 'button';
    btn.title = 'Use this address and test it';
    btn.setAttribute('aria-describedby', helpId);
    btn.addEventListener('click', () => useLocalComfy(server.url));
    const help = el('button', '?', 'help-icon');
    help.type = 'button';
    help.title = 'Use this address and test it';
    help.setAttribute('aria-describedby', helpId);
    const note = el('span', 'Opens the server form with this address filled in, then tests it. We do not make a song.', 'sr-only');
    note.id = helpId;
    row.append(btn, help, note);
    div.append(row);
    container.append(div);
  });
}

// An open "More" menu closes when you click anywhere else, like any menu.
document.addEventListener('click', e => {
  document.querySelectorAll('details.take-more[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
});
// Landing behaviour: reopen the draft the person was last in. A pane that comes back to an empty
// form reads as "my work is gone", even when every take is safely on disk. The saved form copy is
// applied after the project loads: the other order lets the older server text overwrite it.
function applyDraft(draft) {
  for (const [key, value] of Object.entries(draft || {})) {
    const node = form.elements[key];
    if (!node) continue;
    if (key === 'engine') ensureEngineOption(value);
    if (node.type === 'checkbox') node.checked = Boolean(value); else node.value = value ?? '';
  }
}
(async () => {
  try {
    const backup = readBackup();
    const lastId = readLastProject();
    // readLastProject answers undefined only when storage is unavailable — the sandboxed pane.
    const storageWorks = lastId !== undefined;
    if (storageWorks && !lastId && !backup) return;
    const data = await api('/status');
    const projects = data.projects || [];
    // A backup names the draft the person was typing in, even a brand-new one (project_id null),
    // so it wins over the last project they merely had open.
    let projectId = backup && backup.draft ? backup.project_id : (lastId || null);
    if (!projectId && !backup && !storageWorks) {
      // Nothing can be remembered here, so the most recently edited project is the closest
      // thing to where the person left off.
      const stamp = p => Number(p && p.updated_at) || 0;
      const newest = projects.slice().sort((a, b) => stamp(b) - stamp(a))[0];
      projectId = newest ? newest.project_id : null;
    }
    const project = projectId ? projects.find(p => p.project_id === projectId) : null;
    if (project) await loadProject(project);
    const mine = backup && backup.draft
      && (backup.project_id == null ? !project : Boolean(project) && backup.project_id === project.project_id);
    if (mine) {
      applyDraft(backup.draft);
      dirty = true;
      revision++;
      buttons();
      // After buttons(): that helper paints the state pill from the dirty flag.
      draftStateLabel('Restored unsaved draft');
      scheduleAutosave();
    }
  } catch (e) { /* a restore is a convenience, never a failure the person must read */ }
})();

