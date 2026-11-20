// The REVamp menu: REVamp's own screen only when REVamp is installed, a page about getting it when it
// is not, and song lists that stay one line per song so hundreds remain easy to manage.
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

const pause = (ms = 80) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => (node && node.textContent) || '';

const ENGINES = [{ id: 'minimax_m3_comfy', label: 'MiniMax Music 3', kind: 'comfyui', available: true,
  supports_generate: true, selectable: true, selectable_reason: null,
  limits: { min_seconds: 10, max_seconds: 360, recommended_seconds: 120 },
  capabilities: { instrumental: true, vocal_language: true, repaint: false, continuation: true, batch: true } }];

function song(id, extra = {}) {
  return { project_id: id, title: 'Song ' + id, caption: 'folk', lyrics: 'la', engine: 'minimax_m3_comfy',
    approved: true, updated_at: 1763540000, takes: [], ...extra };
}

async function boot({ projects, revamp = { installed: false, available: false }, added = [] }) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const calls = [];
  const state = { projects: [...projects] };
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    calls.push({ path, method, body });
    const json = payload => ({ ok: true, status: 200, json: async () => payload });
    if (path === '/api/status') return json({ success: true, engines: ENGINES, projects: state.projects });
    if (path === '/api/studio/revamp/status') return json(revamp);
    if (path === '/api/studio/revamp/added') return json({ items: added });
    if (path === '/api/studio/radio') return json({ success: true, station: { state: 'stopped', songs: [] }, message: '' });
    if (path === '/api/delete') {
      state.projects = state.projects.filter(p => p.project_id !== body.project_id);
      return json({ success: true });
    }
    if (path === '/api/studio/engines/test') {
      if (body.kind === 'ace_server') {
        return json({ ok: false, kind: 'ace_server', url: body.url, problem: 'is_comfyui',
          message: 'This address is a ComfyUI, not an ACE-Step server.', checks: [] });
      }
      return json({ ok: true, kind: 'comfyui', url: body.url, message: 'This is ComfyUI. ACE-Step 1.5 is ready.',
        models: [{ id: 'ace15_turbo', label: 'ACE-Step 1.5', status: 'ready' }], checks: [] });
    }
    if (path === '/api/studio/engines' && method === 'GET') return json({ success: true, engines: [], ace_remote_url: '', hosted: [] });
    const table = {
      '/api/settings': { success: true, settings: { minimax: {} }, samplers: ['euler'], schedulers: ['simple'],
        library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
      '/api/presets': { success: true, presets: [] },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/hardware': { success: true, devices: [], pid_to_gpu: {} },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/about': { success: true, version: '1.0.0', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: null, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
      '/api/studio/setup/cloud': { success: true, endpoints: [] },
      '/api/studio/setup/hf': { success: true, status: { running: false, detail: 'idle' } },
    };
    const payload = table[path];
    return json(payload === undefined ? { success: true } : payload);
  };
  if (window.crypto && !window.crypto.getRandomValues) {
    Object.defineProperty(window.crypto, 'getRandomValues', { value: array => array, configurable: true });
  }
  window.eval(appjs);
  await pause();
  return { window, calls };
}


const nav = doc => doc.getElementById('nav-listen');

test('without REVamp the menu says Get REVamp and opens the page about it, never the REVamp screen', async t => {
  const { window } = await boot({ projects: [song('a')] });
  t.after(() => window.close());
  const doc = window.document;
  assert.equal(nav(doc).hidden, false);
  assert.equal(text(nav(doc).querySelector('span')), 'Get REVamp');
  nav(doc).click();
  await pause();
  assert.equal(doc.getElementById('listen-view').hidden, true, 'the REVamp screen stays hidden');
  assert.equal(doc.getElementById('promo-view').hidden, false);
  assert.match(text(doc.getElementById('promo-headline')), /\S/);
  assert.equal(doc.querySelectorAll('#promo-together li').length, 3);
  assert.equal(doc.querySelectorAll('#promo-features li').length, 5);
  assert.match(text(doc.getElementById('promo-command')), /^hermes plugins install KeyArgo\/hermes-revamp --enable$/);
});

test('Get REVamp asks the pane to open REVamp\'s page', async t => {
  const { window } = await boot({ projects: [song('a')] });
  t.after(() => window.close());
  const doc = window.document;
  const sent = [];
  // The page is framed in Hermes: links go to the pane, which owns openExternal.
  Object.defineProperty(window, 'parent', { value: { postMessage: m => sent.push(m) }, configurable: true });
  nav(doc).click(); await pause();
  doc.getElementById('promo-get').click();
  // The message is made in the page's own realm, so compare its contents, not its prototype.
  const message = sent.find(m => m.type === 'hermes-music-studio:open-external');
  assert.equal(JSON.stringify(message),
    JSON.stringify({ type: 'hermes-music-studio:open-external', url: 'https://github.com/KeyArgo/hermes-revamp' }));
});

test('with REVamp installed the menu says REVamp and opens its screen', async t => {
  const { window } = await boot({ projects: [song('a')], revamp: { installed: true, available: true } });
  t.after(() => window.close());
  const doc = window.document;
  assert.equal(text(nav(doc).querySelector('span')), 'REVamp');
  nav(doc).click();
  await pause(120);
  assert.equal(doc.getElementById('listen-view').hidden, false);
  assert.equal(doc.getElementById('promo-view').hidden, true);
});

