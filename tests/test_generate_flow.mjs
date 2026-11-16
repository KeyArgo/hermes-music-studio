// One path to a song: Generate saves, approves with the numeric updated_at, then renders.
// An engine that is not ready becomes "Set up a music engine" and shows selectable_reason.
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

const ACE_READY = { id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', available: true,
  kind: 'local', selectable_reason: null };
const ACE_DOWN = { id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', available: false, kind: 'local',
  selectable_reason: 'ACE-Step checkpoints are missing from /opt/ace' };
const MINI_DOWN = { id: 'minimax_m3_comfy', label: 'MiniMax Music 3', available: false, kind: 'remote',
  selectable_reason: 'No ComfyUI endpoint is configured' };

const TOKEN = 1731686400.25;

function project(extra = {}) {
  return { project_id: 'p1', title: 'First song', caption: 'quiet', lyrics: 'hello',
    engine: 'ace_local', approved: false, updated_at: 10, takes: [], ...extra };
}

function songFlow(calls) {
  return calls.filter(c =>
    (c.path === '/api/create' && c.method === 'POST')
    || (c.path === '/api/update' && c.method === 'POST')
    || c.path === '/api/approve' || c.path === '/api/generate' || c.path === '/api/batch'
  ).map(c => c.path);
}

async function boot(options = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = {
    status: options.status || { success: true, engines: [ACE_READY], projects: [project()] },
    failUpdate: options.failUpdate || '',
    failApprove: options.failApprove || '',
    holdSave: null,
  };
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    if (((path === '/api/update' && method === 'POST') || path === '/api/create') && state.holdSave) {
      await state.holdSave;
    }
    if (path === '/api/update' && method === 'POST' && state.failUpdate) {
      return { ok: false, status: 409, json: async () => ({ detail: state.failUpdate }) };
    }
    if (path === '/api/approve' && state.failApprove) {
      return { ok: false, status: 409, json: async () => ({ detail: state.failApprove }) };
    }
    const generated = calls.some(c => c.path === '/api/generate' || c.path === '/api/batch');
    if (path === '/api/status' && options.statusAfterGenerate && generated) {
      return { ok: true, status: 200, json: async () => options.statusAfterGenerate };
    }
    const draft = (body && body.draft) || {};
    const saved = { ...project(), ...draft, project_id: (body && body.project_id) || 'p-new',
      approved: false, updated_at: TOKEN, takes: [] };
    if (path === '/api/update' && method === 'POST') {
      return { ok: true, status: 200, json: async () => ({ success: true, project: { ...saved, project_id: 'p1' } }) };
    }
    if (path === '/api/create') {
      return { ok: true, status: 200, json: async () => ({ success: true, project: { ...saved, project_id: 'p-new' } }) };
    }
    const settings = options.settings || { success: true, settings: { minimax: {} },
      samplers: ['euler'], schedulers: ['simple'],
      library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } };
    const table = {
      '/api/status': state.status,
      '/api/presets': { success: true, presets: [] },
      '/api/settings': settings,
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [{ id: 'default', name: 'My workspace', project_ids: [] }] },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/discover': options.discover || { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/assign': { success: true },
      '/api/approve': { success: true, project: { ...saved, project_id: (body && body.project_id) || 'p1', approved: true } },
      '/api/generate': { success: true, take: { take_id: 't9', state: 'queued' } },
      '/api/batch': { success: true, takes: [
        { take_id: 't9', state: 'queued' }, { take_id: 't10', state: 'queued' }] },
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

async function openDraft(booted) {
  const doc = booted.window.document;
  doc.getElementById('nav-library').click();
  await pause(30);
  doc.querySelector('#library .project-title').click();
  await pause(20);
  return doc;
}

test('the page has one Generate song button and no separate approve step', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  assert.equal(doc.getElementById('approve'), null, 'Approve draft is gone');
  assert.equal(doc.getElementById('save-draft').textContent, 'Save draft');
  assert.match(text(doc.getElementById('preview-chorus')), /Preview the chorus \(renders audio\)/);
  assert.ok(doc.getElementById('render-takes'), 'the takes selector stays');
  const primary = [...doc.querySelectorAll('#draft-form .button.primary')];
  assert.equal(primary.length, 1);
  assert.equal(primary[0].id, 'render');
});

