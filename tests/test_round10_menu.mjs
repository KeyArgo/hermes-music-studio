// Round 10 UI fixes (lane r10-menu): the Music engine menu, every way into the engines page, the
// el() argument order, Advanced Save, the version panel, the length limit and the help text.
// Every test below failed on the code before its fix.
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
const text = node => (node && node.textContent) || '';

const COMFY_URL = 'http://192.168.1.50:8188';
const ACE_SERVER = 'http://192.168.1.50:8000';
const ACE_LIMITS = { min_seconds: 10, max_seconds: 180, recommended_seconds: 120 };
const MINIMAX_LIMITS = { min_seconds: 10, max_seconds: 360, recommended_seconds: 120 };

const STATUS_ENGINES = [
  { id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', kind: 'local', available: true,
    selectable: true, selectable_reason: null, limits: ACE_LIMITS },
  { id: 'minimax_m3_comfy', label: 'MiniMax Music 3', kind: 'comfyui', available: true,
    selectable: true, selectable_reason: null, limits: MINIMAX_LIMITS },
];

function project() {
  return { project_id: 'p1', title: 'First song', caption: 'quiet', lyrics: 'hello',
    engine: 'ace_local', approved: false, updated_at: 10, takes: [] };
}

const UPDATE = {
  ok: true,
  installed: { version: '1.0.0', commit: 'a'.repeat(40), commit_short: 'aaaaaaaa' },
  latest: null,
  update_available: false,
  detail: 'Updates come through Hermes: run `hermes plugins update hermes-music-studio`.',
};

