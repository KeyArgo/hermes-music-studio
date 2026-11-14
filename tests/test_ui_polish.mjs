// The studio runs inside a sandboxed desktop frame where window.confirm() always says no, the
// poll must not wipe an open inline editor, and the JSON shapes from the routes are nested.
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
    approved: true, takes: [{ take_id: 't1', state: 'succeeded', engine_id: 'ace_local',
      requested_seed: 7 }] }] };

const text = node => (node && node.textContent) || '';
const pause = (ms = 35) => new Promise(resolve => setTimeout(resolve, ms));

async function boot(overrides = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  const timers = [];
  const nativeSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = (fn, delay, ...args) => {
    timers.push({ fn, delay });
    return nativeSetTimeout(fn, delay, ...args);
  };
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, options = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ path, method: options.method || 'GET', body });
    const table = {
      '/api/status': STATUS,
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true, settings: {},
        minimax: { steps: 30, cfg: 3.5, cfg_scale: 1, top_k: 20, sampler: 'euler', scheduler: 'simple' },
        samplers: ['euler'], schedulers: ['simple'],
        library: { default: '/lib', active: '/lib', moved: false, projects: 1, audio_files: 1, bytes: 0 } },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/about/diagnostics': { success: true, text: 'Music Studio 0.2.5' },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/library/reset': { success: true, library: { default: '/lib', active: '/lib', moved: false,
        projects: 1, audio_files: 1, bytes: 0 } },
      '/api/delete': { success: true },
      '/api/take/delete': { success: true },
    };
    let payload = table[path];
    if (path in overrides) payload = overrides[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await pause(40);
  return { dom, window, calls, timers };
}

const openLibrary = async booted => {
  booted.window.document.getElementById('nav-library').click();
  await pause();
  return booted.window.document;
};

const dialog = doc => doc.querySelector('.confirm-dialog');

test('deleting a song asks in the page and posts only after Confirm', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openLibrary(booted);
  // The desktop frame has no allow-modals: any native dialog is a bug, not a fallback.
  booted.window.confirm = () => { throw new Error('window.confirm was called'); };
  booted.window.alert = () => { throw new Error('window.alert was called'); };
  booted.window.prompt = () => { throw new Error('window.prompt was called'); };

  [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Delete').click();
  await pause(10);
  assert.ok(dialog(doc), 'an in-page dialog is shown');
  assert.match(text(dialog(doc)), /Delete .*First song/);
  assert.ok(!booted.calls.some(c => c.path === '/api/delete'), 'nothing is deleted before confirmation');

  [...dialog(doc).querySelectorAll('button')].find(b => /Delete/.test(text(b))).click();
  await pause();
  const posted = booted.calls.filter(c => c.path === '/api/delete').pop();
  assert.ok(posted, 'Confirm posts the delete');
  assert.equal(posted.body.project_id, 'p1');
  assert.equal(dialog(doc), null, 'the dialog is gone');
});

test('cancelling the delete dialog posts nothing', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openLibrary(booted);
  booted.window.confirm = () => { throw new Error('window.confirm was called'); };

  [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Delete').click();
  await pause(10);
  [...dialog(doc).querySelectorAll('button')].find(b => text(b) === 'Cancel').click();
  await pause();
  assert.equal(dialog(doc), null, 'Cancel closes the dialog');
  assert.ok(!booted.calls.some(c => c.path === '/api/delete'), 'Cancel posts nothing');
});