test('an installed REVamp that is not answering still opens its screen, with the reason', async t => {
  const { window } = await boot({ projects: [song('a')],
    revamp: { installed: true, available: false, reason: 'REVamp is installed but not answering.' } });
  t.after(() => window.close());
  const doc = window.document;
  nav(doc).click();
  await pause(120);
  assert.equal(doc.getElementById('listen-view').hidden, false);
  assert.match(text(doc.getElementById('revamp-status')), /not answering/);
});

test('hundreds of songs are one line each, counted, and filterable', async t => {
  const added = Array.from({ length: 300 }, (_, i) => ({ id: 'i' + i, title: `Song ${i}`, artist: 'Hermes Music Studio',
    playback: { url: '/x' + i } }));
  const { window } = await boot({ projects: [song('a')], revamp: { installed: true, available: true }, added });
  t.after(() => window.close());
  const doc = window.document;
  nav(doc).click();
  await pause(150);
  const box = doc.getElementById('revamp-added');
  assert.equal(box.querySelectorAll('.compact-row').length, 300);
  assert.equal(box.querySelectorAll('article.project').length, 0, 'no big cards');
  assert.match(text(box.querySelector('.compact-head')), /300 songs/);
  const filter = box.querySelector('.compact-filter');
  assert.ok(filter, 'a long list gets a filter');
  filter.value = 'song 29';
  filter.dispatchEvent(new window.Event('input'));
  const shown = [...box.querySelectorAll('.compact-row')].filter(row => !row.hidden);
  assert.equal(shown.length, 11, 'Song 29 and Song 290-299');
  assert.match(text(box.querySelector('.compact-head')), /11 of 300 songs/);
});

test('a short list has no filter', async t => {
  const added = [{ id: 'one', title: 'Only song', playback: { url: '/x' } }];
  const { window } = await boot({ projects: [song('a')], revamp: { installed: true, available: true }, added });
  t.after(() => window.close());
  const doc = window.document;
  nav(doc).click();
  await pause(150);
  const box = doc.getElementById('revamp-added');
  assert.equal(box.querySelectorAll('.compact-row').length, 1);
  assert.equal(box.querySelector('.compact-filter'), null);
});

test('the Library offers Add to REVamp only when REVamp is installed', async t => {
  const take = { take_id: 't1', state: 'succeeded', engine_id: 'minimax_m3_comfy', created_at: 1763400000 };
  for (const [revamp, expected] of [[{ installed: false, available: false }, false], [{ installed: true, available: true }, true]]) {
    const { window } = await boot({ projects: [song('a', { takes: [take] })], revamp });
    const doc = window.document;
    doc.getElementById('nav-library').click();
    await pause(150);
    const has = [...doc.querySelectorAll('#library button')].some(b => text(b) === 'Add to REVamp');
    assert.equal(has, expected, `installed=${revamp.installed}`);
    window.close();
  }
});

test('a song deleted in Music Studio shows as Deleted, not as a Play button that fails', async t => {
  const { window } = await boot({ projects: [song('a')], revamp: { installed: true, available: true },
    added: [{ id: 'studio:a:t1', title: 'Gone song', missing: true, playback: { url: 'http://127.0.0.1:8794/api/projects/a/takes/t1/audio' } },
            { id: 'studio:b:t2', title: 'Here song', playback: { url: 'http://127.0.0.1:8794/api/projects/b/takes/t2/audio' } }] });
  t.after(() => window.close());
  const doc = window.document;
  nav(doc).click();
  await pause(150);
  const buttons = [...doc.querySelectorAll('#revamp-added button')];
  const deleted = buttons.find(b => text(b) === 'Deleted');
  assert.ok(deleted, 'the deleted song says Deleted');
  assert.equal(deleted.disabled, true);
  assert.ok(buttons.some(b => text(b) === '▶ Play here' && !b.disabled), 'the other song still plays');
  assert.match(text(doc.getElementById('revamp-added')), /Deleted in Music Studio/);
});

test('Play here plays the studio\'s own copy, never REVamp\'s link (REVamp\'s link 404s here)', async t => {
  const { window } = await boot({ projects: [song('a')], revamp: { installed: true, available: true },
    added: [{ id: 'studio:a:t1', title: 'Frostfire Chant',
      playback: { url: '/api/radio/stream?url=http%3A%2F%2F127.0.0.1%3A8775%2Fapi%2Fprojects%2Fa' } }] });
  t.after(() => window.close());
  const doc = window.document;
  nav(doc).click();
  await pause(150);
  [...doc.querySelectorAll('#revamp-added button')].find(b => text(b) === '▶ Play here').click();
  const src = doc.getElementById('player').getAttribute('src') || doc.getElementById('player').src;
  assert.match(src, /\/api\/projects\/a\/takes\/t1\/audio/);
  assert.doesNotMatch(src, /radio\/stream/);
});

test('an entry added the old way offers Fix for REVamp, which hands the take over again', async t => {
  const { window, calls } = await boot({ projects: [song('a')], revamp: { installed: true, available: true },
    added: [{ id: 'studio:a:t1', title: 'Frostfire Chant', relink: true, playback: { url: '/api/radio/stream?url=x' } }] });
  t.after(() => window.close());
  const doc = window.document;
  nav(doc).click();
  await pause(150);
  assert.match(text(doc.getElementById('revamp-added')), /Needs a fix to play in REVamp/);
  const fix = [...doc.querySelectorAll('#revamp-added button')].find(b => text(b) === 'Fix for REVamp');
  assert.ok(fix);
  fix.click();
  await pause(150);
  const sent = calls.find(c => c.path === '/api/studio/revamp/add-take');
  assert.equal(sent.body.project_id, 'a');
  assert.equal(sent.body.take_id, 't1');
});
