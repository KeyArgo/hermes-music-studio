// Music engines page UI tests for adding any reachable ComfyUI server.
// Runs app.js in jsdom with a fake fetch, copying the harness from test_engines_ui.mjs.
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

const pause = (ms = 50) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => (node && node.textContent) || '';

const COMFY_URL = 'http://192.168.1.50:8188';

const ACE_READY = {
  id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', available: true,
  kind: 'local', selectable_reason: null,
};

function project() {
  return {
    project_id: 'p1', title: 'First song', caption: 'quiet', lyrics: 'hello',
    engine: 'ace_local', approved: false, updated_at: 10, takes: [],
  };
}

async function boot(extra = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = {
    workflows: extra.workflows ? extra.workflows.slice() : [],
    engines: extra.engines ? extra.engines.slice() : [],
    hardware: {
      success: true,
      available: true,
      devices: [{ index: 0, name: 'NVIDIA GeForce RTX', memory_used_mb: 512, memory_total_mb: 8192 }],
      processes: [],
      pid_to_gpu: {},
      error: null,
    },
    testReply: null,
    failAdd: '',
  };
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });

    if (path === '/api/studio/hardware') {
      return { ok: true, status: 200, json: async () => state.hardware };
    }
    if (path === '/api/studio/engines/test' && method === 'POST') {
      const reply = state.testReply || { ok: false, message: 'Not tested.', problem: 'no_answer', models: [] };
      return { ok: true, status: 200, json: async () => ({ success: true, ...reply }) };
    }
    if (path === '/api/studio/engines' && method === 'POST') {
      if (state.failAdd) {
        return { ok: false, status: 400, json: async () => ({ success: false, error: state.failAdd }) };
      }
      const models = body && Array.isArray(body.models) ? body.models : [];
      const isConnectOnly = models.length === 0;
      const made = models.indexOf('ace15_turbo') !== -1
        ? [{ id: 'wf-ace', name: 'ACE-Step 1.5', engine_id: 'comfy_workflow:wf-ace' }]
        : [];
      if (made.length) {
        state.workflows = [{ id: 'wf-ace', name: 'ACE-Step 1.5', target_id: 'tgt-1', placeholders: [] }];
      }
      state.engines = [{
        id: 'tgt-1', kind: 'comfyui', name: body && body.name, url: body && body.url, status: 'ready',
        workflows: made,
      }];
      return { ok: true, status: 200, json: async () => ({
        success: true, ok: true,
        target: { id: 'tgt-1', name: body && body.name, url: body && body.url },
        workflows: state.workflows,
        ...(isConnectOnly ? { connect_only: true } : {}),
      }) };
    }
    if (path === '/api/studio/engines' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, engines: (state.engines || []).slice() }) };
    }
    if (path === '/api/studio/workflows' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, workflows: (state.workflows || []).slice() }) };
    }
    const settings = {
      success: true, settings: { minimax: {} },
      samplers: ['euler'], schedulers: ['simple'],
      library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 },
    };
    const table = {
      '/api/status': { success: true, engines: [ACE_READY], projects: [project()] },
      '/api/presets': { success: true, presets: [] },
      '/api/settings': settings,
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [{ id: 'default', name: 'My workspace', project_ids: [] }] },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/studio/engines': { success: true, engines: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/assign': { success: true },
    };
    const payload = table[path];
    return {
      ok: payload !== undefined,
      status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' },
    };
  };
  if (!window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', {
      value: array => { array[0] = 7; return array; }, configurable: true,
    });
  }
  window.eval(appjs);
  await pause();
  return { window, calls, state };
}

function callsTo(calls, path, method) {
  return calls.filter(c => c.path === path && c.method === method);
}

function setValue(doc, id, value) {
  const field = doc.getElementById(id);
  assert.ok(field, id);
  field.value = value;
  field.dispatchEvent(new doc.defaultView.Event('input', { bubbles: true }));
  return field;
}

