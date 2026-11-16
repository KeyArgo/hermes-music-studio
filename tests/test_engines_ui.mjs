// Music engines page UI tests - check HTML structure and key elements.
// The install wizard tests below run app.js in jsdom with a fake fetch, same harness as test_generate_flow.mjs.
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
const css = await readFile(new URL('../standalone/static/style.css', import.meta.url), 'utf8');

test('engines page has nav-engines button with Music engines text', async t => {
  assert.match(html, /id="nav-engines"/);
  assert.match(html, />Music engines</);
});

test('engines page has engines-view section', async t => {
  assert.match(html, /id="engines-view"/);
});

test('engines page displays MUSIC ENGINES heading', async t => {
  assert.match(html, /MUSIC ENGINES/);
});

test('engines page has What is this toggle button', async t => {
  assert.match(html, /class="info-toggle"/);
});

test('engines page includes help text about audio engines', async t => {
  assert.match(html, /turns your words into audio/i);
});

test('engines page has Use a server button', async t => {
  assert.match(html, /id="add-server-btn"/);
});

test('engines page has Install on this computer button', async t => {
  assert.match(html, /id="add-local-btn"/);
});

test('server wizard has engine kind radio buttons', async t => {
  assert.match(html, /name="engine-kind"/);
  assert.match(html, /value="comfyui"/);
  assert.match(html, /value="ace_server"/);
  assert.match(html, /value="hosted"/);
});

test('server wizard has Name field', async t => {
  assert.match(html, /id="server-name"/);
});

test('server wizard has Address field', async t => {
  assert.match(html, /id="server-url"/);
});

test('server wizard has Test button', async t => {
  assert.match(html, /id="test-server-btn"/);
});

test('server wizard has Add button', async t => {
  assert.match(html, /id="add-engine-btn"/);
});

test('server wizard has Cancel button', async t => {
  assert.match(html, /id="cancel-server-btn"/);
});

test('install wizard has ComfyUI Desktop link button', async t => {
  assert.match(html, /id="open-comfyui-link"/);
});

test('install wizard has Look on this computer button', async t => {
  assert.match(html, /id="look-local-btn"/);
});

test('install wizard has Models folder field', async t => {
  assert.match(html, /id="models-dir"/);
});

test('install wizard has Check space button', async t => {
  assert.match(html, /id="check-space-btn"/);
});

test('install wizard has Cancel button', async t => {
  assert.match(html, /id="cancel-install-btn"/);
});

test('engine list container exists', async t => {
  assert.match(html, /id="engine-list"/);
});

test('close wizard buttons exist', async t => {
  assert.match(html, /class="close-wizard"/);
});

test('advanced options disclosure exists', async t => {
  assert.match(html, /class="advanced-options"/);
});

test('all form fields have title attributes for tooltips', async t => {
  assert.match(html, /id="server-name"[^>]*title=/);
  assert.match(html, /id="server-url"[^>]*title=/);
  assert.match(html, /id="models-dir"[^>]*title=/);
});

