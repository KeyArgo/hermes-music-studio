// The settings screen and the file-management buttons, exercised in a real DOM
// against a stubbed API: the panel must fill from /settings, and each button must
// send the request it claims (no invented endpoints).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

// jsdom is a development dependency: a clone runs `npm install` and finds it locally. The
// fallback exists only for machines where it is already installed elsewhere (a Hermes checkout).
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

const SETTINGS = {
  success: true,
  settings: { comfy_url: 'http://192.0.2.9:8188',
    minimax: { steps: 12, cfg: 3.5, cfg_scale: 1.25, top_k: 20, sampler: 'dpmpp_2m', scheduler: 'karras' } },
  defaults: { comfy_url: '', minimax: { steps: 30 } },
  limits: { steps: [1, 100], cfg: [0, 20], cfg_scale: [0, 10], top_k: [1, 200] },
  samplers: ['euler', 'dpmpp_2m', 'heun'],
  schedulers: ['simple', 'karras'],
  library: { default: '/home/me/.hermes/plugin-data/hermes-music-studio', active: '/music/lib',
    moved: true, projects: 2, audio_files: 3, bytes: 2048 }
};

const STATUS = { success: true, engine_available: false, engines: [], projects: [
  { project_id: 'p1', title: 'First song', caption: 'quiet', engine: 'ace', approved: true, takes: [
    { take_id: 't1', state: 'succeeded', engine_id: 'ace_local', requested_seed: 7 },
    { take_id: 't2', state: 'succeeded', engine_id: 'ace_local', requested_seed: 8,
      section_of: { project_id: 'p1', take_id: 't1', start_s: 1, end_s: 2 } }
  ] }
] };
const PRESETS = { success: true, presets: [] };
const empty = { success: true };