async function openServer(booted) {
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(80);
  doc.getElementById('add-server-btn').click();
  return doc;
}

async function clickId(doc, id) {
  doc.getElementById(id).click();
  await pause(80);
}

test('Add turns on for a ComfyUI test with no known model and shows connect-only message', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  const add = doc.getElementById('add-engine-btn');
  assert.equal(add.disabled, true);

  setValue(doc, 'server-name', 'Any Comfy Box');
  setValue(doc, 'server-url', COMFY_URL);

  // Failed test: Add remains disabled.
  booted.state.testReply = {
    ok: false,
    message: 'Nothing answered at that address.',
    problem: 'no_answer',
    models: [],
  };
  await clickId(doc, 'test-server-btn');
  assert.equal(add.disabled, true);
  assert.equal(doc.getElementById('troubleshooting').hidden, false);
  assert.match(text(doc.getElementById('troubleshooting')), /No answer/);
  assert.match(text(doc.getElementById('test-result')), /Nothing answered at that address/);

  // Successful ComfyUI test with no known music models: Add button turns on.
  booted.state.testReply = {
    ok: true,
    kind: 'comfyui',
    models: [],
  };
  await clickId(doc, 'test-server-btn');
  assert.equal(add.disabled, false);
  const result = doc.getElementById('test-result');
  assert.equal(result.hidden, false);
  assert.equal(doc.getElementById('troubleshooting').hidden, true);
  const expectedText = 'Connected to ComfyUI. No music model this studio knows was found. You can still add it, then upload your own workflow under Custom workflows.';
  assert.match(text(result), new RegExp(expectedText.replace(/\./g, '\\.')));

  // Editing address disables Add again and clears result.
  setValue(doc, 'server-url', COMFY_URL + '/x');
  assert.equal(add.disabled, true);
  assert.equal(doc.getElementById('test-result').hidden, true);
  assert.equal(text(doc.getElementById('test-result')), '');

  // Restoring address and testing enables Add again.
  setValue(doc, 'server-url', COMFY_URL);
  assert.equal(add.disabled, true);
  await clickId(doc, 'test-server-btn');
  assert.equal(add.disabled, false);
  assert.match(text(doc.getElementById('test-result')), new RegExp(expectedText.replace(/\./g, '\\.')));

  // Clicking Add saves the engine with empty models and displays the connect-only success message.
  await clickId(doc, 'add-engine-btn');
  const posts = callsTo(booted.calls, '/api/studio/engines', 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, {
    kind: 'comfyui',
    name: 'Any Comfy Box',
    url: COMFY_URL,
    models: [],
  });
  assert.equal(doc.getElementById('server-wizard').hidden, true);

  const engineMsgNode = doc.getElementById('engine-message');
  const serverMsgNode = doc.getElementById('server-message');
  const addSuccessText = 'Added. Now upload a workflow for it under Custom workflows below.';
  const displayed = text(engineMsgNode) || text(serverMsgNode);
  assert.match(displayed, new RegExp(addSuccessText.replace(/\./g, '\\.')));
});

test('a plain ComfyUI is listed as connected, and the workflow menu offers it right away', async t => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../standalone/static/app.js', import.meta.url), 'utf8');
  const fn = src.slice(src.indexOf('function rowStatusFrom'), src.indexOf('async function checkEngineRow'));
  const rowStatusFrom = new Function(fn + '; return rowStatusFrom;')();
  const missing = { status: 'missing_nodes' };
  assert.equal(rowStatusFrom({ ok: true, models: [missing, missing] }), 'connected');
  assert.equal(rowStatusFrom({ ok: true, models: [{ status: 'missing_files' }, missing] }), 'missing_files');
  assert.equal(rowStatusFrom({ ok: true, models: [{ status: 'ready' }, missing] }), 'ready');
  const finish = src.slice(src.indexOf('async function finishEngineAdd'), src.indexOf('async function addServerEngine'));
  assert.match(finish, /refreshTargets\(\)/);
});
