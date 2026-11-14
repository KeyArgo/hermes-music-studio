// Playing the shipped demo must ask for its waveform too, once, and title the take from
// the metadata that exists (the demo card has a caption but no title).
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
  caption: 'warm acoustic folk, fingerpicked guitar, close-mic vocal',
  word_recovery: '3/3',
  peak_dbfs: -4.11,
  audio_url: '/api/plugins/hermes-music-studio/demo/audio',
};
const DEMO_PEAKS = [
  { min: -0.2, max: 0.2, rms: 0.1 },
  { min: -0.6, max: 0.6, rms: 0.3 },
  { min: -0.3, max: 0.4, rms: 0.2 },
  { min: -0.1, max: 0.1, rms: 0.05 },
];

async function boot() {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
  const { window } = dom;
  window.confirm = () => true;
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () { this.__loaded = true; };
  window.HTMLCanvasElement.prototype.getContext = () => null;

  const calls = [];
  const peakCalls = [];
  window.fetch = async (url, options = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    calls.push({ path, method: options.method || 'GET' });
    if (path.startsWith('/api/demo/peaks')) {
      peakCalls.push(path);
      return { ok: true, json: async () => ({ success: true, buckets: 256,
        duration_seconds: 13.119, peaks: DEMO_PEAKS }) };
    }
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
    const payload = table[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await new Promise(resolve => setTimeout(resolve, 40));
  return { window, doc: window.document, calls, peakCalls };
}

const text = node => (node && node.textContent) || '';

test('playing the demo requests its peaks once and titles it from the caption', async t => {
  const { window, doc, peakCalls } = await boot();
  t.after(() => window.close());

  doc.getElementById('play-sample').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(peakCalls.length, 1, 'the demo waveform is requested');
  assert.match(peakCalls[0], /^\/api\/demo\/peaks\?buckets=\d+$/);
  assert.equal(doc.getElementById('player-wave').hidden, false, 'the strip appears');
  assert.equal(text(doc.getElementById('player-title')), 'Warm acoustic folk',
    'the title falls back to the caption, not "Demo song"');

  doc.getElementById('play-sample').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(peakCalls.length, 1, 'the demo peaks are reused under the demo key');
});
