// The takes choice beside the render button. One take keeps the plain /generate path;
// more takes use the serial /batch route, and the number survives a reload.
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

const STATUS = { success: true, engine_available: true,
  engines: [{ id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', available: true }],
  projects: [{ project_id: 'p1', title: 'First song', caption: 'quiet', engine: 'ace',
    approved: true, takes: [] }] };

const empty = { success: true };

async function boot(options = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  window.confirm = () => true;
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  if (options.savedTakes) window.localStorage.setItem('studio.takes', String(options.savedTakes));
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method: opts.method || 'GET', body });
    const table = {
      '/api/status': STATUS,
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true, settings: {}, defaults: {}, limits: {},
        samplers: [], schedulers: [], library: { default: '/lib', active: '/lib' } },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/models': { success: true, providers: [], provider: 'nous', model: 'm' },
      '/api/about': { success: true, version: '0.2.5', build: { version: '0.2.5-dev.17',
        commit: 'abcdef1234567890', commit_short: 'abcdef12', dirty: false },
        repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/generate': empty,
      '/api/batch': empty,
    };
    let payload = table[path];
    if (path in (options.overrides || {})) payload = options.overrides[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await new Promise(resolve => setTimeout(resolve, 40));
  return { window, calls };
}

async function openApprovedDraft(booted) {
  const doc = booted.window.document;
  doc.getElementById('nav-library').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  doc.querySelector('#library .project-title').click();
  await new Promise(resolve => setTimeout(resolve, 20));
  return doc;
}

test('takes defaults to one, and one take still posts /generate', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openApprovedDraft(booted);
  assert.equal(doc.getElementById('render-takes').value, '1');
  assert.equal(doc.getElementById('render').disabled, false, 'the draft can render');

  doc.getElementById('render').click();
  await new Promise(resolve => setTimeout(resolve, 40));

  const generated = booted.calls.filter(c => c.path === '/api/generate').pop();
  assert.ok(generated, 'one take uses the existing generate route');
  assert.equal(generated.body.project_id, 'p1');
  assert.equal(generated.body.engine_id, 'ace_local');
  assert.ok(!booted.calls.some(c => c.path === '/api/batch'), 'no batch for a single take');
});

test('more takes posts /batch with the count and the chosen engine, and says so', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openApprovedDraft(booted);
  doc.getElementById('render-takes').value = '3';

  doc.getElementById('render').click();
  await new Promise(resolve => setTimeout(resolve, 40));

  const posted = booted.calls.filter(c => c.path === '/api/batch').pop();
  assert.ok(posted, 'the batch route is used');
  assert.deepEqual(posted.body, { project_id: 'p1', count: 3, engine_id: 'ace_local' });
  assert.ok(!booted.calls.some(c => c.path === '/api/generate'), 'generate is not also called');
  assert.match(doc.getElementById('form-message').textContent,
    /Rendering 3 takes, one after another/);
});

test('the takes choice is remembered in localStorage', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openApprovedDraft(booted);
  const select = doc.getElementById('render-takes');
  select.value = '4';
  select.dispatchEvent(new booted.window.Event('change'));
  assert.equal(booted.window.localStorage.getItem('studio.takes'), '4');
});

test('a saved takes choice comes back on the next visit', async t => {
  const booted = await boot({ savedTakes: 2 });
  t.after(() => booted.window.close());
  const doc = await openApprovedDraft(booted);
  assert.equal(doc.getElementById('render-takes').value, '2');
});