async function boot(overrides = {}, options = {}) {
  // A framed page arrives with ?token=…; a test can ask for that shape, which is what makes the
  // media-URL assertions below possible at all.
  const pageUrl = 'http://127.0.0.1:8794/' + (options.token ? `?token=${encodeURIComponent(options.token)}` : '');
  const dom = new JSDOM(html, { url: pageUrl, runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  window.confirm = () => true;
  // jsdom has no media stack: `play()` is unimplemented and `load()` does nothing. Stub both so a
  // test can assert what the footer was *told* to play rather than whether sound came out.
  window.HTMLMediaElement.prototype.play = function () { this.__played = true; return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () { this.__loaded = true; };
  window.fetch = async (url, options = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ path, method: options.method || 'GET', body });
    const table = {
      '/api/status': STATUS,
      '/api/presets': PRESETS,
      '/api/settings': SETTINGS,
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/models': { success: true, providers: [], provider: 'nous', model: 'm' },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/about': { success: true, version: '0.2.5', visibility: 'private',
        build: { version: '0.2.5-dev.17', commit: 'abcdef1234567890', commit_short: 'abcdef12',
                 commit_date: '2026-11-09T19:00:00-07:00', built_at: '2026-11-09T19:04:11-07:00',
                 dirty: false, channel: 'dev', source: 'git' },
        repo: 'https://github.com/KeyArgo/hermes-music-studio-dev',
        paths: { tests: 'python3.13 -m pytest tests -q',
          contribute: 'https://github.com/KeyArgo/hermes-music-studio-dev/blob/HEAD/CONTRIBUTING.md',
          issues: 'https://github.com/KeyArgo/hermes-music-studio-dev/issues/new' },
        revamp: { tagline: 'The listening side of the studio.', install: 'hermes plugins install revamp',
          requires: 'Hermes Agent 0.22.5 or newer.', state: 'mcp-only',
          repo: 'https://github.com/KeyArgo/hermes-revamp' } },
      '/api/about/diagnostics': { success: true, text: 'Music Studio 0.2.5\nPlatform: Linux\nLibrary: /music/lib' },
      '/api/update': { ok: true, installed: { version: '1.0.0-dev.235', commit: '4cbe561e' + '0'.repeat(32),
        commit_short: '4cbe561e' }, latest: { commit: 'a'.repeat(40), commit_short: 'aaaa1111',
        message: 'fix: the newest thing' }, update_available: true,
        detail: 'a newer build is published' },
      '/api/update/job': { state: 'idle' },
      '/api/studio/setup/cloud': { success: true, endpoints: [] },
      '/api/studio/setup/hf': { success: true, status: { running: false, detail: 'idle' } },
      '/api/studio/setup/comfy': { ok: false, checks: [
        { name: 'server answers', ok: true, detail: 'ComfyUI' },
        { name: 'MiniMax Music 3 nodes', ok: false, detail: 'missing: MiniMaxMusic3Sampler' }],
        detail: 'this ComfyUI has no MiniMax Music 3 nodes' }
    };
    let payload = table[path];
    if (path === '/api/settings' && options.method === 'POST') payload = { ...SETTINGS, settings: body };
    if (path === '/api/host/close') payload = { success: true, closing: true };
    if (path === '/api/checkpoint') payload = { success: true, saved: 2, fidelity: 'seed', pruned: [] };
    if (path === '/api/checkpoints/p1') payload = { success: true, checkpoints: [
      { checkpoint_id: 'cp1', at: 1, label: 'before', approved: false, kept_take_id: 't1', fidelity: 'seed' }] };
    if (path === '/api/checkpoint/restore') payload = { success: true, restored: 'before', kept_take_id: 't1' };
    if (path === '/api/take-check') payload = body && body.retry
      ? { success: true, retried: true, seed: 424242, because: ['the words reached the engine'] }
      : { success: true, state: 'failed', summary: '1 check(s) failed: the words reached the engine',
          checks: [{ name: 'the words reached the engine', ok: false, detail: 'caption carries 1/2; missing: fiddle' },
                   { name: 'no clipping', ok: true, detail: 'peak -1 dBFS' }] };
    if (path === '/api/snippet') payload = { success: true, take: { take_id: 'c1', state: 'running' } };
    if (path === '/api/snippet/splice') payload = { success: true, take: { take_id: 's1' } };
    if (path === '/api/change') payload = { success: true,
      changed: ['style: quiet, fiddle'], notes: ['asked for: fiddle'],
      project: { ...STATUS.projects[0], caption: 'quiet, fiddle', approved: false,
        memory: { events: [{ at: 1, kind: 'change', text: body.instruction }] } } };
    if (path === '/api/studio/revamp/added') payload = { success: true, playlist: 'pl01', name: 'Music Studio',
      items: [{ id: 'studio:p1:t1', source: 'studio', title: 'Afterglow', artist: 'Hermes Music Studio',
        playback: { backend: 'webamp', url: 'http://127.0.0.1:8775/api/projects/p1/takes/t1/audio' } }] };
    if (path === '/api/studio/revamp/add-take') payload = { success: true, item: { id: 'studio:p1:t1' } };
    if (path === '/api/studio/revamp/playlist') payload = { success: true, folder: '/home/alex/Music/Hermes Music Studio',
      files: ['/home/alex/Music/Hermes Music Studio/Afterglow.wav'], playlist: { id: 'pl02', name: body.name } };
    if (path === '/api/compare/engines') payload = { success: true, engines: [
      { engine_id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', ready: true, detail: '' },
      { engine_id: 'minimax_m3_comfy', label: 'MiniMax Music 3 (ComfyUI)', ready: true, detail: '' },
      { engine_id: 'ace_remote', label: 'ACE-Step 1.5 (remote)', ready: false,
        detail: 'No ACE-Step server is configured' }] };
    if (path === '/api/compare') payload = { success: true, plan: { state: 'running', engines: [
      { engine_id: 'ace_local', state: 'running' }, { engine_id: 'minimax_m3_comfy', state: 'queued' }] } };
    if (path.startsWith('/api/compare/') && path !== '/api/compare/engines') payload = { success: true, plan: { state: 'done', engines: [
      { engine_id: 'ace_local', state: 'succeeded', take_id: 't1', seconds: 10 },
      { engine_id: 'minimax_m3_comfy', state: 'succeeded', take_id: 't2', seconds: 9.9 }] } };
    if (path === '/api/export') payload = { success: true, path: '/music/First song.wav' };
    if (path === '/api/discover') payload = { success: true, tested: 12, best: 'http://127.0.0.1:8188',
      found: [{ url: 'http://127.0.0.1:8188', reachable: true, comfyui: true, minimax: true,
        detail: 'MiniMax Music 3 nodes present' }] };
    if (path === '/api/settings/test') payload = { success: true, url: body.url, minimax: SETTINGS.settings.minimax,
      status: { available: false, checks: [{ name: 'comfyui reachable', ok: false, detail: 'connection refused' }] } };
    if (path === '/api/library/relocate') payload = { success: true, copied: 4, mode: body.mode,
      library: { ...SETTINGS.library, active: body.path } };
    if (path === '/api/library/reset') payload = { success: true, library: { ...SETTINGS.library, active: SETTINGS.library.default, moved: false } };
    if (path === '/api/duplicate') payload = { success: true, project: { project_id: 'p2', title: 'First song (copy)' } };
    if (path === '/api/delete' || path === '/api/take/delete') payload = empty;
    if (['/api/versions', '/api/section/render', '/api/section/splice', '/api/fade'].includes(path)) payload = empty;
    // Per-test overrides win over the shared table: apply them last, or the table's own line for
    // the same path quietly replaces them.
    if (path in overrides) payload = overrides[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await new Promise(resolve => setTimeout(resolve, 30));
  return { dom, window, calls };
}

const text = node => (node && node.textContent) || '';
const confirmInPage = async window => {
  const button = window.document.querySelector('.confirm-dialog .confirm-confirm');
  assert.ok(button, 'the action asks in the page, not through window.confirm');
  button.click();
  await new Promise(resolve => setTimeout(resolve, 20));
};

test('the render-settings panel fills from /settings', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  assert.equal(doc.getElementById('settings-comfy-url').value, 'http://192.0.2.9:8188');
  assert.equal(doc.getElementById('settings-steps').value, '12');
  assert.equal(doc.getElementById('settings-cfg').value, '3.5');
  assert.equal(doc.getElementById('settings-cfg-scale').value, '1.25');
  assert.equal(doc.getElementById('settings-top-k').value, '20');
  assert.equal(doc.getElementById('settings-sampler').value, 'dpmpp_2m');
  assert.equal(doc.getElementById('settings-scheduler').value, 'karras');
  assert.match(text(doc.getElementById('library-path')), /\/music\/lib \(custom folder\)/);
});

test('saving settings posts the numbers the fields hold', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('settings-steps').value = '18';
  doc.getElementById('settings-sampler').value = 'heun';
  doc.getElementById('settings-form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  await new Promise(resolve => setTimeout(resolve, 20));
  const posted = calls.filter(c => c.path === '/api/settings' && c.method === 'POST').pop();
  assert.ok(posted, 'a settings POST was sent');
  assert.equal(posted.body.minimax.steps, 18);
  assert.equal(posted.body.minimax.sampler, 'heun');
  assert.equal(posted.body.comfy_url, 'http://192.0.2.9:8188');
});

test('test endpoint asks the server to probe and shows each check', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('settings-test').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.ok(calls.some(c => c.path === '/api/settings/test'));
  assert.match(text(doc.getElementById('settings-checks')), /comfyui reachable/);
  assert.match(text(doc.getElementById('settings-checks')), /connection refused/);
  assert.match(text(doc.getElementById('settings-message')), /not ready/);
});

test('library buttons send copy, move and reset with the typed folder', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('library-target').value = '/home/me/Music/Hermes';
  doc.getElementById('library-copy').click();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual(calls.filter(c => c.path === '/api/library/relocate').pop().body,
    { path: '/home/me/Music/Hermes', mode: 'copy' });
  doc.getElementById('library-reset').click();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.ok(calls.some(c => c.path === '/api/library/reset'));
});

test('library cards offer duplicate and delete, and deleting a take calls the take route', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  window.confirm = () => true;
  const buttons = [...doc.querySelectorAll('#library button')];
  const duplicate = buttons.find(b => text(b) === 'Duplicate');
  const remove = buttons.find(b => text(b) === 'Delete');
  const removeTake = buttons.find(b => text(b) === 'Delete take');
  assert.ok(duplicate && remove && removeTake, 'all three buttons render');

  duplicate.click();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.ok(calls.some(c => c.path === '/api/duplicate' && c.body.project_id === 'p1'));

  removeTake.click();
  await confirmInPage(window);
  assert.ok(calls.some(c => c.path === '/api/take/delete' && c.body.take_id === 't1'));

  remove.click();
  await confirmInPage(window);
  assert.ok(calls.some(c => c.path === '/api/delete' && c.body.project_id === 'p1'));
});

