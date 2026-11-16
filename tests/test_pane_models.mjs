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
  const paneMessages = [];
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

  if (options.pane) {
    // The page is framed: window.parent is the Hermes pane, which answers by posting back.
    const pane = { postMessage: (data) => {
      paneMessages.push(data);
      const reply = options.pane(data);
      if (reply) setTimeout(() => window.dispatchEvent(new window.MessageEvent('message', { data: reply })), 0);
    } };
    Object.defineProperty(window, 'parent', { value: pane, configurable: true });
  }
  window.eval(appjs);
  await new Promise(resolve => setTimeout(resolve, 30));
  return { dom, window, calls, store, paneMessages };
}


const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const PANE_PROVIDERS = { default: { label: 'Your Hermes model' }, providers: [
  { slug: 'opencode-go', name: 'OpenCode Go', configured: true, models: ['deepseek-v4.1-flash'], source: 'built-in list' } ] };

test('framed: the model list comes from the pane (Hermes process), not the host', async t => {
  const { window, calls, paneMessages } = await boot({ '/api/studio/models': { default: { label: 'Your Hermes model' }, providers: [] } }, {
    pane: data => data.type === 'hermes-music-studio:models'
      ? { type: 'hermes-music-studio:models-result', id: data.id, ok: true, choices: PANE_PROVIDERS } : null });
  t.after(() => window.close());
  await wait(40);
  assert.ok(paneMessages.some(m => m.type === 'hermes-music-studio:models'), 'the page asked the pane');
  const options = [...window.document.getElementById('idea-provider').options].map(o => o.text);
  assert.ok(options.some(o => o.includes('OpenCode Go') && o.includes('(ready)')), options.join(' | '));
  assert.ok(!calls.some(c => c.path === '/api/studio/models'), 'the host was not needed');
});

test('a chosen provider/model travels with the draft request through the pane', async t => {
  const { window, calls, paneMessages } = await boot({}, {
    pane: data => {
      if (data.type === 'hermes-music-studio:models') return { type: 'hermes-music-studio:models-result', id: data.id, ok: true, choices: PANE_PROVIDERS };
      if (data.type === 'hermes-music-studio:draft') return { type: 'hermes-music-studio:draft-result', id: data.id, ok: true, draft: { caption: 'warm synths', lyrics: 'la' } };
      return null;
    } });
  t.after(() => window.close());
  await wait(40);
  const doc = window.document;
  doc.getElementById('idea-provider').value = 'opencode-go';
  doc.getElementById('idea-provider').dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(10);
  doc.getElementById('idea-model').value = 'deepseek-v4.1-flash';
  doc.getElementById('idea-input').value = 'a late night drive';
  doc.getElementById('idea-apply').click();
  await wait(60);
  const draft = paneMessages.find(m => m.type === 'hermes-music-studio:draft');
  assert.ok(draft, 'the draft went to the pane');
  assert.equal(draft.provider, 'opencode-go');
  assert.equal(draft.model, 'deepseek-v4.1-flash');
  assert.ok(!calls.some(c => c.path === '/api/studio/ideas'), 'never the host, which has no model');
});

test('a refused model override shows the two commands from the pane', async t => {
  const commands = ['hermes config set plugins.entries.hermes-music-studio.llm.allow_provider_override true',
    'hermes config set plugins.entries.hermes-music-studio.llm.allow_model_override true'];
  const { window } = await boot({}, {
    pane: data => {
      if (data.type === 'hermes-music-studio:models') return { type: 'hermes-music-studio:models-result', id: data.id, ok: true, choices: PANE_PROVIDERS };
      if (data.type === 'hermes-music-studio:draft') return { type: 'hermes-music-studio:draft-result', id: data.id, ok: false, error: 'Hermes has not allowed this plugin to choose a model', commands };
      return null;
    } });
  t.after(() => window.close());
  await wait(40);
  const doc = window.document;
  doc.getElementById('idea-provider').value = 'opencode-go';
  doc.getElementById('idea-provider').dispatchEvent(new window.Event('change', { bubbles: true }));
  await wait(10);
  doc.getElementById('idea-model').value = 'deepseek-v4.1-flash';
  doc.getElementById('idea-input').value = 'a late night drive';
  doc.getElementById('idea-apply').click();
  await wait(60);
  const box = doc.querySelector('.error-message');
  assert.ok(box, 'an error box with commands is shown');
  assert.match(box.textContent, /allow_provider_override true/);
  assert.match(box.textContent, /allow_model_override true/);
  assert.ok(box.querySelector('.copy-cmd'), 'with a Copy button');
});
