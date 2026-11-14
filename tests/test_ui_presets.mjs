// Draft presets on the Create page: choosing one fills only the fields it carries,
// saving asks for a name inline, and a built-in preset has no delete control.
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

const STATUS = { success: true, engine_available: false, engines: [], projects: [] };
const PRESETS = [
  { name: 'Warm ballad', preset_id: 'Warm ballad', caption: 'warm piano ballad',
    bpm: 84, keyscale: 'C major', vocal_language: 'fr', instrumental: false },
  { name: 'Bedroom sketch', preset_id: 'Bedroom sketch', caption: 'lo-fi bedroom pop' },
  { name: 'Built-in', preset_id: 'Built-in', caption: 'a house preset', bpm: 100,
    keyscale: 'D minor', vocal_language: 'en', instrumental: true, builtin: true },
];

async function boot(overrides = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
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
      '/api/status': STATUS,
      '/api/presets': { success: true, presets: PRESETS },
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
      '/api/about': { success: true, version: '0.2.5', visibility: 'private',
        build: { version: '0.2.5' },
        revamp: { tagline: '', install: '', requires: '', state: 'absent' },
        paths: { tests: 'pytest', contribute: '', issues: '' } },
      '/api/update': { ok: true, installed: {}, latest: null, update_available: false, detail: '' },
      '/api/update/job': { state: 'idle' },
    };
    let payload = table[path];
    if (path === '/api/presets' && options.method === 'POST') {
      payload = { success: true, presets: PRESETS.concat([{ ...body, preset_id: body.name }]) };
    }
    if (path.startsWith('/api/presets/') && options.method === 'DELETE') {
      const name = decodeURIComponent(path.slice('/api/presets/'.length));
      payload = { success: true, presets: PRESETS.filter(p => p.name !== name) };
    }
    if (path in overrides) payload = overrides[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await new Promise(resolve => setTimeout(resolve, 60));
  return { dom, window, calls };
}

const text = node => (node && node.textContent) || '';
const pause = (ms = 30) => new Promise(resolve => setTimeout(resolve, ms));
const choose = (window, doc, name) => {
  const select = doc.getElementById('preset-select');
  select.value = name;
  select.dispatchEvent(new window.Event('change'));
};

test('choosing a preset fills the fields it carries and leaves the rest alone', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.querySelector('input[name=title]').value = 'Keep this title';
  doc.querySelector('input[name=duration_seconds]').value = '77';

  choose(window, doc, 'Warm ballad');

  assert.equal(doc.querySelector('textarea[name=caption]').value, 'warm piano ballad');
  assert.equal(doc.querySelector('input[name=bpm]').value, '84');
  assert.equal(doc.querySelector('input[name=keyscale]').value, 'C major');
  assert.equal(doc.querySelector('select[name=vocal_language]').value, 'fr');
  assert.equal(doc.querySelector('input[name=instrumental]').checked, false);
  assert.equal(doc.querySelector('input[name=title]').value, 'Keep this title', 'the title is not a preset field');
  assert.equal(doc.querySelector('input[name=duration_seconds]').value, '77',
    'a field the preset does not carry is not wiped');
  assert.match(text(doc.getElementById('form-message')), /Applied preset/);
});

test('a preset with no tempo or key keeps the draft values already there', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.querySelector('input[name=bpm]').value = '132';
  doc.querySelector('input[name=keyscale]').value = 'A minor';
  doc.querySelector('select[name=vocal_language]').value = 'es';
  doc.querySelector('input[name=instrumental]').checked = true;

  choose(window, doc, 'Bedroom sketch');

  assert.equal(doc.querySelector('textarea[name=caption]').value, 'lo-fi bedroom pop');
  assert.equal(doc.querySelector('input[name=bpm]').value, '132');
  assert.equal(doc.querySelector('input[name=keyscale]').value, 'A minor');
  assert.equal(doc.querySelector('select[name=vocal_language]').value, 'es');
  assert.equal(doc.querySelector('input[name=instrumental]').checked, true);
});

test('Save as preset asks for a name inline and posts the current draft', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  window.prompt = () => { throw new Error('typed dialog opened'); };
  doc.querySelector('textarea[name=caption]').value = 'slow soul, close vocal';
  doc.querySelector('input[name=bpm]').value = '90';
  doc.querySelector('input[name=keyscale]').value = 'F major';
  doc.querySelector('select[name=vocal_language]').value = 'es';
  doc.querySelector('input[name=instrumental]').checked = true;

  doc.getElementById('preset-save').click();
  const row = doc.getElementById('preset-save-row');
  assert.equal(row.hidden, false, 'the name field opens in place');
  assert.ok(row.querySelector('#preset-name'), 'there is a real input, not a prompt');
  doc.getElementById('preset-name').value = 'Sunday soul';
  doc.getElementById('preset-save-confirm').click();
  await pause();

  const posted = calls.filter(c => c.path === '/api/presets' && c.method === 'POST').pop();
  assert.ok(posted, 'the preset is saved');
  assert.deepEqual(posted.body, { name: 'Sunday soul', caption: 'slow soul, close vocal',
    bpm: 90, keyscale: 'F major', vocal_language: 'es', instrumental: true });
  assert.equal(row.hidden, true);
  assert.equal(doc.getElementById('preset-select').value, 'Sunday soul', 'the new preset is selected');
  assert.match(text(doc.getElementById('form-message')), /Saved preset/);
});

test('a user preset has a delete control; a built-in preset does not', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const remove = doc.getElementById('preset-delete');

  choose(window, doc, 'Warm ballad');
  assert.equal(remove.hidden, false, 'a user preset can be deleted');
  remove.click();
  await pause();
  const deleted = calls.filter(c => c.path.startsWith('/api/presets/') && c.method === 'DELETE').pop();
  assert.equal(deleted.path, '/api/presets/Warm%20ballad');
  assert.match(text(doc.getElementById('form-message')), /Deleted preset/);

  choose(window, doc, 'Built-in');
  assert.equal(remove.hidden, true, 'a built-in preset never offers delete');
  const before = calls.filter(c => c.method === 'DELETE').length;
  remove.click();
  await pause();
  assert.equal(calls.filter(c => c.method === 'DELETE').length, before,
    'clicking it cannot delete a built-in');
  assert.match(text(doc.getElementById('form-message')), /Built-in presets cannot be deleted/);
});