// A page with one fake fetch. `extra` names what each route answers, so a test only states the
// part it is about and the rest stays a quiet, working studio.
async function boot(extra = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = {
    projects: extra.projects ? extra.projects.slice() : [],
    enginesInfo: extra.enginesInfo || { engines: [], ace_remote_url: '', hosted: [] },
    statusEngines: extra.statusEngines || STATUS_ENGINES,
    workflows: extra.workflows ? extra.workflows.slice() : [],
    targets: extra.targets ? extra.targets.slice() : [],
    compareEngines: extra.compareEngines || null,
    holdCompare: false,
    releaseCompare: null,
    testReply: extra.testReply || null,
    setupComfy: extra.setupComfy || null,
    cloud: extra.cloud || { success: true, endpoints: [] },
    hfPlan: null,
    workflowSaved: extra.workflowSaved || { success: true, workflow: { id: 'w9', name: 'flow', placeholders: [] } },
    settings: { success: true, settings: { comfy_url: '', minimax: { steps: 8, cfg: 1, cfg_scale: 1,
      top_k: 10, sampler: 'euler', scheduler: 'simple', export_dir: '' } },
      samplers: ['euler'], schedulers: ['simple'],
      library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
  };
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    const json = payload => ({ ok: true, status: 200, json: async () => payload });
    if (path === '/api/status') {
      return json({ success: true, engines: state.statusEngines, projects: state.projects });
    }
    if (path === '/api/studio/engines' && method === 'GET') return json({ success: true, ...state.enginesInfo });
    if (path === '/api/studio/engines/test' && method === 'POST') {
      const reply = state.testReply || { ok: false, message: 'Nothing answered.', problem: 'no_answer', models: [] };
      return json({ success: true, ...reply });
    }
    if (path === '/api/studio/workflows' && method === 'GET') {
      return json({ success: true, workflows: state.workflows.slice() });
    }
    if (path === '/api/studio/workflows' && method === 'POST') return json(state.workflowSaved);
    if (path === '/api/studio/setup/comfy' && method === 'POST') {
      return json(state.setupComfy || { ok: false, detail: 'Not tested.', checks: [] });
    }
    if (path === '/api/studio/setup/cloud' && method === 'GET') return json(state.cloud);
    if (path === '/api/studio/setup/hf' && method === 'POST') {
      return json(state.hfPlan || { success: true, plan: null });
    }
    if (path === '/api/compare/engines') {
      if (state.holdCompare) await new Promise(resolve => { state.releaseCompare = resolve; });
      return json({ success: true, engines: state.compareEngines || [] });
    }
    if (path === '/api/settings' && method === 'POST') {
      state.settings.settings = { ...state.settings.settings, ...body };
      return json({ success: true, settings: state.settings.settings, library: state.settings.library });
    }
    const table = {
      '/api/settings': state.settings,
      '/api/studio/targets': { success: true, targets: state.targets },
      '/api/presets': { success: true, presets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': extra.update || UPDATE,
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/hardware': { success: true, devices: [] },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/studio/assign': { success: true },
      '/api/studio/install/suggest': { models_dir: 'C:/ComfyUI/models' },
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
  await pause(80);
  return { window, calls, state };
}

const callsTo = (calls, path, method) => calls.filter(c => c.path === path && c.method === method);
const after = (calls, index) => calls.slice(index + 1);

function setValue(doc, id, value) {
  const field = doc.getElementById(id);
  assert.ok(field, id);
  field.value = value;
  field.dispatchEvent(new doc.defaultView.Event('input', { bubbles: true }));
  return field;
}

async function clickId(doc, id, ms = 80) {
  const node = doc.getElementById(id);
  assert.ok(node, id);
  node.click();
  await pause(ms);
}

// ------------------------------------------------------------------ A: the ACE-Step server
// A server added with the wizard saves ace_remote_url. The Create menu listed only the local ACE
// and the workflows, so the engine a person just added could not be chosen.

test('an ACE-Step server added on the engines page appears in the Music engine menu', async t => {
  const booted = await boot({ enginesInfo: { engines: [], ace_remote_url: ACE_SERVER, hosted: [] } });
  t.after(() => booted.window.close());
  const select = booted.window.document.getElementById('engine-select');
  const option = [...select.options].find(o => o.value === 'ace_remote');
  assert.ok(option, 'the menu offers ace_remote');
  assert.match(text(option), /ACE-Step server/, 'named in plain words');
  assert.match(text(option), /192\.168\.1\.50:8000/, 'and its address says which server');
});

test('a ready ACE-Step engine in the status data is offered even with no address saved here', async t => {
  const booted = await boot({ statusEngines: [...STATUS_ENGINES, { id: 'ace_remote',
    label: 'ACE-Step 1.5 (remote)', kind: 'remote', available: true, selectable: true,
    selectable_reason: null, limits: MINIMAX_LIMITS }] });
  t.after(() => booted.window.close());
  await pause(80);
  const option = [...booted.window.document.getElementById('engine-select').options]
    .find(o => o.value === 'ace_remote');
  assert.ok(option, 'the engine the status data says is ready is in the menu');
  assert.match(text(option), /ACE-Step server/);
});

test('the Music engine menu does not offer a server that is not set up', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const values = [...booted.window.document.getElementById('engine-select').options].map(o => o.value);
  assert.equal(values.includes('ace_remote'), false);
  assert.ok(values.includes('ace_local') && values.includes('minimax_m3_comfy'), 'the built-in engines stay');
});

test('an ACE-Step server is listed on the engines page, not "No music engines set up yet"', async t => {
  const booted = await boot({ enginesInfo: { engines: [], ace_remote_url: ACE_SERVER, hosted: [] } });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await clickId(doc, 'nav-engines', 120);
  const list = doc.getElementById('engine-list');
  assert.doesNotMatch(text(list), /No music engines set up yet/);
  assert.match(text(list), /ACE-Step server/);
  assert.match(text(list), /192\.168\.1\.50:8000/);
});

// ------------------------------------------------------------------ B: every way in
// loadEngines() and wireEnginesPage() ran from the nav click only, so the other three ways into
// the page showed an empty list and dead buttons.

test('the Library empty state opens the engines page loaded and wired', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const link = [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Music engines');
  assert.ok(link, 'the empty state offers the engines page');
  const before = booted.calls.length;
  link.click();
  await pause(120);
  assert.ok(after(booted.calls, before - 1).some(c => c.path === '/api/studio/engines' && c.method === 'GET'),
    'the list is loaded');
  await clickId(doc, 'add-server-btn', 40);
  assert.equal(doc.getElementById('server-wizard').hidden, false, 'the Add wizard is wired');
});

test('Manage compute targets opens the engines page loaded and wired', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const before = booted.calls.length;
  await clickId(doc, 'manage-targets', 120);
  assert.ok(after(booted.calls, before - 1).some(c => c.path === '/api/studio/engines' && c.method === 'GET'),
    'the list is loaded');
  await clickId(doc, 'add-server-btn', 40);
  assert.equal(doc.getElementById('server-wizard').hidden, false, 'the Add wizard is wired');
});

test('Generate song with no engine ready opens the engines page loaded and wired', async t => {
  const booted = await boot({ statusEngines: [{ id: 'ace_local', label: 'ACE-Step 1.5', available: false,
    selectable: false, selectable_reason: 'Set the ACE-Step folders.' }] });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await pause(80);
  const render = doc.getElementById('render');
  assert.match(text(render), /Set up a music engine/, 'the button is the way out');
  const before = booted.calls.length;
  render.click();
  await pause(200);
  assert.ok(after(booted.calls, before - 1).some(c => c.path === '/api/studio/engines' && c.method === 'GET'),
    'the list is loaded');
  await clickId(doc, 'add-server-btn', 40);
  assert.equal(doc.getElementById('server-wizard').hidden, false, 'the Add wizard is wired');
});

test('entering the engines page more than once does not double-wire its buttons', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await clickId(doc, 'nav-engines', 80);
  await clickId(doc, 'nav-engines', 80);
  await clickId(doc, 'manage-targets', 80);
  const textBlock = doc.querySelector('.info-text');
  const toggle = doc.querySelector('.info-toggle');
  assert.ok(toggle && textBlock, 'the What is this toggle exists');
  toggle.click();
  await pause(20);
  assert.equal(textBlock.hidden, false, 'one click opens it once');
});

