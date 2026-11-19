// Round 18: library dates and delete-many, friendly engine names on takes, and a ComfyUI typed into
// the ACE-Step server form (the owner hit a dead end there on a real Windows PC).
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

async function boot({ projects }) {
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

async function openLibrary(window) {
  window.document.getElementById('nav-library').click();
  await pause(120);
}

test('every song says when it was made, and old songs fall back to their first take', async t => {
  const { window } = await boot({ projects: [
    song('a', { created_at: 1763500000 }),
    song('b', { takes: [{ take_id: 't1', state: 'succeeded', engine_id: 'minimax_m3_comfy', created_at: 1763400000 }] }),
  ] });
  t.after(() => window.close());
  await openLibrary(window);
  const cards = [...window.document.querySelectorAll('#library .project')];
  assert.equal(cards.length, 2);
  for (const card of cards) assert.match(text(card.querySelector('.song-dates')), /^Made /);
  const take = window.document.querySelector('#library .take');
  assert.match(text(take), /made /, 'a take row says when it was made');
  assert.match(text(take), /MiniMax Music 3/, 'a take names its engine the way the menu does');
  assert.doesNotMatch(text(take), /minimax_m3_comfy/, 'not the internal id');
});

test('Select, Select all, then one confirmation deletes every chosen song', async t => {
  const { window, calls } = await boot({ projects: [song('a'), song('b'), song('c')] });
  t.after(() => window.close());
  await openLibrary(window);
  const doc = window.document;
  const del = doc.getElementById('library-delete-selected');
  assert.ok(del.hidden, 'delete-many is out of the way until Select is pressed');
  doc.getElementById('library-select').click();
  await pause();
  assert.equal(doc.querySelectorAll('#library .library-pick').length, 3, 'each song gets a checkbox');
  assert.ok(del.disabled, 'nothing chosen, nothing to delete');
  doc.getElementById('library-select-all').click();
  await pause();
  assert.match(text(del), /Delete selected \(3\)/);
  del.click();
  await pause();
  const dialog = doc.querySelector('.confirm-dialog');
  assert.ok(dialog, 'one confirmation for the batch');
  assert.match(text(dialog), /Delete 3 songs/);
  dialog.querySelector('.confirm-confirm').click();
  await pause(200);
  const deletes = calls.filter(c => c.path === '/api/delete').map(c => c.body.project_id).sort();
  assert.deepEqual(deletes, ['a', 'b', 'c']);
  assert.match(text(doc.getElementById('library-status')), /Deleted 3\./);
});

test('cancelling the batch confirmation deletes nothing', async t => {
  const { window, calls } = await boot({ projects: [song('a'), song('b')] });
  t.after(() => window.close());
  await openLibrary(window);
  const doc = window.document;
  doc.getElementById('library-select').click(); await pause();
  doc.querySelector('#library .library-pick').click(); await pause();
  doc.getElementById('library-delete-selected').click(); await pause();
  doc.querySelector('.confirm-dialog .confirm-cancel').click(); await pause();
  assert.equal(calls.filter(c => c.path === '/api/delete').length, 0);
});

test('a ComfyUI typed into the ACE-Step server form is switched to ComfyUI and tested as one', async t => {
  const { window, calls } = await boot({ projects: [song('a')] });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-engines').click();
  await pause(120);
  const ace = doc.querySelector('input[name="engine-kind"][value="ace_server"]');
  assert.ok(ace, 'the form offers the ACE-Step server kind');
  ace.checked = true; ace.dispatchEvent(new window.Event('change', { bubbles: true }));
  doc.getElementById('server-url').value = 'http://10.0.0.52:8188';
  doc.getElementById('server-url').dispatchEvent(new window.Event('input', { bubbles: true }));
  doc.getElementById('test-server-btn').click();
  await pause(200);
  const kinds = calls.filter(c => c.path === '/api/studio/engines/test').map(c => c.body.kind);
  assert.deepEqual(kinds, ['ace_server', 'comfyui'], 'tested as ACE, then as ComfyUI');
  assert.equal(doc.querySelector('input[name="engine-kind"]:checked').value, 'comfyui');
  assert.match(text(doc.getElementById('test-result')), /ACE-Step 1\.5/);
  assert.match(text(doc.getElementById('test-result')), /switched to ComfyUI/);
  assert.equal(doc.getElementById('add-engine-btn').disabled, false, 'ready to add');
});