test('help icons with aria-describedby exist', async t => {
  assert.match(html, /aria-describedby="help-/);
  assert.match(html, /class="help-icon"/);
});

test('backwards compatible compute-view div exists', async t => {
  assert.match(html, /id="compute-view"/);
});

test('export folder field exists in advanced options', async t => {
  assert.match(html, /id="export-dir"/);
});

test('custom workflow field exists in advanced options', async t => {
  assert.match(html, /id="custom-workflow"/);
});

const pause = (ms = 50) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => (node && node.textContent) || '';

const ACE_READY = { id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', available: true,
  kind: 'local', selectable_reason: null };

const SUGGEST_DIR = 'C:/Users/me/Documents/ComfyUI/models';

function project() {
  return { project_id: 'p1', title: 'First song', caption: 'quiet', lyrics: 'hello',
    engine: 'ace_local', approved: false, updated_at: 10, takes: [] };
}

function installState() {
  return {
    suggest: { models_dir: SUGGEST_DIR },
    failPlan: '',
    plan: {
      ok: true,
      enough_space: false,
      files: [
        { name: 'acestep_v1.5_turbo.safetensors', folder: 'diffusion_models', size: 4800000000, present: false },
        { name: 'ace_1.5_vae.safetensors', folder: 'vae', size: 300000000, present: true },
      ],
      free_bytes: 2000000000,
      message: 'Not enough free space for these music files.',
    },
    job: {
      state: 'running',
      file: 'acestep_v1.5_turbo.safetensors',
      done_bytes: 1200000000,
      total_bytes: 4800000000,
      files_done: 1,
      files_total: 4,
      message: 'Downloading the music files',
      error: '',
    },
  };
}

async function boot(extra = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = {
    install: installState(),
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
    failAce: '',
    aceSoft: null,
    failCloud: '',
    exportText: 'Studio box http://192.168.1.50:8188',
    importResult: { added: ['Studio box'], skipped: ['Old box'], errors: [] },
    importError: '',
  };
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    const install = state.install;
    if (path === '/api/studio/install/suggest') {
      return { ok: true, status: 200, json: async () => install.suggest };
    }
    if (path === '/api/studio/install/plan' && method === 'POST') {
      if (install.failPlan) {
        return { ok: false, status: 400, json: async () => ({ success: false, error: install.failPlan }) };
      }
      return { ok: true, status: 200, json: async () => ({ success: true, ...install.plan }) };
    }
    if (path === '/api/studio/install/jobs' && method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ success: true, job_id: 'job-1' }) };
    }
    if (path === '/api/studio/install/jobs/job-1/cancel' && method === 'POST') {
      install.job.state = 'cancelled';
      install.job.message = 'Download cancelled.';
      return { ok: true, status: 200, json: async () => ({ success: true }) };
    }
    if (path === '/api/studio/install/jobs/job-1' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, ...install.job }) };
    }
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
      }) };
    }
    if (path === '/api/studio/engines' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, engines: (state.engines || []).slice() }) };
    }
    if (path === '/api/studio/workflows' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, workflows: (state.workflows || []).slice() }) };
    }
    if (path === '/api/studio/setup/ace' && method === 'POST') {
      if (state.failAce) {
        return { ok: false, status: 400, json: async () => ({ success: false, error: state.failAce }) };
      }
      if (state.aceSoft) {
        const soft = state.aceSoft;
        return { ok: true, status: 200, json: async () => ({ ...soft }) };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true, saved: true, url: body && body.url, detail: 'ACE server answers' }) };
    }
    if (path === '/api/studio/setup/cloud' && method === 'POST') {
      if (state.failCloud) {
        return { ok: false, status: 400, json: async () => ({ success: false, error: state.failCloud }) };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true, name: body && body.name, url: body && body.url, has_key: true }) };
    }
    if (path === '/api/studio/setup/cloud' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ success: true, endpoints: [] }) };
    }
    if (path === '/api/studio/engines/export' && method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ text: state.exportText }) };
    }
    if (path === '/api/studio/engines/import' && method === 'POST') {
      if (state.importError) {
        return { ok: false, status: 400, json: async () => ({ success: false, error: state.importError }) };
      }
      return { ok: true, status: 200, json: async () => state.importResult };
    }
    const settings = { success: true, settings: { minimax: {} },
      samplers: ['euler'], schedulers: ['simple'],
      library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } };
    const table = {
      '/api/status': { success: true, engines: [ACE_READY], projects: [project()] },
      '/api/presets': { success: true, presets: [] },
      '/api/settings': settings,
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
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

function jobPolls(calls) {
  return calls.filter(c => c.path === '/api/studio/install/jobs/job-1' && c.method === 'GET');
}

test('opening Install on this computer prefills the models folder from suggest', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openInstall(booted);
  const suggest = booted.calls.filter(c => c.path === '/api/studio/install/suggest');
  assert.equal(suggest.length, 1);
  assert.equal(suggest[0].method, 'GET');
  assert.equal(doc.getElementById('models-dir').value, SUGGEST_DIR);

  booted.state.install.suggest = 'D:/ComfyUI/models';
  doc.getElementById('add-local-btn').click();
  await pause(80);
  assert.equal(doc.getElementById('models-dir').value, 'D:/ComfyUI/models');

  booted.state.install.suggest = { models_dir: null };
  doc.getElementById('add-local-btn').click();
  await pause(80);
  assert.equal(doc.getElementById('models-dir').value, 'D:/ComfyUI/models');

  booted.state.install.suggest = null;
  doc.getElementById('add-local-btn').click();
  await pause(80);
  assert.equal(doc.getElementById('models-dir').value, 'D:/ComfyUI/models');

  for (const id of ['check-space-btn', 'download-ace-btn', 'cancel-download-btn', 'look-local-btn', 'resume-download-btn']) {
    const node = doc.getElementById(id);
    assert.ok(node.title, id + ' title');
    const helpId = node.getAttribute('aria-describedby');
    assert.ok(helpId, id + ' help');
    assert.ok(doc.getElementById(helpId), helpId);
  }
});

