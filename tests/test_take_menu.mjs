// The More menu on a take: every choice closes the menu (so it never covers the box the choice
// opened), the menu opens upward near the player bar, and every result shows where the person is
// looking instead of only in the status line at the top of the library.
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


const take = { take_id: 't1', state: 'succeeded', engine_id: 'minimax_m3_comfy', requested_seed: 7, edits: [] };
const withTake = () => [song('a', { takes: [take] })];

async function openLibrary(t) {
  const booted = await boot({ projects: withTake() });
  t.after(() => booted.window.close());
  booted.window.document.getElementById('nav-library').click();
  await pause(120);
  return booted;
}
const menuOf = doc => doc.querySelector('.take .take-more');
const item = (doc, label) => [...doc.querySelectorAll('.take-menu button')].find(b => text(b).trim() === label);
const toast = doc => doc.getElementById('studio-toast');

test('every finished take keeps its extra actions in one More menu', async t => {
  const { window } = await openLibrary(t);
  const doc = window.document;
  for (const label of ['Save spot', 'Back to last spot', 'Check this take', 'Fix it', 'Fade out', 'Extend…', 'Delete take']) {
    assert.ok(item(doc, label), `${label} is in the menu`);
  }
});

test('a choice closes the menu and its result shows above the player', async t => {
  const { window, calls } = await openLibrary(t);
  const doc = window.document;
  menuOf(doc).open = true;
  item(doc, 'Fade out').click();
  await pause(120);
  assert.ok(calls.some(c => c.path === '/api/fade'));
  assert.equal(doc.querySelector('.take-more[open]'), null, 'no menu is left open');
  assert.equal(toast(doc).hidden, false);
  assert.equal(text(toast(doc)), 'Added a faded copy as a new take.');
});

test('Extend opens its box and the menu no longer covers it', async t => {
  const { window } = await openLibrary(t);
  const doc = window.document;
  menuOf(doc).open = true;
  item(doc, 'Extend…').click();
  await pause();
  assert.ok(doc.querySelector('.take .extend-form'), 'the extend box is open');
  assert.equal(menuOf(doc).open, false);
});

test('Back to last spot names no internal id', async t => {
  const { window } = await openLibrary(t);
  const doc = window.document;
  item(doc, 'Back to last spot').click();
  await pause(150);
  assert.doesNotMatch(text(toast(doc)), /cp-9f1e2d3c/);
  assert.match(text(toast(doc)), /^Back to your saved spot/);
});

test('Fix it with nothing to fix says so once', async t => {
  const { window } = await openLibrary(t);
  const doc = window.document;
  item(doc, 'Fix it').click();
  await pause(150);
  assert.equal(text(toast(doc)), 'Nothing measurable failed, so there is nothing to fix.');
});

test('near the player bar the menu opens upward', async t => {
  const { window } = await openLibrary(t);
  const doc = window.document;
  const more = menuOf(doc);
  const rect = (top, bottom) => () => ({ top, bottom, left: 0, right: 0, width: 0, height: bottom - top });
  doc.querySelector('.player').getBoundingClientRect = rect(700, 780);
  more.querySelector('.take-menu').getBoundingClientRect = rect(650, 960);
  more.open = true;
  more.dispatchEvent(new window.Event('toggle'));
  assert.ok(more.classList.contains('up'));
  more.querySelector('.take-menu').getBoundingClientRect = rect(200, 500);
  more.dispatchEvent(new window.Event('toggle'));
  assert.equal(more.classList.contains('up'), false, 'with room below it opens downward');
});