// ------------------------------------------------------------------ C: el(tag, text, class)
// el('p', 'hint', msg) put the word "hint" on the page and made the message the class name.

test('no el() call passes a class name where the text belongs', () => {
  const bad = appjs.split('\n')
    .map((line, index) => ({ line, number: index + 1 }))
    .filter(row => /el\('[a-z]+',\s*'hint(\s|'|")/.test(row.line));
  assert.deepEqual(bad.map(row => row.number), [], 'these lines still swap the text and the class');
});

test('the compare panel shows its words with the hint class', async t => {
  const booted = await boot({ projects: [project()], compareEngines: [
    { engine_id: 'ace_local', label: 'ACE-Step 1.5', ready: true },
    { engine_id: 'minimax_m3_comfy', label: 'MiniMax Music 3', ready: true },
  ] });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const compare = [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Compare engines');
  assert.ok(compare, 'the library card offers a comparison');
  booted.state.holdCompare = true;
  compare.click();
  await pause(40);
  const first = doc.querySelector('#library .compare-panel p');
  assert.ok(first, 'the panel says what it is doing');
  assert.match(text(first), /Checking which engines can render/);
  assert.equal(text(first), 'Checking which engines can render…', 'the word "hint" is not shown');
  assert.ok(first.classList.contains('hint'), 'the class is on the element');

  booted.state.releaseCompare();
  await pause(80);
  const second = doc.querySelector('#library .compare-panel p');
  assert.match(text(second), /Same draft, one render at a time/);
  assert.ok(second.classList.contains('hint'));
});

test('setup plan rows and the hosted list are styled, not printed with the word "hint"', async t => {
  const booted = await boot({
    cloud: { success: true, endpoints: [{ name: 'default', url: 'https://api.example.com/v1',
      model: 'm', has_key: true }], message: 'Saved.' },
  });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const cloudRows = [...doc.querySelectorAll('#setup-cloud-list p')];
  assert.ok(cloudRows.length >= 2, 'the hosted endpoint and its message are listed');
  for (const row of cloudRows) {
    assert.notEqual(text(row), 'hint');
    assert.ok(row.classList.contains('hint'), 'each row is a hint');
  }

  // "Where would it go?" fills #setup-hf-status with one line per fact, through renderSetupChecks.
  booted.state.hfPlan = { success: true, plan: { download_to: 'C:/ComfyUI/models/diffusion_models',
    local_comfy: 'C:/ComfyUI', where_it_goes: 'models/diffusion_models', size_hint: 'about 14.7 GB' } };
  await clickId(doc, 'setup-hf-plan', 120);
  const lines = [...doc.querySelectorAll('#setup-hf-status p')];
  assert.ok(lines.length >= 4, 'the plan is listed');
  assert.match(text(lines[0]), /C:\/ComfyUI\/models\/diffusion_models/);
  for (const line of lines) {
    assert.notEqual(text(line), 'hint');
    assert.ok(line.classList.contains('hint'), 'each plan line is a hint');
  }
});

// ------------------------------------------------------------------ D: Advanced Save
// #save-advanced had no handler at all: the folder and the workflow file were never saved.

test('Advanced Save stores the export folder', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  setValue(doc, 'export-dir', '/home/dev/Music');
  await clickId(doc, 'save-advanced', 120);
  const saved = callsTo(booted.calls, '/api/settings', 'POST').pop();
  assert.ok(saved, 'the folder is sent');
  assert.equal(saved.body.export_dir, '/home/dev/Music');
  assert.match(text(doc.getElementById('advanced-message')), /\/home\/dev\/Music/, 'the result is shown');
});

test('Advanced Save uploads a workflow to a saved engine', async t => {
  const booted = await boot({
    targets: [{ id: 't1', name: 'Studio box', url: COMFY_URL }],
    workflowSaved: { success: true, workflow: { id: 'w9', name: 'flow-two', placeholders: ['lyrics'] } },
  });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const input = doc.getElementById('custom-workflow');
  Object.defineProperty(input, 'files', { configurable: true,
    value: [{ name: 'flow-two.json', text: async () => '{"1":{"class_type":"Sampler"}}' }] });
  await clickId(doc, 'save-advanced', 150);
  const posted = callsTo(booted.calls, '/api/studio/workflows', 'POST').pop();
  assert.ok(posted, 'the workflow is uploaded');
  assert.equal(posted.body.name, 'flow-two');
  assert.equal(posted.body.target_id, 't1');
  assert.match(text(doc.getElementById('advanced-message')), /flow-two/);
  assert.match(text(doc.getElementById('advanced-message')), /\{\{lyrics\}\}/, 'what it can fill is named');
});

test('Advanced Save says what to do when no engine is saved yet', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const input = doc.getElementById('custom-workflow');
  Object.defineProperty(input, 'files', { configurable: true,
    value: [{ name: 'flow-two.json', text: async () => '{"1":{}}' }] });
  await clickId(doc, 'save-advanced', 150);
  const said = text(doc.getElementById('advanced-message'));
  assert.match(said, /Music engines page/, 'it points at the page that adds one');
  assert.equal(callsTo(booted.calls, '/api/studio/workflows', 'POST').length, 0);
});

// ------------------------------------------------------------------ E: updates are Hermes's job
// #version-undo was enabled whenever a commit was known, and /update/undo always refuses.

test('the version panel does not offer an undo that cannot work', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await pause(80);
  assert.equal(doc.getElementById('version-undo').hidden, true, 'Undo is not shown');
  assert.match(doc.getElementById('version-update').title, /hermes plugins update hermes-music-studio/,
    'the update button names the command that does work');
});