test('check space lists files, and Download stays off without enough room', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openInstall(booted);
  assert.equal(doc.getElementById('download-ace-btn').disabled, true);
  doc.getElementById('check-space-btn').click();
  await pause(80);

  const planCall = booted.calls.filter(c => c.path === '/api/studio/install/plan').pop();
  assert.equal(planCall.method, 'POST');
  assert.equal(planCall.body.models_dir, SUGGEST_DIR);
  const box = doc.getElementById('space-result');
  assert.equal(box.hidden, false);
  assert.match(text(box), /acestep_v1\.5_turbo\.safetensors/);
  assert.match(text(box), /4\.8 GB/);
  assert.match(text(box), /ace_1\.5_vae\.safetensors/);
  assert.match(text(box), /0\.3 GB/);
  assert.match(text(box), /already there/);
  assert.match(text(box), /Total to download/);
  assert.match(text(box), /Free space/);
  assert.match(text(box), /2\.0 GB/);
  assert.match(text(box), /Not enough free space for these music files/);
  assert.equal(box.querySelector('.message').classList.contains('error'), false);
  assert.equal(doc.getElementById('download-ace-btn').disabled, true);
  assert.equal(doc.getElementById('install-gpu').hidden, false);
  assert.equal(doc.getElementById('install-gpu').textContent, 'GPU 0 · NVIDIA GeForce RTX · 512 / 8192 MiB');

  booted.state.install.plan = {
    ok: false,
    enough_space: false,
    files: [],
    free_bytes: 0,
    message: 'Choose the folder ComfyUI calls models.',
  };
  doc.getElementById('check-space-btn').click();
  await pause(80);
  const note = doc.querySelector('#space-result .message');
  assert.equal(note.classList.contains('error'), true);
  assert.match(text(note), /Choose the folder ComfyUI calls models/);
  assert.equal(doc.getElementById('download-ace-btn').disabled, true);
});

test('a failed space check shows the server error in the wizard', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  booted.state.install.failPlan = 'Choose the folder ComfyUI calls models.';
  const doc = await openInstall(booted);
  doc.getElementById('check-space-btn').click();
  await pause(80);
  const message = doc.getElementById('install-message');
  assert.equal(message.classList.contains('error'), true);
  assert.match(text(message), /Choose the folder ComfyUI calls models/);
  assert.equal(doc.getElementById('download-ace-btn').disabled, true);
});

test('download progress polls until the job is done', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  booted.state.install.plan.enough_space = true;
  const doc = await openInstall(booted);
  doc.getElementById('check-space-btn').click();
  await pause(80);
  const download = doc.getElementById('download-ace-btn');
  assert.equal(download.disabled, false);
  assert.equal(download.textContent, 'Download ACE-Step 1.5 (14.7 GB)');
  download.click();
  await pause(100);

  const started = booted.calls.filter(c => c.path === '/api/studio/install/jobs' && c.method === 'POST').pop();
  assert.equal(started.body.models_dir, SUGGEST_DIR);
  assert.match(text(doc.getElementById('progress-text')), /acestep_v1\.5_turbo\.safetensors/);
  assert.match(text(doc.getElementById('progress-text')), /1 of 4/);
  assert.match(text(doc.getElementById('progress-text')), /Downloading the music files/);
  assert.equal(doc.getElementById('install-progress-bar').style.width, '25%');
  assert.equal(doc.getElementById('install-progress-track').getAttribute('aria-valuenow'), '25');
  assert.equal(jobPolls(booted.calls).length, 1);

  Object.assign(booted.state.install.job, {
    state: 'done', done_bytes: 4800000000, files_done: 4, message: 'All files are in place.',
  });
  await pause(2000);
  assert.equal(doc.getElementById('install-done').hidden, false);
  assert.match(text(doc.getElementById('install-done')), /Restart ComfyUI, then press Look on this computer/);
  assert.equal(jobPolls(booted.calls).length, 2);
  const settled = jobPolls(booted.calls).length;
  await pause(1500);
  assert.equal(jobPolls(booted.calls).length, settled);
});

test('cancel stops the download and offers Resume', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  booted.state.install.plan.enough_space = true;
  const doc = await openInstall(booted);
  doc.getElementById('check-space-btn').click();
  await pause(80);
  doc.getElementById('download-ace-btn').click();
  await pause(100);
  assert.match(text(doc.getElementById('progress-text')), /1 of 4/);

  doc.getElementById('cancel-download-btn').click();
  await pause(100);
  const resume = doc.getElementById('resume-download-btn');
  assert.equal(resume.hidden, false);
  assert.equal(resume.textContent, 'Resume download');
  assert.ok(booted.calls.some(c => c.path === '/api/studio/install/jobs/job-1/cancel' && c.method === 'POST'));
  assert.equal(booted.state.install.job.state, 'cancelled');

  resume.click();
  await pause(100);
  const posts = booted.calls.filter(c => c.path === '/api/studio/install/jobs' && c.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[1].body.models_dir, SUGGEST_DIR);
});

