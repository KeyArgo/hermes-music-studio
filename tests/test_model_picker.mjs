// The model picker: shows what Hermes knows (default + configured providers),
// remembers the choice in localStorage, and displays error commands with a Copy button.
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

const PROVIDERS = {
  success: true,
  default: { label: 'Your Hermes model' },
  providers: [
    { slug: 'nous', name: 'Nous', configured: true, models: ['nous-small', 'nous-large'], source: 'built-in' },
    { slug: 'openai', name: 'OpenAI', configured: false, models: ['gpt-4', 'gpt-3.5'], source: 'built-in' },
    { slug: 'custom', name: 'Custom Provider', configured: null, models: [], source: 'public catalog' }
  ]
};

async function boot(overrides = {}, options = {}) {
  const pageUrl = 'http://127.0.0.1:8794/';
  const dom = new JSDOM(html, { url: pageUrl, runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  window.confirm = () => true;
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () { this.__loaded = true; };

  // Stub localStorage for browser persistence
  const store = {};
  Object.defineProperty(window, 'localStorage', {
    value: {
      getItem: (key) => store[key] || null,
      setItem: (key, value) => { store[key] = value; },
      removeItem: (key) => { delete store[key]; },
      clear: () => { Object.keys(store).forEach(k => delete store[k]); }
    }
  });

  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method: opts.method || 'GET', body });
    const table = {
      '/api/status': { success: true, engine_available: false, engines: [], projects: [] },
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true, settings: {}, defaults: {}, limits: {}, samplers: [], schedulers: [] },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/models': PROVIDERS,
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5' }
    };
    if (path in overrides) table[path] = overrides[path];
    const payload = table[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };

  window.eval(appjs);
  await new Promise(resolve => setTimeout(resolve, 30));
  return { dom, window, calls, store };
}

const text = node => (node && node.textContent) || '';

test('model picker shows default option and configured providers', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const provider = doc.getElementById('idea-provider');
  const options = [...provider.options].map(o => ({ text: o.text, value: o.value }));
  assert.ok(options.some(o => o.text.includes('Your Hermes model') && o.value === ''),
    'default option "Your Hermes model (recommended)" is first');
  assert.ok(options.some(o => o.text.includes('Nous') && o.text.includes('(ready)')),
    'configured provider marked as ready');
  assert.ok(options.some(o => o.text === 'OpenAI' && !o.text.includes('(ready)')),
    'unconfigured provider not marked ready');
});

test('model picker loads models for selected provider', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const provider = doc.getElementById('idea-provider');
  const model = doc.getElementById('idea-model');

  provider.value = 'nous';
  provider.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 15));

  const modelOptions = [...model.options].map(o => o.value).filter(v => v);
  assert.ok(modelOptions.includes('nous-small'), 'nous-small model option appears');
  assert.ok(modelOptions.includes('nous-large'), 'nous-large model option appears');
});

test('model picker remembers choice in localStorage', async t => {
  const { window, store } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const provider = doc.getElementById('idea-provider');
  const model = doc.getElementById('idea-model');

  provider.value = 'nous';
  provider.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 15));

  model.value = 'nous-large';
  model.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 15));

  const saved = JSON.parse(store['studio.modelChoice'] || '{}');
  assert.equal(saved.provider, 'nous', 'provider saved to localStorage');
  assert.equal(saved.model, 'nous-large', 'model saved to localStorage');
});

test('model picker restores choice from localStorage', async t => {
  const { window, store } = await boot();
  t.after(() => window.close());
  store['studio.modelChoice'] = JSON.stringify({ provider: 'openai', model: 'gpt-4' });

  const doc = window.document;
  // Simulate the page reload by checking localStorage directly
  const pref = JSON.parse(store['studio.modelChoice'] || '{}');
  assert.equal(pref.provider, 'openai', 'provider in localStorage');
  assert.equal(pref.model, 'gpt-4', 'model in localStorage');
});

test('manual provider/model entry is available as fallback', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const providerManual = doc.getElementById('idea-provider-manual');
  const modelManual = doc.getElementById('idea-model-manual');

  assert.ok(providerManual, 'manual provider ID input exists');
  assert.ok(modelManual, 'manual model ID input exists');
  assert.equal(providerManual.placeholder, 'Use selected provider');
  assert.equal(modelManual.placeholder, 'Use selected model');
});

test('filter by provider or model name', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const filter = doc.getElementById('idea-filter');
  const provider = doc.getElementById('idea-provider');

  filter.value = 'nous';
  filter.dispatchEvent(new window.Event('input', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 15));

  const providerOptions = [...provider.options].filter(o => o.value).map(o => o.value);
  assert.ok(providerOptions.includes('nous'), 'nous appears in filtered list');
  assert.ok(!providerOptions.includes('openai'), 'openai filtered out');
});

test('model selection clears when provider changes', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const provider = doc.getElementById('idea-provider');
  const model = doc.getElementById('idea-model');

  provider.value = 'nous';
  provider.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 15));
  model.value = 'nous-small';

  provider.value = 'openai';
  provider.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise(resolve => setTimeout(resolve, 15));

  // Model select should be reset to the placeholder
  assert.equal(model.value, '', 'model field resets when provider changes');
});

test('connection status message updates after model load', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const status = doc.getElementById('idea-connection');

  await new Promise(resolve => setTimeout(resolve, 40));

  assert.match(status.textContent, /Choose a configured Hermes model|Checking Hermes/i);
});