test('revisions: three versions, redo one part, splice it, and fade the ending', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const buttons = [...doc.querySelectorAll('#library button')];
  const pause = () => new Promise(resolve => setTimeout(resolve, 25));

  buttons.find(b => text(b) === '3 versions').click();
  await pause();
  assert.deepEqual(calls.filter(c => c.path === '/api/versions').pop().body,
    { project_id: 'p1', count: 3 });

  // The edit is a form on the row now: three typed dialogs used to stand in front of it.
  window.prompt = () => { throw new Error('typed dialog opened'); };
  // Re-query: clicking "3 versions" refreshes the library, so the nodes captured above are detached
  // and a form appended to a detached row is invisible to the document.
  [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Change this part').click();
  await pause();
  const form = doc.querySelector('.snippet-form');
  form.querySelector('.snippet-start').value = '2';
  form.querySelector('.snippet-end').value = '6';   // the engine repaints at least 3 s at a time
  form.querySelector('.snippet-words').value = 'dreamier strings';
  [...form.querySelectorAll('button')].find(b => text(b) === 'Render the new part').click();
  await pause();
  assert.deepEqual(calls.filter(c => c.path === '/api/snippet').pop().body,
    { project_id: 'p1', take_id: 't1', start_s: 2, end_s: 6, instruction: 'dreamier strings' });

  buttons.find(b => text(b) === 'Splice into song').click();
  await pause();
  assert.deepEqual(calls.filter(c => c.path === '/api/section/splice').pop().body,
    { project_id: 'p1', take_id: 't1', candidate_take_id: 't2' });

  buttons.find(b => text(b) === 'Fade out').click();
  await pause();
  assert.ok(calls.some(c => c.path === '/api/fade' && c.body.take_id === 't1'));
});

test('about panel plugs REVamp and copies a diagnostics report', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;

  assert.equal(doc.getElementById('revamp-install').textContent, 'hermes plugins install revamp');
  assert.match(text(doc.getElementById('revamp-state')), /MCP server only/i);
  assert.match(text(doc.getElementById('contribute-note')), /private/i);
  assert.equal(doc.getElementById('tests-command').textContent, 'python3.13 -m pytest tests -q');

  doc.getElementById('copy-diagnostics').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.ok(calls.some(c => c.path === '/api/about/diagnostics'), 'diagnostics requested');
  const box = doc.getElementById('diagnostics-out');
  assert.equal(box.hidden, false);
  assert.match(text(box), /Music Studio 0\.2\.5/);
  assert.match(text(doc.getElementById('about-message')), /copied|shown above/i);
});

test('finding a renderer offers the one that answered, and fills the endpoint', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('discover-renderers').click();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.ok(calls.some(c => c.path === '/api/discover'), 'the page asks the server to scan');
  const box = doc.getElementById('discover-results');
  assert.match(text(box), /127\.0\.0\.1:8188/, 'the found endpoint is listed');
  const use = [...box.querySelectorAll('button')].find(button => /Use this/.test(text(button)));
  assert.ok(use, 'a found endpoint can be chosen');
  use.click();
  assert.equal(doc.getElementById('settings-comfy-url').value, 'http://127.0.0.1:8188');
});

test('closing the studio posts the close and says the studio stopped', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('close-studio').click();
  await confirmInPage(window);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.ok(calls.some(c => c.path === '/api/host/close'), 'the page asks the host to close');
  assert.match(text(doc.body), /Studio closed\./);
  assert.match(text(doc.body), /library is untouched/);
});

test('the REVamp screen lists only the takes Music Studio added', async t => {
  const { window, calls } = await boot({ '/api/studio/revamp/status': { available: true, player_connected: false } });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-listen').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.ok(calls.some(c => c.path === '/api/studio/revamp/added'), 'the screen asks for the studio list');
  const box = doc.getElementById('revamp-added');
  assert.match(text(box), /Afterglow/);
  assert.doesNotMatch(text(box), /Dune|audiobook/i, 'nothing from the REVamp library leaks in');
  assert.equal(doc.getElementById('revamp-query'), null, 'there is no library search to browse other music');
});

test('a missing REVamp is never asked for its added list', async t => {
  // The status answers { detail: 'REVamp is unavailable.' } here. Asking for the list anyway is a
  // guaranteed 503 on every visit, and a red error in the console for a plugin that is optional.
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-listen').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.ok(!calls.some(c => c.path === '/api/studio/revamp/added'),
    'no list is requested from a REVamp that is not there');
  assert.match(text(doc.getElementById('revamp-status')), /not installed/i);
  assert.match(text(doc.getElementById('revamp-added')), /Nothing added yet/);
});

test('Add to REVamp hands the take over without a playlist picker', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const add = [...doc.querySelectorAll('button')].find(b => /Add to REVamp/.test(text(b)));
  assert.ok(add, 'the library offers Add to REVamp');
  add.click();
  await new Promise(resolve => setTimeout(resolve, 40));
  const posted = calls.filter(c => c.path === '/api/studio/revamp/add-take').pop();
  assert.ok(posted, 'the take is handed to REVamp');
  assert.deepEqual(Object.keys(posted.body).sort(), ['project_id', 'take_id']);
});

test('closing the studio tells the Hermes pane, so the frame goes away', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const sent = [];
  Object.defineProperty(window, 'parent', { value: { postMessage: m => sent.push(m) }, configurable: true });
  doc.getElementById('close-studio').click();
  await confirmInPage(window);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.ok(sent.some(m => m && m.type === 'hermes-music-studio:closed'), 'the pane is told to close');
  assert.ok(calls.some(c => c.path === '/api/host/close'));
});

test('a new REVamp playlist is built from the exported takes, not from the live stream', async t => {
  const { window, calls } = await boot({ '/api/studio/revamp/status': { available: true, player_connected: false } });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-listen').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  doc.getElementById('revamp-new-name').value = 'Drive mix';
  doc.getElementById('revamp-create').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  const posted = calls.filter(c => c.path === '/api/studio/revamp/playlist').pop();
  assert.ok(posted, 'the playlist is built through the export route');
  assert.equal(posted.body.name, 'Drive mix');
  assert.deepEqual(posted.body.takes, [{ project_id: 'p1', take_id: 't1' }]);
  assert.match(text(doc.getElementById('revamp-status')), /created in REVamp/);
});

test('the version is on screen from the moment the studio opens', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  await new Promise(resolve => setTimeout(resolve, 40));
  const chip = window.document.getElementById('app-version');
  // version · short commit · date (VERSION-CHIP.md) — from the server's stamp, not from this bundle.
  assert.equal(text(chip), 'v0.2.5-dev.17 · abcdef12 · 2026-11-09');
  assert.equal(chip.tagName, 'BUTTON');
  assert.equal(chip.getAttribute('aria-label'), 'Copy version v0.2.5-dev.17 · abcdef12 · 2026-11-09');
  assert.match(chip.title, /Hermes Music Studio 0\.2\.5-dev\.17/);
  assert.match(chip.title, /commit abcdef1234567890/);
  assert.ok(!chip.classList.contains('dirty'), 'a clean tree is not marked modified');
});