const COMFY_URL = 'http://192.168.1.50:8188';
const ACE_URL = 'http://192.168.1.40:8000';
const HOSTED_URL = 'https://api.example.com/v1';
const HOSTED_KEY = 'super-secret-key-9f3a';
const MINIMAX_MISSING = { id: 'minimax_m3', label: 'MiniMax Music 3', status: 'missing_files' };
const ACE_MODEL_MISSING = { id: 'ace15_turbo', label: 'ACE-Step 1.5', status: 'missing_files' };
const ACE_MODEL_READY = { id: 'ace15_turbo', label: 'ACE-Step 1.5', status: 'ready' };
const MINIMAX_READY = { id: 'minimax_m3', label: 'MiniMax Music 3', status: 'ready' };

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

function pickKind(doc, value) {
  const input = doc.querySelector('input[name="engine-kind"][value="' + value + '"]');
  assert.ok(input, value);
  input.checked = true;
  input.dispatchEvent(new doc.defaultView.Event('change', { bubbles: true }));
  return input;
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

test('Add stays off until the current kind and address test ready', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  const add = doc.getElementById('add-engine-btn');
  assert.equal(add.disabled, true);

  setValue(doc, 'server-name', 'Studio box');
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = {
    ok: false,
    message: 'Nothing answered at that address.',
    problem: 'no_answer',
    models: [ACE_MODEL_READY],
  };
  await clickId(doc, 'test-server-btn');
  assert.equal(add.disabled, true);
  assert.equal(doc.getElementById('troubleshooting').hidden, false);
  assert.match(text(doc.getElementById('troubleshooting')), /Firewall blocking/);
  assert.match(text(doc.getElementById('test-result')), /Nothing answered at that address/);

  booted.state.testReply = {
    ok: true,
    kind: 'comfyui',
    message: 'This is ComfyUI. Neither music model is ready.',
    models: [MINIMAX_MISSING, ACE_MODEL_MISSING],
  };
  await clickId(doc, 'test-server-btn');
  assert.equal(add.disabled, true);

  booted.state.testReply = {
    ok: true,
    kind: 'comfyui',
    message: 'This is ComfyUI. <b>ACE-Step 1.5</b> is ready.',
    models: [{ id: 'ace15_turbo', label: 'ACE <i>Step</i>', status: 'ready' }, MINIMAX_MISSING],
  };
  await clickId(doc, 'test-server-btn');
  const result = doc.getElementById('test-result');
  assert.equal(add.disabled, false);
  assert.match(text(result), /<b>ACE-Step 1\.5<\/b>/);
  assert.match(text(result), /ACE <i>Step<\/i>/);
  assert.equal(result.querySelector('b'), null);
  assert.equal(result.querySelector('i'), null);
  assert.deepEqual(callsTo(booted.calls, '/api/studio/engines/test', 'POST').pop().body, {
    kind: 'comfyui', url: COMFY_URL,
  });

  setValue(doc, 'server-url', COMFY_URL + '/x');
  assert.equal(add.disabled, true);
  assert.equal(doc.getElementById('test-result').hidden, true);
  assert.equal(text(doc.getElementById('test-result')), '');
  assert.equal(doc.getElementById('troubleshooting').hidden, true);

  setValue(doc, 'server-url', COMFY_URL);
  assert.equal(add.disabled, true);
  assert.equal(text(doc.getElementById('test-result')), '');

  await clickId(doc, 'test-server-btn');
  assert.equal(add.disabled, false);
  assert.deepEqual(callsTo(booted.calls, '/api/studio/engines/test', 'POST').pop().body, {
    kind: 'comfyui', url: COMFY_URL,
  });

  pickKind(doc, 'ace_server');
  assert.equal(add.disabled, true);
  assert.equal(doc.getElementById('test-result').hidden, true);
  assert.equal(text(doc.getElementById('test-result')), '');
  assert.equal(text(doc.getElementById('troubleshooting')), '');

  pickKind(doc, 'comfyui');
  assert.equal(add.disabled, true);
  assert.equal(doc.getElementById('test-server-btn').hidden, false);
});

