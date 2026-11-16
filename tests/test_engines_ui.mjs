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

async function boot() {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = {
    install: installState(),
    hardware: {
      success: true,
      available: true,
      devices: [{ index: 0, name: 'NVIDIA GeForce RTX', memory_used_mb: 512, memory_total_mb: 8192 }],
      processes: [],
      pid_to_gpu: {},
      error: null,
    },
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