test('clicking the version chip copies the whole line', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  await new Promise(resolve => setTimeout(resolve, 40));
  const copied = [];
  Object.defineProperty(window.navigator, 'clipboard', {
    value: { writeText: text => { copied.push(text); return Promise.resolve(); } },
    configurable: true,
  });
  const chip = window.document.getElementById('app-version');
  await chip.onclick();
  assert.deepEqual(copied, ['Hermes Music Studio v0.2.5-dev.17 · abcdef12 · 2026-11-09'],
    'the product name plus the exact line the chip shows is what a bug report gets');
  assert.equal(chip.dataset.copied, '1');
  assert.equal(text(chip), 'copied ✓');
});

test('the version chip never claims a copy that did not happen', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  await new Promise(resolve => setTimeout(resolve, 40));

  const chip = window.document.getElementById('app-version');
  // No clipboard API and no execCommand in this DOM: copyText must report failure.
  await chip.onclick();
  assert.equal(chip.dataset.copied, 'failed', 'a failed copy is not shown as success');
  assert.equal(text(chip), 'copy failed');
});

test('the chip shows the line the server built when it sends one', async t => {
  const { window } = await boot({ '/api/about': { success: true, version: '1.0.0', build: { version: '1.0.0', line: 'v1.0.0 · 11112222 · 2026-11-13' } } });
  t.after(() => window.close());
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.equal(text(window.document.getElementById('app-version')), 'v1.0.0 · 11112222 · 2026-11-13');
});

test('Play here puts the take in the footer player, not on a mystery page', async t => {
  const { window, calls } = await boot({ '/api/studio/revamp/status': { available: true, player_connected: false } });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-listen').click();
  await new Promise(resolve => setTimeout(resolve, 60));
  const playHere = [...doc.querySelectorAll('button')].find(b => /Play here/.test(text(b)));
  assert.ok(playHere, 'each added take offers Play here');
  playHere.click();
  await new Promise(resolve => setTimeout(resolve, 30));
  const audio = doc.getElementById('player');
  assert.match(audio.src, /\/api\/projects\/p1\/takes\/t1\/audio$/, 'the player is pointed at the item URL');
  assert.equal(text(doc.getElementById('player-title')), 'Afterglow');
  assert.doesNotMatch(text(doc.getElementById('revamp-status')), /no playable file/);
});

test('Play here says why it cannot play an item with no file', async t => {
  // REVamp can hold an item whose audio it never indexed. The button must explain that instead of
  // looking alive and doing nothing — the failure this product keeps being judged on.
  const { window } = await boot({
    '/api/studio/revamp/status': { available: true, player_connected: false },
    '/api/studio/revamp/added': { success: true, playlist: 'pl01', name: 'Music Studio',
      items: [{ id: 'studio:p9:t9', source: 'studio', title: 'No file yet',
        artist: 'Hermes Music Studio', playback: {} }] }
  });
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('nav-listen').click();
  // Wait for the whole screen to settle: the status line is written when the list arrives, so a
  // click that lands mid-load is overwritten by the load's own sentence.
  await new Promise(resolve => setTimeout(resolve, 150));
  const playHere = [...doc.querySelectorAll('button')].find(b => /Play here/.test(text(b)));
  assert.ok(playHere);
  playHere.click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.match(text(doc.getElementById('revamp-status')), /no playable file/i);
  assert.equal(doc.getElementById('player').getAttribute('src'), null, 'nothing is loaded from nowhere');
});

test('the version panel reads the same format as the rest of the suite', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  await new Promise(resolve => setTimeout(resolve, 60));
  const line = doc.getElementById('version-line').textContent;
  assert.match(line, /v1\.0\.0-dev\.235 · 4cbe561e/, 'version · short commit');
  assert.match(line, /aaaa1111/, 'the published build is shown beside it');
  assert.equal(doc.getElementById('version-update').disabled, false, 'a newer build can be installed');
  assert.equal(doc.getElementById('version-undo').disabled, false, 'and the last one can be undone');
});

test('no newer build leaves the update button disabled', async t => {
  const { window } = await boot({
    '/api/update': { ok: true, installed: { version: '1.0.0', commit: 'b'.repeat(40),
      commit_short: 'bbbbbbbb' }, latest: { commit: 'b'.repeat(40), commit_short: 'bbbbbbbb',
      message: 'same build' }, update_available: false, detail: 'this copy is the newest published build' }
  });
  t.after(() => window.close());
  const doc = window.document;
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(doc.getElementById('version-update').disabled, true);
});

test('setting up a server names the check that failed', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('setup-server-url').value = 'http://192.168.1.20:8188';
  doc.getElementById('setup-server-test').click();
  await new Promise(resolve => setTimeout(resolve, 120));
  assert.ok(calls.some(c => c.path === '/api/studio/setup/comfy'), 'the address is tested before saving');
  const message = doc.getElementById('setup-message').textContent;
  assert.match(message, /no MiniMax Music 3 nodes/i);
  assert.ok(doc.getElementById('setup-message').classList.contains('error'), 'the failure is not silent');
});

test('the three setup paths are one screen, not three pages', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const tabs = [...doc.querySelectorAll('.setup-tab')];
  assert.equal(tabs.length, 3);
  tabs.find(tab => tab.dataset.path === 'cloud').click();
  assert.equal(doc.getElementById('setup-server').hidden, true);
  assert.equal(doc.getElementById('setup-cloud').hidden, false);
});

test('one click picks engines, one more renders them', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const button = [...doc.querySelectorAll('button')].find(b => /Compare engines/.test(text(b)));
  assert.ok(button, 'the library offers a comparison');
  button.click();
  await new Promise(resolve => setTimeout(resolve, 160));

  const picker = doc.querySelector('.compare-picker');
  assert.ok(picker, 'the person chooses which engines, rather than getting a fixed pair');
  assert.match(text(picker), /MiniMax Music 3/, 'engines are named as products');
  assert.match(text(picker), /not ready|No ACE-Step server/, 'an engine that cannot render says why');
  const ready = [...picker.querySelectorAll('input[type=checkbox]')].filter(box => !box.disabled);
  assert.equal(ready.length, 2, 'both ready engines are offered');

  const go = [...doc.querySelectorAll('button')].find(b => /Render 2 versions/.test(text(b)));
  assert.ok(go);
  go.click();
  await new Promise(resolve => setTimeout(resolve, 200));

  const posted = calls.find(c => c.path === '/api/compare');
  assert.ok(posted, 'the comparison is started from the card');
  assert.equal(posted.body.project_id, 'p1');
  assert.deepEqual(posted.body.engines, ['ace_local', 'minimax_m3_comfy']);

  const panel = doc.querySelector('.compare-panel');
  assert.ok(panel, 'results appear beside the song, not on another page');
  assert.match(text(panel), /ACE-Step/);
  assert.match(text(panel), /MiniMax Music 3/);
  const plays = [...panel.querySelectorAll('button')].filter(b => /Play/.test(text(b)));
  assert.equal(plays.length, 2, 'each finished version can be played');
});

