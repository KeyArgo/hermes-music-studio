// Round 9 (lane ds-docfix) - Music engines page: a failed poll must hide Cancel.
// Same harness as test_engines_ui.mjs: app.js runs in jsdom against a fake fetch.
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

const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms));
const SUGGEST_DIR = 'C:/Users/me/Documents/ComfyUI/models';

async function boot() {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = {
    // The plan has room, so the download button is live.
    plan: {
      ok: true,
      enough_space: true,
      files: [
        { name: 'acestep_v1.5_turbo.safetensors', folder: 'diffusion_models', size: 4800000000, present: false },
        { name: 'ace_1.5_vae.safetensors', folder: 'vae', size: 300000000, present: false },
      ],
      free_bytes: 20000000000,
      message: 'Ready to download.',
    },
    // Once true, the poll for the running job answers 500 instead of a status.
    failPoll: false,
  };
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    calls.push({ path, method });
    if (path === '/api/studio/install/suggest') {
      return { ok: true, status: 200, json: async () => ({ models_dir: SUGGEST_DIR }) };
    }
    if (path === '/api/studio/install/plan' && method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ success: true, ...state.plan }) };
    }
    if (path === '/api/studio/install/jobs' && method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ success: true, job_id: 'job-1' }) };
    }
    if (path === '/api/studio/install/jobs/job-1' && method === 'GET') {
      if (state.failPoll) {
        return { ok: false, status: 500, json: async () => ({ success: false, error: 'The host stopped answering.' }) };
      }
      return { ok: true, status: 200, json: async () => ({ success: true, state: 'running',
        file: 'acestep_v1.5_turbo.safetensors', done_bytes: 1200000000, total_bytes: 4800000000,
        files_done: 1, files_total: 2, message: 'Downloading the music files', error: '' }) };
    }
    if (path === '/api/studio/hardware') {
      return { ok: true, status: 200, json: async () => ({ success: true, available: false, devices: [], processes: [], pid_to_gpu: {} }) };
    }
    const table = {
      '/api/status': { success: true, engines: [], projects: [] },
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true, settings: { minimax: {} }, samplers: [], schedulers: [],
        library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/studio/engines': { success: true, engines: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
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

async function openInstall(booted) {
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(80);
  doc.getElementById('add-local-btn').click();
  await pause(80);
  return doc;
}

test('a failed install poll shows Resume and hides Cancel', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openInstall(booted);
  doc.getElementById('check-space-btn').click();
  await pause(80);
  doc.getElementById('download-ace-btn').click();
  await pause(100);
  assert.equal(doc.getElementById('cancel-download-btn').hidden, false);

  booted.state.failPoll = true;
  await pause(1200);

  const message = doc.getElementById('install-message');
  assert.match(message.textContent, /The host stopped answering/);
  assert.equal(doc.getElementById('resume-download-btn').hidden, false);
  assert.equal(doc.getElementById('cancel-download-btn').hidden, true);
});
