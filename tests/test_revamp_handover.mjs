// Songs handed to REVamp: Play here plays the studio's own copy, a song deleted in Music Studio says
// so, and an entry added before songs were copied into REVamp offers a fix.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (error) {
  const elsewhere = process.env.JSDOM_PACKAGE
    || require('path').join(require('os').homedir(), '.hermes/hermes-agent/apps/desktop/package.json');
  ({ JSDOM } = createRequire(elsewhere)('jsdom'));
}

const html = await readFile(new URL('../standalone/static/index.html', import.meta.url), 'utf8');
const appjs = await readFile(new URL('../standalone/static/app.js', import.meta.url), 'utf8');

const pause = (ms = 80) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => (node && node.textContent) || '';

const ENGINES = [{ id: 'minimax_m3_comfy', label: 'MiniMax Music 3', kind: 'comfyui', available: true,
  supports_generate: true, selectable: true, selectable_reason: null,
  limits: { min_seconds: 10, max_seconds: 360, recommended_seconds: 120 },
  capabilities: { instrumental: true, vocal_language: true, repaint: false, continuation: true, batch: true } }];

function song(id, extra = {}) {
  return { project_id: id, title: 'Song ' + id, caption: 'folk', lyrics: 'la', engine: 'minimax_m3_comfy',
    approved: true, updated_at: 1763540000, takes: [], ...extra };
}

async function boot({ projects, revamp = { installed: false, available: false }, added = [] }) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = { projects: [...projects] };
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    const json = payload => ({ ok: true, status: 200, json: async () => payload });
    if (path === '/api/status') return json({ success: true, engines: ENGINES, projects: state.projects });
    if (path === '/api/studio/revamp/status') return json(revamp);
    if (path === '/api/studio/revamp/added') return json({ items: added });
    if (path === '/api/studio/radio') return json({ success: true, station: { state: 'stopped', songs: [] }, message: '' });
    if (path === '/api/fade') return json({ success: true });
    if (path === '/api/checkpoint') return json({ saved: 1, fidelity: 'seed' });
    if (path.startsWith('/api/checkpoints/')) return json({ checkpoints: [{ checkpoint_id: 'cp-9f1e2d3c', label: '' }] });
    if (path === '/api/checkpoint/restore') return json({ restored: 'cp-9f1e2d3c' });
    if (path === '/api/take-check') return json(body.retry ? { retried: false, reason: 'nothing measurable failed' }
      : { state: 'passed', summary: 'all checks passed', checks: [] });
    if (path === '/api/delete') {
      state.projects = state.projects.filter(p => p.project_id !== body.project_id);
      return json({ success: true });
    }
    if (path === '/api/studio/engines/test') {
      if (body.kind === 'ace_server') {
        return json({ ok: false, kind: 'ace_server', url: body.url, problem: 'is_comfyui',
          message: 'This address is a ComfyUI, not an ACE-Step server.', checks: [] });
      }
      return json({ ok: true, kind: 'comfyui', url: body.url, message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
        models: [{ id: 'ace15_turbo', label: 'ACE-Step 1.5', status: 'ready' }], checks: [] });
    }
    if (path === '/api/studio/engines' && method === 'GET') return json({ success: true, engines: [], ace_remote_url: '', hosted: [] });
    const table = {
      '/api/settings': { success: true, settings: { minimax: {} }, samplers: ['euler'], schedulers: ['simple'],
        library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
      '/api/presets': { success: true, presets: [] },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/about': { success: true, version: '1.0.0', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: null, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/setup/cloud': { success: true, endpoints: [] },
      '/api/studio/setup/hf': { success: true, status: { running: false, detail: 'idle' } },
    };
    const payload = table[path];
    return json(payload === undefined ? { success: true } : payload);
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await pause();
  return { window, calls };
}


const nav = doc => doc.getElementById('nav-listen');
const buttons = doc => [...doc.querySelectorAll('#revamp-added button')];