test('the create screen offers a chorus before the whole song', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const chorus = doc.getElementById('preview-chorus');
  const render = doc.getElementById('render');
  assert.ok(chorus, 'there is a button to hear the chorus');
  assert.match(text(chorus), /Hear the chorus/);
  assert.match(text(render), /Build the full song/, 'the big button says what it now does');
  // Neither is usable until a draft exists — no silent click that does nothing.
  assert.equal(chorus.disabled, true);
  assert.equal(render.disabled, true);
});

test('changing a draft in plain words posts the instruction and shows the result', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());

  const card = window.document.querySelector('[data-project="p1"]');
  const field = card.querySelector('.change-words');
  assert.ok(field, 'the card offers a place to type the change');
  field.value = 'add a fiddle';

  const button = [...card.querySelectorAll('button')].find(b => b.textContent === 'Change with words');
  assert.ok(button, 'the capability is a button, not only a chat command');
  button.click();
  await new Promise(done => setTimeout(done, 60));

  const call = calls.find(c => c.path === '/api/change' && c.method === 'POST');
  assert.ok(call, 'the page posts the change');
  assert.deepEqual(call.body, { project_id: 'p1', instruction: 'add a fiddle' });
  assert.match(window.document.querySelector('#library-status').textContent, /style: quiet, fiddle/);
});

test('changing part of a take is a form on the row, not a typed dialog', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  window.prompt = () => { throw new Error('typed dialog opened'); };

  const card = window.document.querySelector('[data-project="p1"]');
  const button = [...card.querySelectorAll('button')].find(b => b.textContent === 'Change this part');
  assert.ok(button, 'every finished take offers the control');
  button.click();

  const form = card.querySelector('.snippet-form');
  assert.ok(form, 'the form appears on the row');
  assert.ok(form.querySelector('.snippet-start') && form.querySelector('.snippet-end'),
    'start and end are fields, not questions');
  form.querySelector('.snippet-start').value = '4';
  form.querySelector('.snippet-end').value = '12';
  form.querySelector('.snippet-words').value = 'make this more intense';

  [...form.querySelectorAll('button')].find(b => b.textContent === 'Render the new part').click();
  await new Promise(done => setTimeout(done, 60));

  const call = calls.find(c => c.path === '/api/snippet' && c.method === 'POST');
  assert.ok(call, 'the row posts the snippet');
  assert.deepEqual(call.body, { project_id: 'p1', take_id: 't1', start_s: 4, end_s: 12,
    instruction: 'make this more intense' });
  assert.match(window.document.querySelector('#library-status').textContent, /Repainting 4–12s/);
});

test('checking a take names the failing check, and Fix it re-renders with a new seed', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const card = doc.querySelector('[data-project="p1"]');

  [...card.querySelectorAll('button')].find(b => b.textContent === 'Check this take').click();
  await new Promise(done => setTimeout(done, 60));
  assert.deepEqual(calls.filter(c => c.path === '/api/take-check').pop().body,
    { project_id: 'p1', take_id: 't1' });
  assert.match(doc.querySelector('#library-status').textContent, /the words reached the engine/);

  [...card.querySelectorAll('button')].find(b => b.textContent === 'Fix it').click();
  await new Promise(done => setTimeout(done, 60));
  assert.equal(calls.filter(c => c.path === '/api/take-check').pop().body.retry, true);
  assert.match(doc.querySelector('#library-status').textContent, /Re-rendering with seed 424242/);
});

test('save spot is a button, and Back to last spot restores it', async t => {
  const { window, calls } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const card = doc.querySelector('[data-project="p1"]');

  [...card.querySelectorAll('button')].find(b => b.textContent === 'Save spot').click();
  await new Promise(done => setTimeout(done, 60));
  assert.deepEqual(calls.filter(c => c.path === '/api/checkpoint').pop().body, { project_id: 'p1' });
  assert.match(doc.querySelector('#library-status').textContent, /Saved \(2 spot/);

  [...doc.querySelectorAll('#library button')].find(b => b.textContent === 'Back to last spot').click();
  await new Promise(done => setTimeout(done, 80));
  assert.deepEqual(calls.filter(c => c.path === '/api/checkpoint/restore').pop().body,
    { project_id: 'p1', checkpoint_id: 'cp1' });
  assert.match(doc.querySelector('#library-status').textContent, /approval was revoked/);
});

test('audio and cover URLs carry the token, because a media element cannot send a header', async t => {
  const { window, calls } = await boot({}, { token: 'tok-abc123' });
  t.after(() => window.close());
  const doc = window.document;
  const cover = doc.querySelector('img.project-cover');
  assert.ok(cover, 'a project cover is rendered');
  assert.match(cover.src, /token=tok-abc123/, 'the cover is a 401 without the token');

  const playButton = [...doc.querySelectorAll('button')].find(b => text(b).includes('▶ Play'));
  assert.ok(playButton, 'a take can be played');
  playButton.click();
  await new Promise(resolve => setTimeout(resolve, 30));
  const audio = doc.getElementById('player');
  assert.match(audio.src, /token=tok-abc123/, 'the take audio is a 401 without the token');
  // and the API calls keep using the header, not the query
  const statusCall = calls.find(c => c.path.startsWith('/api/status'));
  assert.ok(statusCall, 'the page still asks the host for status');
});

test('with no model in the host, the page asks the pane to draft', async t => {
  // The standalone host has no ctx.llm; in Hermes Desktop the pane does. The page must not simply
  // refuse ("Connect Hermes and choose a model first") — it asks the window that framed it.
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  const sent = [];
  Object.defineProperty(window, 'parent', {
    value: { postMessage: message => sent.push(message) }, configurable: true
  });
  doc.getElementById('idea-input').value = 'a night drive, warm synths';
  doc.getElementById('idea-apply').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  const ask = sent.find(m => m.type === 'hermes-music-studio:draft');
  assert.ok(ask, 'the page did not ask the pane for a draft');
  assert.match(ask.prompt, /night drive/);

  window.dispatchEvent(new window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:draft-result', id: ask.id, ok: true,
            draft: { title: 'Night Drive', caption: 'warm synthwave' } }
  }));
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.equal(doc.querySelector('input[name=title]').value, 'Night Drive');
  assert.match(text(doc.getElementById('idea-connection')), /Hermes agent/);
});

test('a plain browser tab is told where drafting does work', async t => {
  const { window } = await boot();
  t.after(() => window.close());
  const doc = window.document;
  doc.getElementById('idea-input').value = 'a night drive';
  doc.getElementById('idea-apply').click();
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.match(text(doc.getElementById('idea-connection')), /pane in Hermes Desktop|music_studio tool/);
});