test('ComfyUI Add posts only the models this test marked ready', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  setValue(doc, 'server-name', 'Studio box');
  setValue(doc, 'server-url', COMFY_URL);
  setValue(doc, 'server-key', HOSTED_KEY);
  booted.state.testReply = {
    ok: true,
    kind: 'comfyui',
    message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
    models: [{ id: 12, status: 'ready', label: 'not a string' },
      { ...ACE_MODEL_READY, resolved: { 'qwen_4b_ace15.safetensors': 'ace/qwen_4b_ace15.safetensors' } },
      { ...MINIMAX_MISSING, resolved: { 'x.safetensors': 'sub/x.safetensors' } }],
  };
  await clickId(doc, 'test-server-btn');
  assert.equal(doc.getElementById('add-engine-btn').disabled, false);
  await clickId(doc, 'add-engine-btn');

  let posts = callsTo(booted.calls, '/api/studio/engines', 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0].body, {
    kind: 'comfyui',
    name: 'Studio box',
    url: COMFY_URL,
    models: ['ace15_turbo'],
    // Exact loader names from the ready rows only, so subfolder installs render.
    resolved: { 'qwen_4b_ace15.safetensors': 'ace/qwen_4b_ace15.safetensors' },
  });
  assert.equal(JSON.stringify(posts[0].body).includes(HOSTED_KEY), false);
  assert.equal(doc.getElementById('server-wizard').hidden, true);

  doc.getElementById('add-server-btn').click();
  setValue(doc, 'server-name', 'Other box');
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = {
    ok: true,
    kind: 'comfyui',
    message: 'This is ComfyUI. MiniMax Music 3 is ready.',
    models: [MINIMAX_READY, ACE_MODEL_MISSING],
  };
  await clickId(doc, 'test-server-btn');
  await clickId(doc, 'add-engine-btn');
  posts = callsTo(booted.calls, '/api/studio/engines', 'POST');
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[1].body, {
    kind: 'comfyui',
    name: 'Other box',
    url: COMFY_URL,
    models: ['minimax_m3'],
  });
});

test('ACE-Step server is tested as ace_server and saved with setup/ace', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  pickKind(doc, 'ace_server');
  setValue(doc, 'server-name', 'ACE box');
  setValue(doc, 'server-url', ACE_URL);

  booted.state.testReply = {
    ok: false,
    kind: 'ace_server',
    message: 'That address is not an ACE-Step server.',
    problem: 'not_ace',
    models: [],
  };
  await clickId(doc, 'test-server-btn');
  assert.equal(doc.getElementById('add-engine-btn').disabled, true);
  assert.match(text(doc.getElementById('troubleshooting')), /Not an ACE server/);

  booted.state.testReply = {
    ok: true,
    kind: 'ace_server',
    message: 'This is an ACE-Step server.',
    models: [],
  };
  await clickId(doc, 'test-server-btn');
  assert.equal(doc.getElementById('add-engine-btn').disabled, false);
  assert.match(text(doc.getElementById('test-result')), /This is an ACE-Step server\./);
  assert.deepEqual(callsTo(booted.calls, '/api/studio/engines/test', 'POST').map(c => c.body), [
    { kind: 'ace_server', url: ACE_URL },
    { kind: 'ace_server', url: ACE_URL },
  ]);

  booted.state.aceSoft = { ok: false, detail: 'nothing answered at the address' };
  await clickId(doc, 'add-engine-btn');
  assert.equal(doc.getElementById('server-wizard').hidden, false);
  const message = doc.getElementById('server-message');
  assert.ok(message);
  assert.equal(message.classList.contains('error'), true);
  assert.equal(message.textContent, 'nothing answered at the address');
  assert.equal(message.querySelector('img'), null);
  assert.equal(callsTo(booted.calls, '/api/studio/engines', 'POST').length, 0);

  booted.state.aceSoft = null;
  await clickId(doc, 'add-engine-btn');
  assert.equal(doc.getElementById('server-wizard').hidden, true);
  assert.deepEqual(callsTo(booted.calls, '/api/studio/setup/ace', 'POST').map(c => c.body), [
    { url: ACE_URL },
    { url: ACE_URL },
  ]);
  assert.equal(callsTo(booted.calls, '/api/studio/engines', 'POST').length, 0);
});

