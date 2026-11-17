// Round 9 UI hunt (lane hunt-ui): Music engines page, Generate song button and the
// model picker. Every test below failed on the code before its fix.
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
const css = await readFile(new URL('../standalone/static/style.css', import.meta.url), 'utf8');

const pause = (ms = 50) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => (node && node.textContent) || '';

const COMFY_URL = 'http://192.168.1.50:8188';
const ACE_MODEL_READY = { id: 'ace15_turbo', label: 'ACE-Step 1.5', status: 'ready' };

const PROVIDERS = {
  success: true,
  default: { label: 'Your Hermes model' },
  providers: [
    { slug: 'nous', name: 'Nous', configured: true, models: ['nous-small', 'nous-large'] },
    { slug: 'openai', name: 'OpenAI', configured: false, models: ['gpt-4', 'gpt-3.5'] },
  ],
};

function project() {
  return { project_id: 'p1', title: 'First song', caption: 'quiet', lyrics: 'hello',
    engine: 'ace_local', approved: false, updated_at: 10, takes: [] };
}

async function boot(extra = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = {
    engines: extra.engines ? extra.engines.slice() : [],
    workflows: extra.workflows ? extra.workflows.slice() : [],
    testReply: null,
    failAdd: '',
    rawAdd: null,          // { status, payload } answered as-is, like a real 422 from FastAPI
    installJob: { state: 'running', file: 'acestep_v1.5_turbo.safetensors', done_bytes: 1, total_bytes: 2,
      files_done: 1, files_total: 4, message: 'Downloading the music files', error: '' },
    models: extra.models || { success: true, providers: [], provider: null, model: null },
  };
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    if (path === '/api/studio/install/suggest') {
      return { ok: true, status: 200, json: async () => ({ models_dir: 'C:/ComfyUI/models' }) };
    }
    if (path === '/api/studio/install/plan' && method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ success: true, ok: true, enough_space: true,
        files: [{ name: 'acestep_v1.5_turbo.safetensors', folder: 'diffusion_models', size: 4800000000, present: false }],
        free_bytes: 90000000000, message: '' }) };
    }
    if (path === '/api/studio/install/jobs' && method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ success: true, job_id: 'job-1' }) };
    }
    if (path === '/api/studio/install/jobs/job-1' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, ...state.installJob }) };
    }
    if (path === '/api/studio/hardware') {
      return { ok: true, status: 200, json: async () => ({ success: true, devices: [] }) };
    }
    if (path === '/api/studio/engines/test' && method === 'POST') {
      const reply = state.testReply || { ok: false, message: 'Not tested.', problem: 'no_answer', models: [] };
      return { ok: true, status: 200, json: async () => ({ success: true, ...reply }) };
    }
    if (path === '/api/studio/engines' && method === 'POST') {
      if (state.rawAdd) {
        const raw = state.rawAdd;
        return { ok: raw.status < 400, status: raw.status, json: async () => raw.payload };
      }
      if (state.failAdd) {
        return { ok: false, status: 400, json: async () => ({ success: false, error: state.failAdd }) };
      }
      state.engines = [{ id: 't1', kind: 'comfyui', name: body && body.name, url: body && body.url,
        status: 'ready', workflows: [] }];
      return { ok: true, status: 200, json: async () => ({ success: true, ok: true,
        target: { id: 't1', name: body && body.name, url: body && body.url }, workflows: [] }) };
    }
    if (path === '/api/studio/engines' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, engines: state.engines.slice() }) };
    }
    if (String(path).startsWith('/api/studio/engines/') && method === 'DELETE') {
      state.engines = state.engines.filter(e => '/api/studio/engines/' + e.id !== path);
      return { ok: true, status: 200, json: async () => ({ success: true }) };
    }
    if (path === '/api/studio/workflows' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, workflows: state.workflows.slice() }) };
    }
    if (path === '/api/studio/models') {
      return { ok: true, status: 200, json: async () => state.models };
    }
    const settings = { success: true, settings: { minimax: {} }, samplers: ['euler'], schedulers: ['simple'],
      library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } };
    const table = {
      '/api/status': { success: true, engines: [{ id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)',
        available: true, kind: 'local', selectable_reason: null }], projects: [project()] },
      '/api/presets': { success: true, presets: [] },
      '/api/settings': settings,
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/assign': { success: true },
    };
    const payload = table[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  if (!window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', {
      value: array => { array[0] = 7; return array; }, configurable: true });
  }
  window.eval(appjs);
  await pause();
  return { window, calls, state };
}

const callsTo = (calls, path, method) => calls.filter(c => c.path === path && c.method === method);

function setValue(doc, id, value) {
  const field = doc.getElementById(id);
  assert.ok(field, id);
  field.value = value;
  field.dispatchEvent(new doc.defaultView.Event('input', { bubbles: true }));
  return field;
}

async function openServer(booted) {
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(80);
  doc.getElementById('add-server-btn').click();
  return doc;
}

async function clickId(doc, id, ms = 80) {
  doc.getElementById(id).click();
  await pause(ms);
}

// ------------------------------------------------------------------ double clicks

test('Add pressed twice adds the engine once', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  setValue(doc, 'server-name', 'Studio box');
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = { ok: true, kind: 'comfyui', message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
    models: [ACE_MODEL_READY] };
  await clickId(doc, 'test-server-btn');
  const add = doc.getElementById('add-engine-btn');
  assert.equal(add.disabled, false);

  // A person double-clicks: both clicks land before the first answer arrives.
  add.click();
  add.click();
  await pause(200);
  assert.equal(callsTo(booted.calls, '/api/studio/engines', 'POST').length, 1);
});

