// Radio mode in the REVamp screen: hidden unless REVamp answers, and it starts what the person typed.
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

const pause = (ms = 120) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => (node && node.textContent) || '';

const ENGINES = [{ id: 'ace_local', label: 'ACE-Step 1.5', kind: 'local', available: true,
  supports_generate: true, selectable: true, selectable_reason: null,
  limits: { min_seconds: 10, max_seconds: 180, recommended_seconds: 120 },
  capabilities: { instrumental: true, vocal_language: true, repaint: true, continuation: true, batch: true } }];

const STATION = {
  state: 'running', theme: 'Warm folk for a long drive', instrumental: false,
  engine_id: 'ace_local', target_count: 10, done: 2,
  playlist_name: 'Music Studio Radio', playlist_id: 'pl01',
  songs: [
    { project_id: 'p1', take_id: 't1', title: 'Sunday Drive', exported_path: '/music/a.wav', track_id: 'aaaaaaaaaaaaaaaa', appended: true },
    { project_id: 'p2', take_id: 't2', title: 'Dust Road', exported_path: '/music/b.wav', track_id: 'bbbbbbbbbbbbbbbb', appended: true },
  ],
  last_error: null, current_title: 'Night Drive', current_take_id: 't3',
};
const RUNNING = {
  success: true, station: STATION, songs: STATION.songs,
  message: 'Song 3 of 10: Night Drive, rendering…', running: true,
};

async function boot({ revamp = { available: true, player_connected: true } } = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    const json = payload => ({ ok: true, status: 200, json: async () => payload });
    if (path === '/api/status') return json({ success: true, engines: ENGINES, projects: [] });
    if (path === '/api/studio/revamp/status') return json(revamp);
    if (path === '/api/studio/revamp/added') return json({ success: true, items: [] });
    if (path === '/api/studio/revamp/playlists') return json({ playlists: [] });
    if (path === '/api/studio/radio') return json(RUNNING);
    if (path === '/api/studio/radio/start') {
      return json({ ...RUNNING, message: 'Writing song 1 of 10…' });
    }
    if (path.startsWith('/api/studio/radio/')) return json(RUNNING);
    const table = {
      '/api/settings': { success: true, settings: { minimax: {} }, samplers: ['euler'], schedulers: ['simple'],
        library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
      '/api/presets': { success: true, presets: [] },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/about': { success: true, version: '1.0.0', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: null, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/engines': { success: true, engines: [], ace_remote_url: '', hosted: [] },
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

async function openRevamp(window) {
  window.document.getElementById('nav-listen').click();
  await pause(200);
}

test('Radio stays hidden when REVamp is not installed', async t => {
  const { window } = await boot({ revamp: { available: false, reason: 'REVamp is not installed.' } });
  t.after(() => window.close());
  await openRevamp(window);
  assert.equal(window.document.getElementById('radio-panel').hidden, true,
    'a station nobody can hear is not offered');
});

test('Radio appears with REVamp and says what the station is doing', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  await openRevamp(window);
  const doc = window.document;
  assert.equal(doc.getElementById('radio-panel').hidden, false);
  assert.match(text(doc.getElementById('radio-status')), /Song 3 of 10: Night Drive/);
  assert.equal(doc.querySelectorAll('#radio-songs .compact-row').length, 2, 'the songs added so far, one line each');
  assert.equal(doc.getElementById('radio-pause').hidden, false, 'a running station can be paused');
});

test('Start sends the fields the station needs', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  await openRevamp(window);
  const doc = window.document;
  doc.getElementById('radio-theme').value = 'Slow soul for a rainy kitchen';
  doc.getElementById('radio-count').value = '4';
  doc.getElementById('radio-instrumental').value = '1';
  doc.getElementById('radio-engine').value = 'minimax_m3_comfy';
  doc.getElementById('radio-playlist').value = 'Night Shift';
  doc.getElementById('radio-start').click();
  await pause(200);
  const start = calls.filter(call => call.path === '/api/studio/radio/start');
  assert.equal(start.length, 1);
  assert.equal(start[0].method, 'POST');
  assert.deepEqual(start[0].body, {
    theme: 'Slow soul for a rainy kitchen',
    target_count: 4,
    instrumental: true,
    engine_id: 'minimax_m3_comfy',
    playlist_name: 'Night Shift',
  });
  assert.match(text(doc.getElementById('radio-status')), /Writing song 1 of 10/);
});