test('a hosted service posts the key only to setup/cloud and then clears it', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  pickKind(doc, 'hosted');
  assert.equal(doc.getElementById('test-server-btn').hidden, true);
  assert.equal(doc.getElementById('add-engine-btn').disabled, false);
  assert.equal(doc.getElementById('api-key-label').hidden, false);
  const modelLabel = doc.getElementById('server-model-label');
  assert.ok(modelLabel);
  assert.equal(modelLabel.hidden, false);

  setValue(doc, 'server-name', 'Hosted box');
  setValue(doc, 'server-url', HOSTED_URL);
  setValue(doc, 'server-model', 'music-large');
  setValue(doc, 'server-key', HOSTED_KEY);
  booted.state.failCloud = 'an API key is required for a hosted endpoint';
  await clickId(doc, 'add-engine-btn');
  assert.equal(doc.getElementById('server-wizard').hidden, false);
  const message = doc.getElementById('server-message');
  assert.equal(message.textContent, 'an API key is required for a hosted endpoint');
  assert.equal(message.classList.contains('error'), true);
  assert.equal(doc.getElementById('server-key').value, HOSTED_KEY);
  assert.equal(callsTo(booted.calls, '/api/studio/engines', 'POST').length, 0);
  assert.equal(callsTo(booted.calls, '/api/studio/engines/test', 'POST').length, 0);

  booted.state.failCloud = '';
  await clickId(doc, 'add-engine-btn');
  const cloud = callsTo(booted.calls, '/api/studio/setup/cloud', 'POST');
  assert.deepEqual(cloud.map(c => c.body), [
    { name: 'Hosted box', url: HOSTED_URL, key: HOSTED_KEY, model: 'music-large' },
    { name: 'Hosted box', url: HOSTED_URL, key: HOSTED_KEY, model: 'music-large' },
  ]);
  const leaked = booted.calls.filter(c => JSON.stringify(c).includes(HOSTED_KEY));
  assert.equal(leaked.length, 2);
  assert.ok(leaked.every(c => c.path === '/api/studio/setup/cloud' && c.method === 'POST'));
  assert.equal(doc.getElementById('server-key').value, '');
  assert.equal(doc.body.textContent.includes(HOSTED_KEY), false);
  assert.equal(doc.getElementById('server-wizard').innerHTML.includes(HOSTED_KEY), false);
  assert.equal(doc.getElementById('server-wizard').hidden, true);
  assert.equal(doc.getElementById('test-server-btn').hidden, false);
  assert.equal(doc.getElementById('api-key-label').hidden, true);
});

test('a successful Add closes the wizard, reloads engines, and shows the new workflow', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  const name = 'Studio <b>box</b>';
  setValue(doc, 'server-name', name);
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = {
    ok: true,
    kind: 'comfyui',
    message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
    models: [ACE_MODEL_READY, MINIMAX_MISSING],
  };
  await clickId(doc, 'test-server-btn');
  await clickId(doc, 'add-engine-btn');

  assert.equal(doc.getElementById('server-wizard').hidden, true);
  const addAt = booted.calls.findIndex(c => c.path === '/api/studio/engines' && c.method === 'POST');
  assert.ok(addAt >= 0);
  const after = booted.calls.slice(addAt + 1);
  assert.ok(after.some(c => c.path === '/api/studio/engines' && c.method === 'GET'));
  assert.ok(after.some(c => c.path === '/api/studio/workflows' && c.method === 'GET'));
  const list = doc.getElementById('engine-list');
  assert.match(text(list), /Studio <b>box<\/b>/);
  assert.equal(list.querySelector('b'), null);
  const added = [...doc.getElementById('engine-select').options].find(o => o.value === 'comfy_workflow:wf-ace');
  assert.ok(added);
  assert.match(added.textContent, /^ACE-Step 1\.5 on /);
  assert.equal([...doc.getElementById('engine-select').options].some(o => o.textContent === 'Custom workflow: ACE-Step 1.5'), false);
});

test('a failed Add stays in the wizard and shows the server error as text', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  setValue(doc, 'server-name', 'Studio box');
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = {
    ok: true,
    kind: 'comfyui',
    message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
    models: [ACE_MODEL_READY],
  };
  await clickId(doc, 'test-server-btn');
  const fail = 'Could not save <img src=x onerror=alert(1)>';
  booted.state.failAdd = fail;
  await clickId(doc, 'add-engine-btn');

  assert.equal(doc.getElementById('server-wizard').hidden, false);
  const message = doc.getElementById('server-message');
  assert.ok(message);
  assert.equal(message.classList.contains('error'), true);
  assert.equal(message.textContent, fail);
  assert.equal(message.querySelector('img'), null);
  assert.equal(doc.querySelector('#server-wizard img'), null);
  assert.equal(doc.getElementById('add-engine-btn').disabled, false);
  const addAt = booted.calls.findIndex(c => c.path === '/api/studio/engines' && c.method === 'POST');
  assert.ok(addAt >= 0);
  assert.equal(booted.calls.slice(addAt + 1).some(c => c.path === '/api/studio/workflows'), false);
});

