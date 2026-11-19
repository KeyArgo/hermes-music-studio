// Round 13 live-test fixes on the page itself:
//
//  * the ACE-Step server row on the Music engines page offered Test but no Remove, so an address
//    could be replaced but never taken away;
//  * "Change this part" on an engine without repaint defaulted to a 0-8 s range, and MiniMax
//    refuses anything under 10 s — the button paid a failed take for its default;
//  * the same form sent the typed words as the whole caption, so the song's own description was
//    replaced by "add a climax".
//
// Promised here: Remove asks first and posts the route that clears the address; the default range
// is one the engine will render; a range typed too short is grown to that minimum before it is sent.
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

const ACE_SERVER = 'http://192.168.1.50:8000';
const ACE_LIMITS = { min_seconds: 10, max_seconds: 180, recommended_seconds: 120 };
const MINIMAX_LIMITS = { min_seconds: 10, max_seconds: 360, recommended_seconds: 120 };

// MiniMax Music 3 cannot repaint a range, so "Change this part" renders the section on its own.
const MINIMAX = {
  id: 'minimax_m3_comfy', label: 'MiniMax Music 3', kind: 'comfyui', available: true,
  supports_generate: true, selectable: true, selectable_reason: null, limits: MINIMAX_LIMITS,
  capabilities: { instrumental: true, vocal_language: true, repaint: false, continuation: true, batch: true },
};
const ACE = {
  id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', kind: 'local', available: true,
  supports_generate: true, selectable: true, selectable_reason: null, limits: ACE_LIMITS,
  capabilities: { instrumental: true, vocal_language: true, repaint: true, continuation: true, batch: true },
};

function song(extra = {}) {
  return { project_id: 'p1', title: 'First song', caption: 'quiet', lyrics: 'hello',
    engine: 'ace_local', approved: true, updated_at: 10, takes: [],
    ...extra };
}

function take(extra = {}) {
  return { take_id: 't1', state: 'succeeded', engine_id: 'minimax_m3_comfy', requested_seed: 7,
    audio_seconds: 20, ...extra };
}

async function boot({ projects = [song()], engines = [ACE, MINIMAX], aceRemoteUrl = '' } = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = { aceRemoteUrl };
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    const json = payload => ({ ok: true, status: 200, json: async () => payload });
    if (path === '/api/status') {
      return json({ success: true, engines, projects });
    }
    if (path === '/api/studio/engines' && method === 'GET') {
      return json({ success: true, engines: [], ace_remote_url: state.aceRemoteUrl, hosted: [] });
    }
    if (path === '/api/studio/setup/ace/remove' && method === 'POST') {
      // The route clears the one saved address, so the list the page reloads has none.
      state.aceRemoteUrl = '';
      return json({ success: true, ok: true, ace_remote_url: '' });
    }
    const table = {
      '/api/settings': { success: true, settings: { minimax: {} }, samplers: ['euler'],
        schedulers: ['simple'], library: { default: '/lib', active: '/lib', moved: false,
          projects: 0, audio_files: 0, bytes: 0 } },
      '/api/presets': { success: true, presets: [] },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: null, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/setup/cloud': { success: true, endpoints: [] },
      '/api/studio/setup/hf': { success: true, status: { running: false, detail: 'idle' } },
      '/api/section/render': { success: true, take: { take_id: 't2', state: 'queued' } },
    };
    const payload = table[path];
    if (payload === undefined) return json({ success: true });
    return json(payload);
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await pause();
  return { window, calls };
}

async function confirmInPage(window, accept = true) {
  // askConfirm draws its own dialog: window.confirm() is a no-op inside the desktop pane.
  const dialog = window.document.querySelector('.confirm-dialog');
  assert.ok(dialog, 'a confirmation is asked before the address is removed');
  const label = accept ? '.confirm-confirm' : '.confirm-cancel';
  dialog.querySelector(label).click();
  await pause();
  return dialog;
}

test('the ACE-Step server row offers Remove, asks first and clears the saved address', async t => {
  const { window, calls } = await boot({ aceRemoteUrl: ACE_SERVER });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-engines').click();
  await pause(120);

  const row = [...doc.querySelectorAll('#engine-list .engine-row')]
    .find(node => text(node).includes('ACE-Step server'));
  assert.ok(row, 'the saved ACE-Step server is a row on this page');
  assert.ok([...row.querySelectorAll('button')].some(b => text(b) === 'Test'), 'Test stays');
  const remove = [...row.querySelectorAll('button')].find(b => text(b) === 'Remove');
  assert.ok(remove, 'the row can now be removed, not only tested');

  // Cancel means cancel: nothing is posted and the row stays.
  remove.click();
  await pause(30);
  const dialog = await confirmInPage(window, false);
  assert.match(text(dialog), /192\.168\.1\.50:8000/, 'the dialog names the address it would forget');
  assert.equal(calls.some(c => c.path === '/api/studio/setup/ace/remove'), false,
    'a cancelled removal posts nothing');

  [...doc.querySelectorAll('#engine-list .engine-row button')]
    .find(b => text(b) === 'Remove').click();
  await pause(30);
  await confirmInPage(window, true);
  assert.ok(calls.some(c => c.path === '/api/studio/setup/ace/remove' && c.method === 'POST'),
    'the row posts the route that clears ace_remote_url');
  assert.doesNotMatch(text(doc.getElementById('engine-list')), /ACE-Step server/,
    'the row is gone once its address is gone');
});

test('a range the engine refuses is not the default on an engine without repaint', async t => {
  const { window } = await boot({ projects: [song({ takes: [take()] })] });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-library').click();
  await pause(120);

  const card = doc.querySelector('[data-project="p1"]');
  const redo = [...card.querySelectorAll('button')].find(b => text(b) === 'Change this part');
  assert.ok(redo, 'a finished take offers the control');
  redo.click();
  await pause(30);

  const form = card.querySelector('.snippet-form');
  const start = form.querySelector('.snippet-start');
  const end = form.querySelector('.snippet-end');
  assert.equal(start.value, '0');
  assert.ok(Number(end.value) >= 10,
    `the default range must clear MiniMax's 10 s floor, not 8 (got ${end.value})`);
});

test('a typed range below the engine minimum is grown to it before it is sent', async t => {
  const { window, calls } = await boot({ projects: [song({ takes: [take()] })] });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-library').click();
  await pause(120);

  const card = doc.querySelector('[data-project="p1"]');
  [...card.querySelectorAll('button')].find(b => text(b) === 'Change this part').click();
  await pause(30);
  const form = card.querySelector('.snippet-form');
  const start = form.querySelector('.snippet-start');
  const end = form.querySelector('.snippet-end');
  start.value = '2';
  end.value = '6';                       // 4 s: refused by MiniMax
  form.querySelector('.snippet-words').value = 'dreamier strings';
  [...form.querySelectorAll('button')].find(b => text(b) === 'Render the new part').click();
  await pause(80);

  const posted = calls.filter(c => c.path === '/api/section/render').pop();
  assert.ok(posted, 'the section render is still the route used');
  assert.equal(posted.body.start_s, 2);
  assert.equal(posted.body.end_s, 12, 'the end was grown to the engine minimum before sending');
  assert.equal(end.value, '12', 'the field shows the range that was actually sent');
  assert.equal(posted.body.caption, 'dreamier strings', 'the words still travel as the change');
});