test('Generate song saves, approves with the numeric token, then generates', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openDraft(booted);
  assert.equal(doc.getElementById('render').textContent, 'Generate song');
  assert.equal(doc.getElementById('render').disabled, false);

  doc.getElementById('render').click();
  await pause(80);

  assert.deepEqual(songFlow(booted.calls), ['/api/update', '/api/approve', '/api/generate']);
  const approved = booted.calls.filter(c => c.path === '/api/approve').pop();
  assert.equal(typeof approved.body.base_updated_at, 'number');
  assert.equal(approved.body.base_updated_at, TOKEN);
  assert.equal(approved.body.project_id, 'p1');
  const generated = booted.calls.filter(c => c.path === '/api/generate').pop();
  assert.equal(generated.body.project_id, 'p1');
  assert.equal(generated.body.engine_id, 'ace_local');
  assert.equal(typeof generated.body.seed, 'number');
});

test('a new draft is created, then approved with the fresh number, then generated', async t => {
  const booted = await boot({ status: { success: true, engines: [ACE_READY], projects: [] } });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const caption = doc.querySelector('[name=caption]');
  caption.value = 'warm acoustic folk';
  caption.dispatchEvent(new booted.window.Event('input', { bubbles: true }));
  await pause(20);

  doc.getElementById('render').click();
  await pause(80);

  assert.deepEqual(songFlow(booted.calls), ['/api/create', '/api/approve', '/api/generate']);
  const approved = booted.calls.filter(c => c.path === '/api/approve').pop();
  assert.equal(approved.body.base_updated_at, TOKEN);
  assert.equal(typeof approved.body.base_updated_at, 'number');
  assert.equal(approved.body.project_id, 'p-new');
});

test('more than one take approves once, then posts /batch and not /generate', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = await openDraft(booted);
  doc.getElementById('render-takes').value = '3';
  doc.getElementById('render').click();
  await pause(80);

  assert.deepEqual(songFlow(booted.calls), ['/api/update', '/api/approve', '/api/batch']);
  const posted = booted.calls.filter(c => c.path === '/api/batch').pop();
  assert.equal(posted.body.count, 3);
  assert.equal(posted.body.engine_id, 'ace_local');
  assert.match(text(doc.getElementById('form-message')), /Rendering 3 takes, one after another/);
});

test('a stale draft stops before anything is rendered', async t => {
  const booted = await boot({ failUpdate: 'stale draft' });
  t.after(() => booted.window.close());
  const doc = await openDraft(booted);
  doc.getElementById('render').click();
  await pause(80);

  assert.deepEqual(songFlow(booted.calls), ['/api/update']);
  assert.match(text(doc.getElementById('form-message')), /stale draft/);
  assert.match(text(doc.getElementById('form-message')), /Library/);
  assert.equal(doc.getElementById('form-message').classList.contains('error'), true);
});

test('a stale approval does not go on to generate', async t => {
  const booted = await boot({ failApprove: 'stale draft' });
  t.after(() => booted.window.close());
  const doc = await openDraft(booted);
  doc.getElementById('render').click();
  await pause(80);

  assert.deepEqual(songFlow(booted.calls), ['/api/update', '/api/approve']);
  assert.match(text(doc.getElementById('form-message')), /stale draft/);
  assert.ok(!booted.calls.some(c => c.path === '/api/generate'));
});

test('an empty draft says why Generate is disabled, and Rendering shows while the save is in flight', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const button = doc.getElementById('render');
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Generate song');
  assert.match(text(doc.getElementById('generate-reason')), /Write a style or lyrics first/);
  assert.equal(doc.getElementById('preview-chorus').disabled, true);
  assert.match(doc.getElementById('preview-chorus').title, /Write a style or lyrics first/);

  const caption = doc.querySelector('[name=caption]');
  caption.value = 'warm folk';
  caption.dispatchEvent(new booted.window.Event('input', { bubbles: true }));
  assert.equal(button.disabled, false);
  assert.equal(doc.getElementById('generate-reason').hidden, true);
  assert.match(text(doc.getElementById('chorus-reason')), /Save the draft before previewing the chorus/);

  let release;
  booted.state.holdSave = new Promise(resolve => { release = resolve; });
  button.click();
  await pause(30);
  assert.match(text(doc.getElementById('generate-reason')), /Rendering/);
  assert.equal(button.disabled, true);
  release();
  await pause(80);
  assert.ok(booted.calls.some(c => c.path === '/api/generate'));
});

