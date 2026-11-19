// Round 13, fix 1: the model picker starts on "Your Hermes model".
//
// The owner opened the studio with nothing saved and the Provider menu already said "OpenCode Go".
// Picking a provider is a *provider override*, and Hermes refuses an override it has not been told
// to allow — so the very first draft failed for a choice nobody made. The cause was the picker's
// "only one provider, so that must be the choice" fallback: a Hermes with one connected provider is
// the normal case, not a choice.
//
// Promised here: nothing saved -> nothing chosen (and the draft carries no provider/model); a saved
// provider that is gone -> nothing chosen; a saved choice that still exists -> restored, override and
// all.
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

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

// What the owner's Hermes lists: the one provider their account is connected to. One provider is
// the normal case, and it is exactly the case the old picker read as "the person chose it".
const ONE_PROVIDER = {
  default: { label: 'Your Hermes model' },
  providers: [
    { slug: 'opencode-go', name: 'OpenCode Go', configured: true,
      models: ['deepseek-v4.1-flash', 'qwen-35b'], source: 'built-in list' }
  ]
};

async function boot({ saved = null, choices = ONE_PROVIDER } = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
  const { window } = dom;
  const store = {};
  if (saved) store['studio.modelChoice'] = JSON.stringify(saved);
  Object.defineProperty(window, 'localStorage', {
    value: {
      getItem: key => store[key] || null,
      setItem: (key, value) => { store[key] = value; },
      removeItem: key => { delete store[key]; },
      clear: () => { Object.keys(store).forEach(key => delete store[key]); }
    }
  });
  window.confirm = () => true;
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async url => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const table = {
      '/api/status': { success: true, engines: [], projects: [] },
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true, settings: {}, defaults: {}, limits: {}, samplers: [], schedulers: [] },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5' },
      '/api/studio/models': { success: true, providers: [] }
    };
    const payload = table[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  // Framed by the Hermes pane, as the studio really is: the pane answers the model list and the draft.
  const sent = [];
  const pane = {
    postMessage: data => {
      sent.push(data);
      let reply = null;
      if (data.type === 'hermes-music-studio:models') {
        reply = { type: 'hermes-music-studio:models-result', id: data.id, ok: true, choices };
      } else if (data.type === 'hermes-music-studio:draft') {
        reply = { type: 'hermes-music-studio:draft-result', id: data.id, ok: true,
          draft: { caption: 'warm synths' } };
      }
      if (reply) window.dispatchEvent(new window.MessageEvent('message', { data: reply }));
    }
  };
  Object.defineProperty(window, 'parent', { value: pane, configurable: true });
  window.eval(appjs);
  await wait(60);
  return { window, doc: window.document, sent, store };
}

const picker = booted => ({
  provider: booted.doc.getElementById('idea-provider'),
  model: booted.doc.getElementById('idea-model')
});
const chosenLabel = select => (select.selectedOptions[0] || {}).textContent || '';

async function draftRequest(booted) {
  booted.doc.getElementById('idea-input').value = 'a night drive, warm synths';
  booted.doc.getElementById('idea-apply').click();
  await wait(60);
  return booted.sent.find(m => m.type === 'hermes-music-studio:draft');
}

test('with nothing saved the picker starts on your Hermes model', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const { provider, model } = picker(booted);

  assert.equal(provider.value, '',
    `the provider menu started on "${provider.value}" (${chosenLabel(provider)}) with nothing saved`);
  assert.match(chosenLabel(provider), /Your Hermes model/,
    'the first line of the menu names the Hermes model');
  assert.equal(model.value, '', `the model menu started on "${model.value}" with nothing saved`);

  // The real damage was the request: a provider nobody picked is an override Hermes may refuse.
  // The pane message leaves provider/model out entirely when neither was chosen, which is how "use
  // your Hermes model" is spelled on the wire.
  const request = await draftRequest(booted);
  assert.ok(request, 'the draft went to the pane');
  assert.equal(request.provider, undefined, 'no provider is sent unless the person picked one');
  assert.equal(request.model, undefined, 'and no model either');
});

test('a saved provider that is no longer listed does not come back', async t => {
  const booted = await boot({ saved: { provider: 'openai', model: 'gpt-4' } });
  t.after(() => booted.window.close());
  const { provider, model } = picker(booted);

  assert.equal(provider.value, '', `a stale saved provider came back as "${provider.value}"`);
  assert.equal(model.value, '');

  const request = await draftRequest(booted);
  assert.equal(request.provider, undefined, 'a provider this Hermes no longer has is never sent');
  assert.equal(request.model, undefined);
});

test('a saved model its provider no longer lists is not half restored', async t => {
  // The provider exists but the model is gone: restoring the pair as "provider, no model" would show
  // one in the menu while the draft silently sent neither. Falling back to the Hermes model is honest.
  const booted = await boot({ saved: { provider: 'opencode-go', model: 'retired-model' } });
  t.after(() => booted.window.close());
  const { provider, model } = picker(booted);

  assert.equal(provider.value, '', `the provider menu kept a half choice: "${provider.value}"`);
  assert.equal(model.value, '');
});

test('a saved choice that still exists is restored, override and all', async t => {
  const booted = await boot({ saved: { provider: 'opencode-go', model: 'deepseek-v4.1-flash' } });
  t.after(() => booted.window.close());
  const { provider, model } = picker(booted);

  assert.equal(provider.value, 'opencode-go', 'a saved provider that still exists is restored');
  assert.equal(model.value, 'deepseek-v4.1-flash', 'and the model the person saved');

  const request = await draftRequest(booted);
  assert.equal(request.provider, 'opencode-go');
  assert.equal(request.model, 'deepseek-v4.1-flash');
});

test('a person who picks a provider still saves it', async t => {
  // The fix must not make the menu a one-way street: choosing is what an override is for.
  const booted = await boot();
  t.after(() => booted.window.close());
  const { provider, model } = picker(booted);
  provider.value = 'opencode-go';
  provider.dispatchEvent(new booted.window.Event('change', { bubbles: true }));
  await wait(20);
  model.value = 'qwen-35b';
  model.dispatchEvent(new booted.window.Event('change', { bubbles: true }));
  await wait(20);

  const saved = JSON.parse(booted.store['studio.modelChoice'] || '{}');
  assert.equal(saved.provider, 'opencode-go');
  assert.equal(saved.model, 'qwen-35b');
});