test('pressing Update explains that Hermes handles updates', async t => {
  const booted = await boot({ update: { ...UPDATE, update_available: true } });
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await pause(80);
  const before = booted.calls.length;
  doc.getElementById('version-update').click();
  await pause(80);
  assert.match(text(doc.getElementById('version-message')), /hermes plugins update hermes-music-studio/);
  assert.equal(after(booted.calls, before - 1).filter(c => /\/update\/(apply|undo)/.test(c.path)).length, 0,
    'nothing calls a route that always refuses');
});

// ------------------------------------------------------------------ F: length follows the engine
// The length field said max 180 for every engine, so MiniMax and workflows could not ask for 360.

test('the length limit follows the chosen engine, and a too-long value is clamped', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  const engine = doc.getElementById('engine-select');
  const length = doc.querySelector('#draft-form [name=duration_seconds]');
  assert.equal(length.max, '180', 'the local ACE-Step takes 180 s');

  engine.value = 'minimax_m3_comfy';
  engine.dispatchEvent(new booted.window.Event('change', { bubbles: true }));
  assert.equal(length.max, '360', 'MiniMax takes 360 s');
  length.value = '300';
  length.dispatchEvent(new booted.window.Event('input', { bubbles: true }));
  assert.equal(length.value, '300', '300 s is allowed on MiniMax');

  engine.value = 'ace_local';
  engine.dispatchEvent(new booted.window.Event('change', { bubbles: true }));
  assert.equal(length.max, '180', 'back to 180 s');
  assert.equal(length.value, '180', 'and 300 s becomes 180 s rather than a refused render');
});

test('the length limit falls back to 180 when the engine is not in the status data', async t => {
  const booted = await boot({ statusEngines: [{ id: 'ace_local', label: 'ACE-Step 1.5', available: true }] });
  t.after(() => booted.window.close());
  const length = booted.window.document.querySelector('#draft-form [name=duration_seconds]');
  assert.equal(length.max, '180');
});

// ------------------------------------------------------------------ G: an honest no-answer card
// Every timeout was called "Firewall blocking", which sent people to change a firewall for a
// server that was simply not running.

test('a server that does not answer lists the likely causes, not only a firewall', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const doc = booted.window.document;
  await clickId(doc, 'nav-engines', 120);
  await clickId(doc, 'add-server-btn', 40);
  setValue(doc, 'server-name', 'Studio box');
  setValue(doc, 'server-url', COMFY_URL);
  booted.state.testReply = { ok: false, message: 'Nothing answered at that address.', problem: 'no_answer', models: [] };
  await clickId(doc, 'test-server-btn', 120);

  const trouble = doc.getElementById('troubleshooting');
  assert.equal(trouble.hidden, false, 'the help card is shown');
  assert.match(text(trouble.querySelector('h4')), /^No answer$/);
  const said = text(trouble.querySelector('p'));
  assert.match(said, /server may be off/);
  assert.match(said, /port may be wrong/);
  assert.match(said, /firewall/);
  assert.match(said, /8188 or 8000/);
  assert.doesNotMatch(text(trouble.querySelector('h4')), /Firewall/, 'the title no longer assumes a firewall');
});
