// Find a model: typing a search with no provider chosen lists matching models from every provider,
// and picking one selects its provider too. Before, the model list stayed empty and the box looked broken.
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

async function boot({ projects, revamp = { installed: false, available: false }, added = [], providers = [] }) {
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
    if (path === '/api/studio/models') return json({ success: true, providers, provider: null, model: null });
    if (path === '/api/studio/revamp/added') return json({ items: added });
    if (path === '/api/studio/radio') return json({ success: true, station: { state: 'stopped', songs: [] }, message: '' });
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



const PROVIDERS = [
  { slug: 'nous', name: 'Nous Portal', configured: true, models: ['qwen/qwen4-max:free', 'tencent/hy4-flash:free'] },
  { slug: 'opencode-go', name: 'OpenCode Go', configured: true, models: ['deepseek-v4.1-flash', 'glm-5.3'] },
];

test('typing in Find a model lists matches from every provider', async t => {
  const { window } = await boot({ projects: [song('a')], providers: PROVIDERS });
  t.after(() => window.close());
  const doc = window.document;
  await pause(150);
  const filter = doc.getElementById('idea-filter');
  filter.value = 'flash';
  filter.dispatchEvent(new window.Event('input'));
  const labels = [...doc.getElementById('idea-model').options].slice(1).map(o => o.textContent);
  assert.deepEqual(labels, ['Nous Portal · tencent/hy4-flash:free', 'OpenCode Go · deepseek-v4.1-flash']);
  assert.match(doc.getElementById('idea-model').options[0].textContent, /2 found/);
});

test('picking a search result selects its provider and model', async t => {
  const { window } = await boot({ projects: [song('a')], providers: PROVIDERS });
  t.after(() => window.close());
  const doc = window.document;
  await pause(150);
  const filter = doc.getElementById('idea-filter');
  filter.value = 'deepseek';
  filter.dispatchEvent(new window.Event('input'));
  const model = doc.getElementById('idea-model');
  model.selectedIndex = 1;
  model.dispatchEvent(new window.Event('change'));
  assert.equal(doc.getElementById('idea-provider').value, 'opencode-go');
  assert.equal(model.value, 'deepseek-v4.1-flash');
});

test('Enter in the search box takes the first match', async t => {
  const { window } = await boot({ projects: [song('a')], providers: PROVIDERS });
  t.after(() => window.close());
  const doc = window.document;
  await pause(150);
  const filter = doc.getElementById('idea-filter');
  filter.value = 'qwen';
  filter.dispatchEvent(new window.Event('input'));
  filter.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter' }));
  assert.equal(doc.getElementById('idea-provider').value, 'nous');
  assert.equal(doc.getElementById('idea-model').value, 'qwen/qwen4-max:free');
});

test('a search that matches nothing says so', async t => {
  const { window } = await boot({ projects: [song('a')], providers: PROVIDERS });
  t.after(() => window.close());
  const doc = window.document;
  await pause(150);
  const filter = doc.getElementById('idea-filter');
  filter.value = 'zzz';
  filter.dispatchEvent(new window.Event('input'));
  assert.match(doc.getElementById('idea-model').options[0].textContent, /No model matches/);
});