test('the confirmation is keyboard-first: Cancel is focused, Escape cancels, a stray Enter never deletes', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openLibrary(booted);
  booted.window.confirm = () => { throw new Error('window.confirm was called'); };
  const deleteSong = () => [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Delete').click();

  deleteSong();
  await pause(10);
  assert.equal(doc.activeElement, dialog(doc).querySelector('.confirm-cancel'), 'focus starts on Cancel');
  dialog(doc).dispatchEvent(new booted.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await pause();
  assert.equal(dialog(doc), null, 'Escape cancels');
  assert.ok(!booted.calls.some(c => c.path === '/api/delete'), 'Escape posts nothing');

  deleteSong();
  await pause(10);
  // Enter belongs to the focused button, which is Cancel: it must not delete.
  dialog(doc).dispatchEvent(new booted.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await pause();
  assert.ok(!booted.calls.some(c => c.path === '/api/delete'), 'a stray Enter posts nothing');
  // Choosing Delete itself confirms.
  dialog(doc).querySelector('.confirm-confirm').click();
  await pause();
  assert.ok(booted.calls.some(c => c.path === '/api/delete'), 'the Delete button confirms');
});

test('reset library and diagnostics use POST, as their routes require', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;

  doc.getElementById('library-reset').click();
  await pause();
  const reset = booted.calls.filter(c => c.path === '/api/library/reset').pop();
  assert.ok(reset, 'reset requested the library');
  assert.equal(reset.method, 'POST', 'the reset route is POST-only');

  doc.getElementById('copy-diagnostics').click();
  await pause();
  const diagnostics = booted.calls.filter(c => c.path === '/api/about/diagnostics').pop();
  assert.ok(diagnostics, 'diagnostics requested');
  assert.equal(diagnostics.method, 'POST', 'the diagnostics route is POST-only');
});

test('a polling refresh keeps an open inline Rename input and its text', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openLibrary(booted);

  [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Rename').click();
  const input = doc.querySelector('#library .rename-input');
  assert.ok(input, 'the inline Rename form is open');
  input.value = 'Half-typed title';

  const poll = booted.timers.filter(timer => timer.delay >= 2000).pop();
  assert.ok(poll, 'the page scheduled its polling refresh');
  poll.fn();
  await pause();

  const still = doc.querySelector('#library .rename-input');
  assert.ok(still, 'the polling refresh did not rebuild the library under the editor');
  assert.equal(still.value, 'Half-typed title', 'the typed text survived');
});

test('the REVamp playlist picker reads the nested playlists payload', async t => {
  const booted = await boot({
    '/api/studio/revamp/status': { available: true, player_connected: false },
    '/api/studio/revamp/playlists': { success: true,
      playlists: { playlists: [{ id: 'pl09', name: 'Drive mix' }] } },
    '/api/studio/revamp/added': { success: true, items: [] },
  });
  t.after(() => booted.window.close());
  const doc = booted.window.document;

  doc.getElementById('nav-listen').click();
  await pause(60);

  const options = [...doc.getElementById('revamp-playlist').options].map(o => ({ value: o.value, name: text(o) }));
  assert.ok(options.some(o => o.value === 'pl09' && o.name === 'Drive mix'),
    'the playlist returned by REVamp is offered, not lost to a TypeError');
});

test('a pane reply with only a draft fills the form instead of throwing', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const sent = [];
  Object.defineProperty(booted.window, 'parent', {
    value: { postMessage: message => sent.push(message) }, configurable: true
  });

  doc.getElementById('idea-input').value = 'a night drive, warm synths';
  doc.getElementById('idea-send').click();
  await pause(40);
  const ask = sent.find(m => m.type === 'hermes-music-studio:draft');
  assert.ok(ask, 'Discuss idea asks the pane to draft');

  booted.window.dispatchEvent(new booted.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:draft-result', id: ask.id, ok: true,
            draft: { title: 'Night Drive', caption: 'warm synthwave' } }
  }));
  await pause(40);

  assert.equal(doc.querySelector('input[name=title]').value, 'Night Drive',
    'the draft filled the form even though the reply had no content');
  assert.match(text(doc.getElementById('idea-connection')) + text(doc.getElementById('idea-messages')),
    /draft/i, 'the page says it received a draft');
});

test('an empty provider list means Hermes drafts, not that the model is unavailable', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const line = text(doc.getElementById('idea-connection'));
  assert.match(line, /through Hermes|Hermes agent/i, 'the page says drafting goes through Hermes');
  assert.doesNotMatch(line, /unavailable/i, 'an empty list is not an error');
});