test('Download pressed twice starts one install job', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(80);
  doc.getElementById('add-local-btn').click();
  await pause(80);
  await clickId(doc, 'check-space-btn');
  const download = doc.getElementById('download-ace-btn');
  assert.equal(download.disabled, false);

  download.click();
  download.click();
  await pause(200);
  assert.equal(callsTo(booted.calls, '/api/studio/install/jobs', 'POST').length, 1);
});

// ------------------------------------------------------------- error text, not objects

test('a list-shaped server error is shown as words, not [object Object]', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  setValue(doc, 'server-name', 'Studio box');
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = { ok: true, kind: 'comfyui', message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
    models: [ACE_MODEL_READY] };
  await clickId(doc, 'test-server-btn');
  // FastAPI answers a bad field with a list of objects, which String() flattens.
  booted.state.rawAdd = { status: 422, payload: { detail: [
    { loc: ['body', 'url'], msg: 'value is not a valid URL', type: 'value_error' },
  ] } };
  await clickId(doc, 'add-engine-btn');

  const said = text(doc.getElementById('server-message'));
  assert.equal(doc.getElementById('server-message').classList.contains('error'), true);
  assert.match(said, /value is not a valid URL/);
  assert.equal(said.includes('[object Object]'), false);
  assert.equal(said.includes('{"loc"'), false);
});

// ------------------------------------------------------------------ model picker

test('filtering the model list does not forget the chosen model', async t => {
  const booted = await boot({ models: PROVIDERS });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await pause(60);
  const provider = doc.getElementById('idea-provider');
  const model = doc.getElementById('idea-model');
  provider.value = 'nous';
  provider.dispatchEvent(new booted.window.Event('change', { bubbles: true }));
  await pause(30);
  model.value = 'nous-large';
  model.dispatchEvent(new booted.window.Event('change', { bubbles: true }));
  await pause(30);
  assert.equal(JSON.parse(booted.window.localStorage.getItem('studio.modelChoice')).model, 'nous-large');

  // Typing in the filter box is a search, not a choice.
  setValue(doc, 'idea-filter', 'zzz-nothing-matches');
  await pause(30);
  const saved = JSON.parse(booted.window.localStorage.getItem('studio.modelChoice') || '{}');
  assert.equal(saved.provider, 'nous');
  assert.equal(saved.model, 'nous-large');

  setValue(doc, 'idea-filter', '');
  await pause(30);
  assert.equal(JSON.parse(booted.window.localStorage.getItem('studio.modelChoice') || '{}').model, 'nous-large');
});

// ------------------------------------------------------------------ announced results

test('engine page results are announced to a screen reader', () => {
  for (const id of ['test-result', 'troubleshooting', 'space-result', 'look-local-result']) {
    const tag = html.match(new RegExp('<div id="' + id + '"[^>]*>'));
    assert.ok(tag, id + ' exists');
    assert.match(tag[0], /aria-live="polite"/, id + ' is a live region');
  }
});

// ------------------------------------------------------------------ narrow screens

test('long server names and addresses wrap instead of overflowing a 400px column', async t => {
  const long = 'http://192.168.1.50:8188/' + 'a-long-path-segment/'.repeat(6);
  const booted = await boot({ engines: [{ id: 't1', name: 'My studio box upstairs'.repeat(3), url: long,
    kind: 'comfyui', workflows: [] }] });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(120);
  const style = doc.createElement('style');
  style.textContent = css;
  doc.head.appendChild(style);
  for (const selector of ['.engine-name', '.engine-address', '.found-server .server-label']) {
    // The engine rows are live in the page; the found-server row is not, so build it.
    const parts = selector.split(' ');
    const outer = doc.createElement('div');
    outer.className = parts[0].slice(1);
    const inner = doc.createElement(parts.length > 1 ? 'span' : 'div');
    inner.className = parts.length > 1 ? parts[1].slice(1) : parts[0].slice(1);
    outer.append(inner);
    doc.body.append(outer);
    const live = doc.querySelector('#engine-list ' + selector);
    assert.equal(booted.window.getComputedStyle(live || inner).overflowWrap, 'anywhere', selector);
  }
});

// ------------------------------------------------------------------ removing an engine

test('removing an engine asks for fresh status and workflows', async t => {
  const booted = await boot({
    engines: [{ id: 't1', name: 'Studio', url: COMFY_URL, kind: 'comfyui', workflows: [] }],
    workflows: [{ id: 'wf-ace', name: 'ACE-Step 1.5', target_id: 't1', placeholders: [] }],
  });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(120);
  const remove = [...doc.querySelectorAll('#engine-list .engine-row button')].find(b => b.textContent === 'Remove');
  assert.ok(remove, 'Remove button');
  remove.click();
  await pause(60);
  const confirm = doc.querySelector('.confirm-confirm');
  assert.ok(confirm, 'the in-page confirm appears');
  confirm.click();
  await pause(200);

  const deleted = booted.calls.findIndex(c => c.path === '/api/studio/engines/t1' && c.method === 'DELETE');
  assert.ok(deleted >= 0, 'the engine was deleted');
  const after = booted.calls.slice(deleted + 1);
  assert.ok(after.some(c => c.path === '/api/studio/workflows' && c.method === 'GET'), 'workflows refreshed');
  assert.ok(after.some(c => c.path === '/api/status' && c.method === 'GET'), 'engine status refreshed');
});