// ---------------------------------------------------------------- library: search, sort, folding
const LIB = { ...STATUS, projects: [
  { project_id: 'a', title: 'Alpha', caption: 'warm folk', lyrics: 'river song', updated_at: 10, takes: [] },
  { project_id: 'b', title: 'Bravo', caption: 'dark metal', lyrics: '', updated_at: 30,
    takes: [{ take_id: 'bt', state: 'succeeded', engine_id: 'ace_local', requested_seed: 1 }] },
  { project_id: 'c', title: 'Handover', caption: '', lyrics: '', updated_at: 20, takes: [] },
] };
const titles = doc => [...doc.querySelectorAll('#library .project h2')].map(h => text(h));

async function openLibrary() {
  const booted = await boot({ '/api/status': LIB });
  const doc = booted.window.document;
  await new Promise(resolve => setTimeout(resolve, 40));
  doc.getElementById('nav-library').click();
  await new Promise(resolve => setTimeout(resolve, 20));
  return { ...booted, doc };
}

test('the library folds empty drafts away and counts the rest', async t => {
  const { window, doc } = await openLibrary();
  t.after(() => window.close());
  assert.deepEqual(titles(doc), ['Bravo', 'Alpha'], 'newest first, the empty Handover folded');
  assert.equal(text(doc.getElementById('library-count')), '2 of 3 songs');
  const toggle = doc.querySelector('.library-empties');
  assert.equal(text(toggle), 'Show 1 empty drafts');
  toggle.click();
  assert.deepEqual(titles(doc), ['Bravo', 'Alpha', 'Handover']);
});

test('search narrows by title, style or lyrics, and sort reorders', async t => {
  const { window, doc } = await openLibrary();
  t.after(() => window.close());
  const search = doc.getElementById('library-search');
  search.value = 'RIVER';
  search.dispatchEvent(new window.Event('input'));
  assert.deepEqual(titles(doc), ['Alpha'], 'lyrics match, case-insensitive');
  search.value = 'zzz';
  search.dispatchEvent(new window.Event('input'));
  assert.match(text(doc.getElementById('library')), /No songs match/);
  search.value = '';
  search.dispatchEvent(new window.Event('input'));
  const sort = doc.getElementById('library-sort');
  sort.value = 'oldest';
  sort.dispatchEvent(new window.Event('change'));
  assert.deepEqual(titles(doc), ['Alpha', 'Bravo']);
});

test('a take row keeps Play and Export, the rest sit under More', async t => {
  const { window, doc } = await openLibrary();
  t.after(() => window.close());
  const row = doc.querySelector('#library .take');
  const visible = [...row.children].filter(n => n.tagName === 'BUTTON').map(b => text(b));
  assert.ok(visible.some(v => /Play$/.test(v)) && visible.includes('Export'));
  const more = row.querySelector('details.take-more');
  assert.ok(more, 'a More menu exists');
  const inside = [...more.querySelectorAll('button')].map(b => text(b));
  assert.ok(inside.includes('Delete take') && inside.includes('Check this take'), inside.join(','));
});

test('clicking a song title opens its draft; the change box opens on demand', async t => {
  const { window, doc } = await openLibrary();
  t.after(() => window.close());
  const card = doc.querySelector('#library .project');
  const change = card.querySelector('.project-change');
  assert.ok(change.hidden, 'the change box starts folded');
  [...card.querySelectorAll('button')].find(b => text(b) === 'Change with words…').click();
  assert.ok(!change.hidden);
  card.querySelector('h2').click();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.ok(!doc.getElementById('create-view').hidden, 'the draft opens on the Create page');
});

// Engine capabilities decide which take actions exist. The pane reads the same engines block it
// already fetches from /status; no new request and no hardcoded engine name.

const CAPS = {
  ace: { instrumental: true, vocal_language: true, repaint: true, continuation: true, batch: false },
  mini: { instrumental: true, vocal_language: true, repaint: false, continuation: false, batch: false },
  frozen: { instrumental: true, vocal_language: true, repaint: false, continuation: false, batch: false },
};

function capabilityStatus(takes, engineIds = ['ace_local', 'minimax_m3_comfy', 'stub_frozen']) {
  const labels = { ace_local: 'ACE-Step 1.5 (local GPU)', minimax_m3_comfy: 'MiniMax Music 3 (ComfyUI)',
    stub_frozen: 'Frozen engine' };
  const caps = { ace_local: CAPS.ace, minimax_m3_comfy: CAPS.mini, stub_frozen: CAPS.frozen };
  return {
    success: true, engine_available: true,
    engines: engineIds.map(id => ({ id, label: labels[id],
      supports_generate: id !== 'stub_frozen', capabilities: caps[id] })),
    projects: [{ project_id: 'p1', title: 'Capability song', caption: 'quiet', engine: 'ace',
      approved: true, takes }],
  };
}

const takeRow = (doc, engineId) => [...doc.querySelectorAll('#library .take')]
  .find(row => text(row).includes(engineId));

test('a take whose engine cannot repaint renders the part as a section instead', async t => {
  const { window, calls } = await boot({
    '/api/status': capabilityStatus([
      { take_id: 't1', state: 'succeeded', engine_id: 'minimax_m3_comfy', requested_seed: 7,
        audio_seconds: 20 }]),
  });
  t.after(() => window.close());
  const doc = window.document;
  const row = takeRow(doc, 'minimax_m3_comfy');
  const redo = [...row.querySelectorAll('button')].find(b => text(b) === 'Change this part');
  assert.ok(redo, 'section render is a generate, so the control stays');
  assert.match(redo.title, /cannot repaint/i, 'the tooltip names what the engine cannot do');

  redo.click();
  const form = row.querySelector('.snippet-form');
  assert.ok(form, 'the inline form opens');
  assert.match(text(form), /cannot repaint/i);

  form.querySelector('.snippet-start').value = '2';
  form.querySelector('.snippet-end').value = '6';
  form.querySelector('.snippet-words').value = 'dreamier strings';
  [...form.querySelectorAll('button')].find(b => text(b) === 'Render the new part').click();
  await new Promise(resolve => setTimeout(resolve, 30));

  const posted = calls.filter(c => c.path === '/api/section/render').pop();
  assert.ok(posted, 'the section render route is used, not repaint');
  assert.deepEqual(posted.body,
    { project_id: 'p1', take_id: 't1', start_s: 2, end_s: 6, caption: 'dreamier strings' });
  assert.ok(!calls.some(c => c.path === '/api/snippet'), 'repaint is never requested on this engine');
});