test('the ui does not show the private example address', () => {
  assert.doesNotMatch(html, /10\.0\.0\.52/);
  assert.doesNotMatch(appjs, /10\.0\.0\.52/);
  assert.doesNotMatch(css, /10\.0\.0\.52/);
  assert.match(html, /placeholder="http:\/\/192\.168\.1\.50:8188"/);
  assert.match(html, /Example: http:\/\/192\.168\.1\.50:8188/);
});

test('wizard-made ACE-Step 1.5 is listed by name and uploads keep the prefix', async t => {
  const booted = await boot({
    workflows: [
      { id: 'wf-ace', name: 'ACE-Step 1.5', target_id: 't1' },
      { id: 'wf-up', name: 'Night bus', target_id: 't1' },
    ],
    engines: [{
      id: 't1', name: 'Studio', url: COMFY_URL, kind: 'comfyui',
      workflows: [
        { id: 'wf-ace', name: 'ACE-Step 1.5', engine_id: 'comfy_workflow:wf-ace' },
      ],
    }],
  });
  t.after(() => booted.window.close());
  await pause(80);
  const doc = booted.window.document;
  const option = value => [...doc.getElementById('engine-select').options].find(item => item.value === value);
  assert.equal(option('ace_local').textContent, 'ACE-Step 1.5 (this computer)');
  assert.equal(option('minimax_m3_comfy').textContent, 'MiniMax Music 3');
  assert.equal(option('comfy_workflow:wf-ace').textContent, 'ACE-Step 1.5 on Studio');
  assert.equal(option('comfy_workflow:wf-up').textContent, 'Custom workflow: Night bus');

});

test('engine kind radios keep the circle beside the title', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const style = doc.createElement('style');
  style.textContent = css;
  doc.head.appendChild(style);
  const label = doc.querySelector('#server-form label.radio-label');
  const input = label.querySelector('input[type="radio"]');
  const copy = label.querySelector('.radio-copy');
  assert.equal(label.firstElementChild, input);
  assert.equal(copy.textContent, 'ComfyUIRuns on a computer you set up');
  assert.equal(copy.querySelector('.radio-help').textContent, 'Runs on a computer you set up');
  const view = booted.window.getComputedStyle(label);
  assert.equal(view.display, 'flex');
  assert.equal(view.flexDirection, 'row');
  const radio = booted.window.getComputedStyle(input);
  assert.notEqual(radio.width, '100%');
  assert.match(css, /\.wizard-form label\.radio-label \{[^}]*display:\s*flex/);
  assert.match(css, /\.wizard-form label\.radio-label input\[type="radio"\] \{[^}]*width:\s*auto/);
});

function optionByValue(doc, value) {
  return [...doc.getElementById('engine-select').options].find(item => item.value === value);
}

test('Copy my engines copies the export text and falls back to a selected box', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(80);
  const copied = [];
  Object.defineProperty(booted.window.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: value => { copied.push(value); return Promise.resolve(); } },
  });
  const button = doc.getElementById('copy-engines-btn');
  const help = 'Copies the names and addresses of your engines so you can paste them on another computer. Keys are not copied.';
  assert.equal(button.textContent, 'Copy my engines');
  assert.equal(button.title, help);
  assert.equal(doc.getElementById(button.getAttribute('aria-describedby')).textContent, help);
  await clickId(doc, 'copy-engines-btn');
  assert.deepEqual(copied, ['Studio box http://192.168.1.50:8188']);
  assert.equal(callsTo(booted.calls, '/api/studio/engines/export', 'GET').length, 1);
  assert.equal(doc.getElementById('move-engines-message').textContent,
    'Copied. On your other computer, open Music engines and press Paste engines. Keys are not included.');
  assert.equal(doc.getElementById('copy-engines-fallback').hidden, true);

  Object.defineProperty(booted.window.navigator, 'clipboard', {
    configurable: true,
    value: { writeText: () => Promise.reject(new Error('denied')) },
  });
  await clickId(doc, 'copy-engines-btn');
  const fallback = doc.getElementById('copy-engines-fallback');
  assert.equal(fallback.hidden, false);
  assert.equal(fallback.readOnly, true);
  assert.equal(fallback.value, 'Studio box http://192.168.1.50:8188');
  assert.equal(fallback.selectionStart, 0);
  assert.equal(fallback.selectionEnd, fallback.value.length);
  assert.match(doc.getElementById('move-engines-message').textContent, /Could not copy automatically/);
  assert.equal(doc.getElementById('move-engines-message').textContent.includes('Copied. On your other computer'), false);
});