async function openRevamp(t, added) {
  const booted = await boot({ projects: [song('a')], revamp: { installed: true, reachable: true }, added });
  t.after(() => booted.window.close());
  nav(booted.window.document).click();
  await pause(150);
  return booted;
}

test('a song deleted in Music Studio shows as Deleted, not as a Play button that fails', async t => {
  const { window } = await openRevamp(t, [
    { id: 'studio:a:t1', title: 'Gone song', missing: true, playback: { url: '/api/media/0123456789abcdef' } },
    { id: 'studio:b:t2', title: 'Here song', playback: { url: '/api/media/fedcba9876543210' } }]);
  const doc = window.document;
  const deleted = buttons(doc).find(b => text(b) === 'Deleted');
  assert.ok(deleted); assert.equal(deleted.disabled, true);
  assert.ok(buttons(doc).some(b => text(b) === '▶ Play here' && !b.disabled));
  assert.match(text(doc.getElementById('revamp-added')), /Deleted in Music Studio/);
});

test('Play here plays the studio\'s own copy, never REVamp\'s link', async t => {
  const { window } = await openRevamp(t, [{ id: 'studio:a:t1', title: 'Frostfire Chant',
    playback: { url: '/api/radio/stream?url=http%3A%2F%2F127.0.0.1%3A8775%2Fapi%2Fprojects%2Fa' } }]);
  const doc = window.document;
  buttons(doc).find(b => text(b) === '▶ Play here').click();
  const src = doc.getElementById('player').getAttribute('src') || doc.getElementById('player').src;
  assert.match(src, /\/api\/projects\/a\/takes\/t1\/audio/);
  assert.doesNotMatch(src, /radio\/stream/);
});

test('an entry added the old way offers Fix for REVamp, which hands the take over again', async t => {
  const { window, calls } = await openRevamp(t, [{ id: 'studio:a:t1', title: 'Frostfire Chant', relink: true,
    playback: { url: '/api/radio/stream?url=x' } }]);
  const doc = window.document;
  assert.match(text(doc.getElementById('revamp-added')), /Needs a fix to play in REVamp/);
  buttons(doc).find(b => text(b) === 'Fix for REVamp').click();
  await pause(150);
  const sent = calls.find(c => c.path === '/api/studio/revamp/add-take');
  assert.equal(sent.body.project_id, 'a'); assert.equal(sent.body.take_id, 't1');
});

test('Create works before any takes have been handed over', async t => {
  // Create only needs a name, even on the first visit.
  const { window, calls } = await openRevamp(t, []);
  const doc = window.document;
  doc.getElementById('revamp-new-name').value = 'Drive';
  doc.getElementById('revamp-create').click();
  await pause(150);
  const sent = calls.find(c => c.path === '/api/studio/revamp/playlists' && c.method === 'POST');
  assert.deepEqual(sent.body, {name: 'Drive'});
});

test('a server that passes the Setup test refreshes the engine list right away', async t => {
  const { window, calls } = await boot({ projects: [song('a')] });
  t.after(() => window.close());
  const doc = window.document;
  const realFetch = window.fetch;
  window.fetch = async (url, opts = {}) => {
    if (String(url).endsWith('/api/studio/setup/comfy')) {
      calls.push({ path: '/api/studio/setup/comfy', method: 'POST' });
      return { ok: true, status: 200, json: async () => ({ ok: true, saved: true, detail: 'ACE-Step 1.5 is ready.', checks: [] }) };
    }
    return realFetch(url, opts);
  };
  const before = calls.filter(c => c.path === '/api/status').length;
  doc.getElementById('setup-server-url').value = 'http://192.168.1.50:8188';
  doc.getElementById('setup-server-test').click();
  await pause(250);
  assert.ok(calls.some(c => c.path === '/api/studio/setup/comfy'));
  assert.ok(calls.filter(c => c.path === '/api/status').length > before, 'status was asked for again');
});
