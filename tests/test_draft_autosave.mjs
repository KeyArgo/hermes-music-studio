// Leaving the form must never eat the words. The pane's sandboxed frame has no localStorage, so
// the server save is the real copy and the last-gasp request has to survive the page going away.
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

const EMPTY = { success: true, engine_available: false, engines: [], projects: [] };
const pause = (ms = 40) => new Promise(resolve => setTimeout(resolve, ms));

async function boot(options = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  const timers = [];
  let releaseCreate = null, createReleased = false;

  const nativeSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = (fn, delay, ...args) => {
    timers.push({ fn, delay });
    return nativeSetTimeout(fn, delay, ...args);
  };
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};

  if (options.storageUnavailable) {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() { throw new Error('localStorage is not available in this frame'); }
    });
  } else {
    if (options.lastProject) window.localStorage.setItem('studio.lastProject', options.lastProject);
    if (options.backup) window.localStorage.setItem('studio.draftBackup', JSON.stringify(options.backup));
  }

  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method: opts.method || 'GET', body, keepalive: opts.keepalive === true });
    if (path === '/api/create' && options.holdCreate && !createReleased) {
      await new Promise(resolve => { releaseCreate = () => { createReleased = true; resolve(); }; });
    }
    const table = {
      '/api/status': options.status || EMPTY,
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true, settings: {}, defaults: {}, limits: {},
        samplers: [], schedulers: [], library: { default: '/lib', active: '/lib' } },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/about/diagnostics': { success: true, text: '' },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/create': { success: true, project: { project_id: 'new1', updated_at: 2,
        title: 'Midnight draft', caption: '', lyrics: '', engine: 'ace_local', approved: false, takes: [] } },
    };
    if (path in (options.overrides || {})) table[path] = options.overrides[path];
    const payload = table[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await pause(50);
  return { dom, window, calls, timers, release: () => releaseCreate && releaseCreate() };
}

const typeTitle = (booted, value) => {
  const input = booted.window.document.querySelector('input[name=title]');
  input.value = value;
  input.dispatchEvent(new booted.window.Event('input', { bubbles: true }));
};

test('typing writes the draft backup synchronously, before any save', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());

  typeTitle(booted, 'Midnight draft');
  const stored = JSON.parse(booted.window.localStorage.getItem('studio.draftBackup'));
  assert.equal(stored.draft.title, 'Midnight draft', 'the words are on this machine at once');
  assert.equal(stored.project_id, null, 'a brand-new draft is not tied to a project yet');
  assert.equal(typeof stored.at, 'number', 'the backup carries when it was taken');
  assert.ok(!booted.calls.some(c => c.path === '/api/create'),
    'the backup is written before the 1.2 s save timer fires');
});

test('flushNow saves on pagehide with keepalive so the request outlives the page', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());

  typeTitle(booted, 'Midnight draft');
  booted.window.dispatchEvent(new booted.window.Event('pagehide'));
  await pause();

  const created = booted.calls.filter(c => c.path === '/api/create').pop();
  assert.ok(created, 'leaving saves immediately instead of waiting for the debounce');
  assert.equal(created.keepalive, true, 'the save is allowed to finish after the page goes away');
  assert.equal(created.body.draft.title, 'Midnight draft');
});

test('flushNow does not create a second song while a create is in flight', async t => {
  const booted = await boot({ holdCreate: true });
  t.after(() => booted.window.close());

  typeTitle(booted, 'Midnight draft');
  const timer = booted.timers.filter(item => item.delay === 1200).pop();
  assert.ok(timer, 'typing scheduled the debounced save');
  timer.fn();
  await pause(10);
  assert.equal(booted.calls.filter(c => c.path === '/api/create').length, 1,
    'the debounced save is on the wire');

  booted.window.dispatchEvent(new booted.window.Event('pagehide'));
  await pause();
  assert.equal(booted.calls.filter(c => c.path === '/api/create').length, 1,
    'leaving waits for the create already in flight instead of making a duplicate');
  booted.release();
  await pause();
});

test('the unsaved backup wins over the older server copy on landing', async t => {
  const status = { success: true, engines: [], projects: [
    { project_id: 'p1', title: 'Saved title', caption: 'old words', lyrics: '',
      engine: 'ace', approved: false, updated_at: 100, takes: [] }
  ] };
  const booted = await boot({
    status,
    lastProject: 'p1',
    backup: { project_id: 'p1', draft: { title: 'Unsaved words' }, at: 101 },
  });
  t.after(() => booted.window.close());

  const doc = booted.window.document;
  assert.equal(doc.querySelector('input[name=title]').value, 'Unsaved words',
    'the newer backup is applied after the project loads, not overwritten by it');
  assert.equal(doc.getElementById('draft-state').textContent, 'Restored unsaved draft');
});

test('without storage the pane lands in the most recently edited project', async t => {
  const status = { success: true, engines: [], projects: [
    { project_id: 'old', title: 'Older', engine: 'ace', approved: false, updated_at: 100, takes: [] },
    { project_id: 'new', title: 'Newest', engine: 'ace', approved: false, updated_at: 300, takes: [] },
  ] };
  const booted = await boot({ status, storageUnavailable: true });
  t.after(() => booted.window.close());

  const doc = booted.window.document;
  assert.equal(doc.querySelector('input[name=title]').value, 'Newest',
    'with no localStorage the newest project is the closest thing to the draft');
});