test('an engine that is not ready offers setup and the real selectable_reason', async t => {
  const booted = await boot({ status: { success: true, engines: [ACE_DOWN, MINI_DOWN], projects: [] } });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const button = doc.getElementById('render');
  assert.equal(button.textContent, 'Set up a music engine');
  assert.equal(button.disabled, false);
  assert.match(text(doc.getElementById('generate-reason')), /ACE-Step checkpoints are missing from \/opt\/ace/);
  assert.match(text(doc.getElementById('engine-hint')), /selectable_reason|checkpoints are missing from \/opt\/ace/);
  assert.doesNotMatch(text(doc.getElementById('engine-hint')), /undefined/);

  button.click();
  await pause(40);
  assert.equal(doc.getElementById('compute-view').hidden, false);
  assert.equal(doc.getElementById('compute-view').dataset.setup, 'ace');
  assert.equal(doc.activeElement, doc.getElementById('setup-server-url'));

  doc.getElementById('nav-create').click();
  const select = doc.getElementById('engine-select');
  select.value = 'minimax_m3_comfy';
  select.dispatchEvent(new booted.window.Event('change'));
  assert.equal(doc.getElementById('render').textContent, 'Set up a music engine');
  assert.match(text(doc.getElementById('generate-reason')), /No ComfyUI endpoint is configured/);
  doc.getElementById('render').click();
  await pause(40);
  assert.equal(doc.getElementById('compute-view').dataset.setup, 'compute');
  assert.equal(doc.activeElement, doc.getElementById('settings-comfy-url'));
});

test('the No ComfyUI line stays hidden while ACE-Step is selected', async t => {
  const booted = await boot({
    status: { success: true, engines: [ACE_DOWN, MINI_DOWN], projects: [] },
    settings: { success: true, settings: { minimax: {} }, samplers: [], schedulers: [],
      library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
    discover: { success: true, found: [], tested: 4 },
  });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await pause(40);
  assert.ok(!booted.calls.some(c => c.path === '/api/discover'), 'ACE does not go looking for ComfyUI');
  assert.doesNotMatch(text(doc.getElementById('form-message')), /No ComfyUI/);

  const select = doc.getElementById('engine-select');
  select.value = 'minimax_m3_comfy';
  select.dispatchEvent(new booted.window.Event('change'));
  await pause(60);
  assert.ok(booted.calls.some(c => c.path === '/api/discover'));
  assert.match(text(doc.getElementById('form-message')), /No ComfyUI answered/);
});

test('a finished take is playable on the page and links to the Library', async t => {
  const finished = { success: true, engines: [ACE_READY], projects: [project({
    approved: true,
    takes: [{ take_id: 't9', state: 'succeeded', engine_id: 'ace_local' }] })] };
  const booted = await boot({ statusAfterGenerate: finished });
  t.after(() => booted.window.close());
  const doc = await openDraft(booted);
  doc.getElementById('render').click();
  await pause(80);

  const play = doc.getElementById('active-play');
  assert.ok(play, 'the finished take has a play control on the create page');
  assert.equal(doc.getElementById('active-take').hidden, false);
  assert.match(doc.getElementById('player').src, /takes\/t9\/audio/);
  assert.match(text(doc.getElementById('active-progress')), /Play it here/);

  doc.getElementById('active-library').click();
  assert.equal(doc.getElementById('library-view').hidden, false);
  assert.equal(doc.getElementById('create-view').hidden, true);
});

test('cover copy is an upload, with no generation wording', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const copy = text(booted.window.document.getElementById('cover-copy'));
  assert.equal(copy, 'Upload artwork for this song (PNG, JPEG or WebP, up to 2 MB).');
  assert.doesNotMatch(copy, /generat/i);
  assert.doesNotMatch(html, /image provider|Generation may use/);
});

test('Discuss idea confirms before it replaces a draft that already has words', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const sent = [];
  Object.defineProperty(booted.window, 'parent', {
    value: { postMessage: message => sent.push(message) }, configurable: true
  });
  const caption = doc.querySelector('[name=caption]');
  caption.value = 'existing style';
  doc.getElementById('idea-input').value = 'a night drive';
  doc.getElementById('idea-send').click();
  await pause(30);
  const ask = sent.find(m => m.type === 'hermes-music-studio:draft');
  assert.ok(ask, 'Discuss idea still asks the pane');

  booted.window.dispatchEvent(new booted.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:draft-result', id: ask.id, ok: true,
            draft: { title: 'Night Drive', caption: 'warm synthwave' } }
  }));
  await pause(20);
  const dialog = doc.querySelector('.confirm-dialog');
  assert.ok(dialog, 'a written draft is not replaced without confirm');
  assert.match(text(dialog), /Replace the current draft/);
  doc.querySelector('.confirm-cancel').click();
  await pause(20);
  assert.equal(caption.value, 'existing style');

  doc.getElementById('idea-send').click();
  await pause(30);
  const again = sent.filter(m => m.type === 'hermes-music-studio:draft').pop();
  booted.window.dispatchEvent(new booted.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:draft-result', id: again.id, ok: true,
            draft: { title: 'Night Drive', caption: 'warm synthwave' } }
  }));
  await pause(20);
  doc.querySelector('.confirm-confirm').click();
  await pause(20);
  assert.equal(caption.value, 'warm synthwave');
  assert.equal(doc.querySelector('[name=title]').value, 'Night Drive');
});