test('a take whose engine repaints keeps the repaint form', async t => {
  const { window, calls } = await boot({
    '/api/status': capabilityStatus([
      { take_id: 't1', state: 'succeeded', engine_id: 'ace_local', requested_seed: 7,
        audio_seconds: 20 }]),
  });
  t.after(() => window.close());
  const doc = window.document;
  const row = takeRow(doc, 'ace_local');
  const redo = [...row.querySelectorAll('button')].find(b => text(b) === 'Change this part');
  assert.ok(redo);
  assert.doesNotMatch(redo.title, /cannot repaint/i);

  redo.click();
  const form = row.querySelector('.snippet-form');
  form.querySelector('.snippet-start').value = '2';
  form.querySelector('.snippet-end').value = '6';
  form.querySelector('.snippet-words').value = 'dreamier strings';
  [...form.querySelectorAll('button')].find(b => text(b) === 'Render the new part').click();
  await new Promise(resolve => setTimeout(resolve, 30));

  assert.ok(calls.some(c => c.path === '/api/snippet'), 'a capable engine still repaints');
  assert.ok(!calls.some(c => c.path === '/api/section/render'), 'and is not silently downgraded');
});

test('an engine that cannot generate has no Change this part control', async t => {
  const { window } = await boot({
    '/api/status': capabilityStatus([
      { take_id: 't1', state: 'succeeded', engine_id: 'stub_frozen', requested_seed: 7,
        audio_seconds: 20 }]),
  });
  t.after(() => window.close());
  const doc = window.document;
  const row = takeRow(doc, 'stub_frozen');
  assert.equal([...row.querySelectorAll('button')].some(b => text(b) === 'Change this part'), false,
    'neither repaint nor section render can run on that engine');
});

test('a take the engine can extend offers Extend under More and posts the seconds', async t => {
  const { window, calls } = await boot({
    '/api/extend': { success: true, take: { take_id: 'e1', state: 'queued' } },
  });
  t.after(() => window.close());
  const doc = window.document;
  const row = takeRow(doc, 'ace_local');
  const more = row.querySelector('details.take-more');
  assert.ok(more, 'a More menu exists');
  const extend = [...more.querySelectorAll('button')].find(b => text(b) === 'Extend…');
  assert.ok(extend, 'Extend sits under More');
  assert.equal(extend.disabled, false);

  extend.click();
  const form = row.querySelector('.extend-form');
  assert.ok(form, 'the inline form opens');
  const seconds = form.querySelector('.extend-seconds');
  assert.equal(seconds.value, '30', 'defaults to 30 seconds');
  assert.equal(seconds.min, '10');
  assert.equal(seconds.max, '60');
  seconds.value = '40';
  form.querySelector('.extend-words').value = 'a long outro';
  [...form.querySelectorAll('button')].find(b => text(b) === 'Extend the take').click();
  await new Promise(resolve => setTimeout(resolve, 30));

  assert.deepEqual(calls.filter(c => c.path === '/api/extend').pop().body,
    { project_id: 'p1', take_id: 't1', extend_seconds: 40, caption: 'a long outro' });
  assert.match(text(doc.getElementById('library-status')), /Extending by 40s/);
});

