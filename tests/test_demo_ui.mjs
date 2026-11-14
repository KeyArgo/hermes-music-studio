// The shipped demo song, exercised in a real DOM: one calm control on Create and in an empty
// library must play the demo through the footer player and show the facts /api/demo reports.
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
const DEMO = {
  demo: true,
  title: 'Afterglow',
  caption: 'warm acoustic folk, fingerpicked guitar, close-mic vocal',
  word_recovery: '3/3',
  peak_dbfs: -4.11,
  audio_url: '/api/plugins/hermes-music-studio/demo/audio',
};

async function boot(overrides = {}, options = {}) {
  const pageUrl = 'http://127.0.0.1:8794/' + (options.token ? `?token=${encodeURIComponent(options.token)}` : '');
  const dom = new JSDOM(html, { url: pageUrl, runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  window.confirm = () => true;
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () { this.__loaded = true; };
  window.fetch = async (url, options = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ path, method: options.method || 'GET', body });
    const table = {
      '/api/status': EMPTY,
      '/api/demo': DEMO,
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true,
        settings: { comfy_url: '', minimax: { steps: 30, cfg: 4, cfg_scale: 1.5, top_k: 20, sampler: 'euler', scheduler: 'simple' } },
        defaults: { comfy_url: '', minimax: { steps: 30 } },
        limits: { steps: [1, 100], cfg: [0, 20], cfg_scale: [0, 10], top_k: [1, 200] },
        samplers: ['euler'], schedulers: ['simple'],
        library: { default: '/music', active: '/music', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/models': { success: true, providers: [], provider: 'nous', model: 'm' },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5', visibility: 'private',
        build: { version: '0.2.5' },
        revamp: { tagline: '', install: '', requires: '', state: 'absent' },
        paths: { tests: 'pytest', contribute: '', issues: '' } },
      '/api/about/diagnostics': { success: true, text: '' },
      '/api/update': { ok: true, installed: {}, latest: null, update_available: false, detail: '' },
      '/api/update/job': { state: 'idle' },
      '/api/studio/setup/cloud': { success: true, endpoints: [] },
      '/api/studio/setup/hf': { success: true, status: { running: false, detail: 'idle' } },
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
  await new Promise(resolve => setTimeout(resolve, 40));
  return { dom, window, calls };
}

const text = node => (node && node.textContent) || '';

test('Create offers one demo control that plays the shipped demo in the footer player', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const button = doc.getElementById('play-sample');
  assert.ok(button, 'the demo control exists on Create');
  assert.equal(text(button), 'Hear a demo song');

  button.click();
  await new Promise(resolve => setTimeout(resolve, 30));

  assert.ok(calls.some(call => call.path === '/api/demo'), 'the facts come from /api/demo');
  const audio = doc.getElementById('player');
  assert.match(audio.src, /\/api\/demo\/audio$/);
  assert.equal(audio.__played, true, 'the footer player starts it');
  assert.equal(text(doc.getElementById('player-title')), 'Afterglow');
  assert.match(text(doc.getElementById('player-subtitle')), /close-mic vocal/);
  assert.match(text(doc.getElementById('player-subtitle')), /3\/3/);
  assert.match(text(doc.getElementById('player-subtitle')), /-4\.1 dBFS/);
  assert.match(text(doc.getElementById('demo-facts')), /words recovered 3\/3/);
});

test('the demo audio URL carries the studio token, like every take URL', async t => {
  const { window } = await boot({}, { token: 'tok-demo123' });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('play-sample').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  const audio = doc.getElementById('player');
  assert.match(audio.src, /\/api\/demo\/audio\?token=tok-demo123$/, 'the media element cannot send the header');
});

test('an empty library explains the demo and points at Compute, with one play control', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-library').click();
  await new Promise(resolve => setTimeout(resolve, 30));

  const empty = doc.querySelector('.library-empty');
  assert.ok(empty, 'the empty library has its own state');
  const sentence = text(empty);
  assert.match(sentence, /real take made with this studio/);
  assert.match(sentence, /set up an engine/);
  assert.match(sentence, /Compute/);

  const demo = [...empty.querySelectorAll('button')].find(button => text(button) === 'Hear a demo song');
  assert.ok(demo, 'the empty state offers the demo');
  demo.click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.match(doc.getElementById('player').src, /\/api\/demo\/audio$/);
  assert.equal(text(doc.getElementById('player-title')), 'Afterglow');

  const compute = [...empty.querySelectorAll('button')].find(button => text(button) === 'Compute');
  compute.click();
  assert.equal(doc.getElementById('library-view').hidden, true);
  assert.equal(doc.getElementById('compute-view').hidden, false);
});