test('Paste engines imports the list and reloads the song menu', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  doc.getElementById('nav-engines').click();
  await pause(80);
  const paste = doc.getElementById('paste-engines-btn');
  assert.equal(paste.textContent, 'Paste engines');
  assert.ok(paste.title);
  assert.ok(doc.getElementById(paste.getAttribute('aria-describedby')));
  const addThese = doc.getElementById('import-engines-btn');
  assert.equal(addThese.textContent, 'Add these engines');
  assert.ok(addThese.title);
  assert.ok(doc.getElementById(addThese.getAttribute('aria-describedby')));
  assert.equal(doc.getElementById('paste-engines-box').hidden, true);
  paste.click();
  assert.equal(doc.getElementById('paste-engines-box').hidden, false);
  const pasted = 'Studio box http://192.168.1.9:8188';
  doc.getElementById('paste-engines-text').value = pasted;
  booted.state.importResult = { added: ['Studio box'], skipped: ['Old box'], errors: ['line 3 skipped'] };
  booted.state.workflows = [{ id: 'wf-ace', name: 'ACE-Step 1.5', target_id: 't9' }];
  booted.state.engines = [{
    id: 't9', name: 'Studio box', url: 'http://192.168.1.9:8188', kind: 'comfyui',
    workflows: [{ id: 'wf-ace', name: 'ACE-Step 1.5', engine_id: 'comfy_workflow:wf-ace' }],
  }];
  assert.equal(optionByValue(doc, 'comfy_workflow:wf-ace'), undefined);
  await clickId(doc, 'import-engines-btn');
  const posted = callsTo(booted.calls, '/api/studio/engines/import', 'POST');
  assert.equal(posted.length, 1);
  assert.deepEqual(posted[0].body, { text: pasted });
  assert.equal(doc.getElementById('move-engines-message').textContent,
    'Added: Studio box. Already here: Old box. line 3 skipped');
  assert.match(text(doc.getElementById('engine-list')), /Studio box/);
  assert.match(optionByValue(doc, 'comfy_workflow:wf-ace').textContent, /^ACE-Step 1\.5 on /);
  const importAt = booted.calls.findIndex(c => c.path === '/api/studio/engines/import');
  const after = booted.calls.slice(importAt + 1);
  assert.ok(after.some(c => c.path === '/api/studio/engines' && c.method === 'GET'));
  assert.ok(after.some(c => c.path === '/api/studio/workflows' && c.method === 'GET'));
});

// Found in the 11-16 browser run: after adding an engine, Generate song still used the status
// from before the Add, called the engine not ready, and opened setup with the old 'compute' view
// name, which hid every page.
test('a successful Add asks for fresh engine status', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openServer(booted);
  setValue(doc, 'server-name', 'Studio box');
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = { ok: true, kind: 'comfyui', message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
    models: [ACE_MODEL_READY] };
  await clickId(doc, 'test-server-btn');
  await clickId(doc, 'add-engine-btn');
  await pause(80);
  const addAt = booted.calls.findIndex(c => c.path === '/api/studio/engines' && c.method === 'POST');
  assert.ok(addAt >= 0);
  assert.ok(booted.calls.slice(addAt + 1).some(c => c.path === '/api/status' && c.method === 'GET'));
});

test('opening setup by its old name shows the Music engines page, never a blank screen', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await clickId(doc, 'manage-targets');
  await pause(40);
  const visible = [...doc.querySelectorAll('section.page')].filter(s => !s.hidden).map(s => s.id);
  assert.deepEqual(visible, ['engines-view']);
});

test('a saved engine is checked when the list loads, so its pill is not stuck on Not tested', async t => {
  const booted = await boot({ engines: [{ id: 't1', name: 'Studio', url: COMFY_URL, kind: 'comfyui', workflows: [] }] });
  t.after(() => booted.window.close());
  booted.state.testReply = { ok: true, kind: 'comfyui', message: 'This is ComfyUI.', models: [ACE_MODEL_READY] };
  const doc = await openServer(booted);
  await pause(120);
  assert.ok(booted.calls.some(c => c.path === '/api/studio/engines/test' && c.body && c.body.url === COMFY_URL));
  assert.match(text(doc.getElementById('engine-list')), /Ready/);
});