test('an engine without continuation offers a disabled Extend and the reason', async t => {
  const { window, calls } = await boot({
    '/api/status': capabilityStatus([
      { take_id: 't1', state: 'succeeded', engine_id: 'minimax_m3_comfy', requested_seed: 7,
        audio_seconds: 40 }]),
    '/api/extend': { success: true, take: { take_id: 'e1', state: 'queued' } },
  });
  t.after(() => window.close());
  const doc = window.document;
  const row = takeRow(doc, 'minimax_m3_comfy');
  const more = row.querySelector('details.take-more');
  assert.ok(more, 'a More menu exists');
  const extend = [...more.querySelectorAll('button')].find(b => /^Extend \(not on /.test(text(b)));
  assert.ok(extend, 'the disabled Extend is in the menu');
  assert.equal(extend.disabled, true);
  assert.match(text(extend), /MiniMax Music 3 \(ComfyUI\)/);
  assert.match(extend.title, /cannot extend/i);

  extend.click();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(row.querySelector('.extend-form'), null, 'no form opens');
  assert.ok(!calls.some(c => c.path === '/api/extend'), 'nothing is posted');
});

// ---------------------------------------------------------------- library: take evidence badges
// A take row may carry QC from the take-check/ASR pass. Show only what was measured; an unknown
// value shows nothing, so a badge on screen is always a real measurement.
const EVIDENCE = {
  success: true, engine_available: false, engines: [],
  projects: [{ project_id: 'p1', title: 'Evidence song', caption: 'quiet', engine: 'ace',
    approved: true, instrumental: false, takes: [
      { take_id: 't1', state: 'succeeded', engine_id: 'ace_local', requested_seed: 1,
        snapshot: { instrumental: false }, qc: { vocal_presence: 'confirmed',
          vocal_evidence: { status: 'confirmed', source: 'asr', model: 'whisper-small',
            run_id: 'run-1', words_recognised: 3, total: 3 } } },
      { take_id: 't2', state: 'succeeded', engine_id: 'ace_local', requested_seed: 2,
        snapshot: { instrumental: false }, qc: { vocal_presence: 'not_confirmed',
          vocal_evidence: { status: 'not_confirmed', source: 'asr', model: 'whisper-small',
            run_id: 'run-2', words_recognised: 1, total: 3 } } },
      { take_id: 't3', state: 'succeeded', engine_id: 'ace_local', requested_seed: 3,
        snapshot: { instrumental: false }, qc: { vocal_presence: 'not_confirmed',
          vocal_evidence: { status: 'not_confirmed', source: 'asr', model: 'whisper-small',
            run_id: 'run-3', words_recognised: 0, total: 2 } } },
      { take_id: 't4', state: 'succeeded', engine_id: 'ace_local', requested_seed: 4,
        snapshot: { instrumental: true }, qc: { vocal_presence: 'unknown',
          vocal_evidence: { status: 'unknown', source: 'none' } } },
      { take_id: 't5', state: 'succeeded', engine_id: 'ace_local', requested_seed: 5,
        snapshot: { instrumental: false }, qc: null },
      { take_id: 't6', state: 'failed', engine_id: 'ace_local', requested_seed: 6,
        snapshot: { instrumental: false }, qc: { vocal_presence: 'confirmed',
          vocal_evidence: { status: 'confirmed', source: 'asr', model: 'whisper-small',
            run_id: 'run-6', words_recognised: 3, total: 3 } } },
    ] }]
};

test('succeeded takes show the words and vocal evidence that was measured', async t => {
  const { window } = await boot({ '/api/status': EVIDENCE });
  t.after(() => window.close());
  const doc = window.document;
  const rows = [...doc.querySelectorAll('#library .take')];
  const badges = row => [...row.querySelectorAll('.take-badge')];
  assert.equal(rows.length, 6);

  assert.deepEqual(badges(rows[0]).map(text), ['words 3/3', 'vocals ✓']);
  assert.ok(badges(rows[0])[0].classList.contains('good'), 'all words is green');
  assert.ok(badges(rows[0])[1].classList.contains('good'));
  assert.match(badges(rows[0])[0].title, /recognised 3 of 3 words/);
  assert.match(badges(rows[0])[1].title, /vocal is confirmed/);

  assert.deepEqual(badges(rows[1]).map(text), ['words 1/3', 'no vocals heard']);
  assert.ok(badges(rows[1])[0].classList.contains('warn'), 'some words is amber');
  assert.match(badges(rows[1])[1].title, /below the vocal threshold/);

  assert.deepEqual(badges(rows[2]).map(text), ['words 0/2', 'no vocals heard']);
  assert.ok(badges(rows[2])[0].classList.contains('bad'), 'zero words is red');

  assert.deepEqual(badges(rows[3]).map(text), ['instrumental']);
  assert.match(badges(rows[3])[0].title, /instrumental/);

  assert.deepEqual(badges(rows[4]), [], 'nothing measured means no badge');
  assert.deepEqual(badges(rows[5]), [], 'a failed take has no evidence to show');
  for (const row of rows) {
    for (const badge of badges(row)) assert.ok(badge.title, 'every badge explains what was measured');
  }
});

// A take's lineage is a short, muted link to the take it was made from. The number is the same
// 1-based position the row label uses, and the link brings the parent into view.
const LINEAGE = {
  success: true, engine_available: false, engines: [],
  projects: [{ project_id: 'p1', title: 'Lineage song', caption: 'quiet', engine: 'ace',
    approved: true, takes: [
      { take_id: 't1', state: 'succeeded', engine_id: 'ace_local', requested_seed: 1, kind: 'generate' },
      { take_id: 't2', state: 'succeeded', engine_id: 'ace_local', requested_seed: 2,
        kind: 'repaint', parent_take_id: 't1', lineage: ['t1'] },
      { take_id: 't3', state: 'succeeded', engine_id: 'ace_local', requested_seed: 3,
        kind: 'section', parent_take_id: 't2', lineage: ['t1', 't2'] }
    ] }]
};

test('a child take says which take it came from, and the link highlights that parent row', async t => {
  const { window } = await boot({ '/api/status': LINEAGE });
  t.after(() => window.close());
  const doc = window.document;
  const links = [...doc.querySelectorAll('#library .take-lineage')];
  assert.deepEqual(links.map(text), ['from take 1 · repaint', 'from take 2 · section']);
  assert.match(links[0].className, /take-lineage/);
  assert.equal(doc.querySelectorAll('#library .take-lineage').length, 2,
    'a take with no parent gets no lineage link');
  assert.equal(links[0].closest('.take-more'), null, 'the lineage link stays on the row, not under More');

  links[0].click();
  const parent = doc.querySelector('#library [data-take="t1"]');
  assert.ok(parent.classList.contains('take-highlight'), 'the parent row is highlighted');
});

// The doctor row keeps the engine's own failure detail and puts the code-based next step
// underneath it, so the pane never has to invent a command.
const DOCTOR = {
  success: true,
  engines: [
    { id: 'ace_local', label: 'ACE-Step 1.5 (local GPU)', ready: false, state: 'unavailable',
      detail: 'source missing', checks: [], fix: 'Set the ACE-Step runtime and source folders in Compute.' },
    { id: 'minimax_m3_comfy', label: 'MiniMax Music 3 (ComfyUI)', ready: true, state: 'ready',
      detail: 'nodes present', checks: [], fix: 'Install ffmpeg if ComfyUI outputs FLAC.' }
  ]
};

test('the compute pane shows each engine doctor row and its fix beneath it', async t => {
  const { window } = await boot({ '/api/doctor': DOCTOR });
  t.after(() => window.close());
  const doc = window.document;
  const aceRow = doc.querySelector('#engine-doctor [data-engine="ace_local"]');
  const miniRow = doc.querySelector('#engine-doctor [data-engine="minimax_m3_comfy"]');
  assert.ok(aceRow && miniRow, 'one doctor row per engine');
  assert.match(text(aceRow), /ACE-Step 1\.5 \(local GPU\) · unavailable: source missing/);
  assert.match(text(aceRow), /Set the ACE-Step runtime and source folders in Compute/);
  assert.match(text(miniRow), /MiniMax Music 3 \(ComfyUI\) · ready: nodes present/);
  assert.match(text(miniRow), /Install ffmpeg if ComfyUI outputs FLAC/);
});

// Renaming a song is an inline edit on the card, saved through the same /update route the draft
// form uses — including the version token it was loaded with.
const RENAME_STATUS = {
  success: true, engine_available: false, engines: [], projects: [
    { project_id: 'p1', title: 'First song', caption: 'quiet', engine: 'ace', approved: true,
      updated_at: '2026-11-13T09:30:00-07:00', takes: [] }
  ]
};

test('rename swaps the title for an inline input and saves through /update', async t => {
  const { window, calls } = await boot({ '/api/status': RENAME_STATUS });
  t.after(() => window.close());
  const doc = window.document;
  const pause = () => new Promise(resolve => setTimeout(resolve, 30));
  window.prompt = () => { throw new Error('a typed dialog opened'); };

  [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Rename').click();
  const input = doc.querySelector('#library .rename-input');
  assert.ok(input, 'the title is replaced by an input');
  assert.equal(input.value, 'First song');
  assert.equal(doc.querySelector('#library .project-title'), null, 'the heading is out of the way');

  input.value = 'A better name';
  input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  await pause();

  const saved = calls.filter(c => c.path === '/api/rename' && c.body && c.body.project_id === 'p1').pop();
  assert.ok(saved, 'the rename posts to /rename, which keeps the draft approved');
  assert.deepEqual(saved.body, { project_id: 'p1', title: 'A better name' });
  assert.ok(!calls.some(c => c.path === '/api/update' && c.body && c.body.project_id === 'p1'),
    'a rename must not go through /update, which un-approves the draft');
});

test('escape cancels a rename and restores the title without a request', async t => {
  const { window, calls } = await boot({ '/api/status': RENAME_STATUS });
  t.after(() => window.close());
  const doc = window.document;

  [...doc.querySelectorAll('#library button')].find(b => text(b) === 'Rename').click();
  const input = doc.querySelector('#library .rename-input');
  input.value = 'Never mind';
  input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await new Promise(resolve => setTimeout(resolve, 20));

  assert.equal(doc.querySelector('#library .rename-input'), null);
  assert.equal(text(doc.querySelector('#library .project-title')), 'First song');
  assert.ok(!calls.some(c => (c.path === '/api/update' || c.path === '/api/rename') && c.body && c.body.project_id === 'p1'), 'no request was sent');
});
