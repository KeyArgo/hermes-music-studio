// Round 11: right-click inside the studio shows a menu, so text can be copied and pasted.
//
// The studio runs in a sandboxed frame inside Hermes Desktop. The desktop's right-click menu belongs
// to the outer window, so a right-click in the studio showed nothing at all. The page now carries its
// own small menu over editable fields and real selections.
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

const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => ((node && node.textContent) || '').trim();

async function boot() {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  window.HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
  window.HTMLMediaElement.prototype.load = function () {};
  window.fetch = async url => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    const table = {
      '/api/status': { success: true, engines: [], projects: [] },
      '/api/presets': { success: true, presets: [] },
      '/api/settings': { success: true, settings: { minimax: {} }, samplers: ['euler'], schedulers: ['simple'],
        library: { default: '/lib', active: '/lib', moved: false, projects: 0, audio_files: 0, bytes: 0 } },
      '/api/studio/engines': { success: true, engines: [] },
      '/api/studio/workflows': { success: true, workflows: [] },
      '/api/studio/targets': { success: true, targets: [] },
      '/api/studio/workspaces': { success: true, workspaces: [] },
      '/api/studio/revamp/status': { detail: 'REVamp is unavailable.' },
      '/api/studio/models': { success: true, providers: [], provider: null, model: null },
      '/api/about': { success: true, version: '0.2.5', build: {}, repo: '', paths: {}, revamp: {} },
      '/api/update': { ok: true, installed: {}, latest: {}, update_available: false },
      '/api/discover': { success: true, found: [], tested: 1 },
      '/api/doctor': { success: true, engines: [] },
    };
    const payload = table[path];
    return { ok: payload !== undefined, status: payload === undefined ? 404 : 200,
      json: async () => payload ?? { detail: 'Not Found' } };
  };
  window.eval(appjs);
  await pause(90);
  return dom;
}

function rightClick(dom, node, x = 40, y = 30) {
  return node.dispatchEvent(new dom.window.MouseEvent('contextmenu',
    { bubbles: true, cancelable: true, clientX: x, clientY: y }));
}

const items = doc => [...doc.querySelectorAll('#context-menu [role="menuitem"]')].map(text);

// ------------------------------------------------------------------ the menu over a text field

test('right-clicking a textarea offers Cut, Copy, Paste and Select all', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const area = doc.getElementById('idea-input');
  area.value = 'a late-night drive with warm synths';
  area.setSelectionRange(2, 14);

  const notCancelled = rightClick(dom, area);
  assert.equal(notCancelled, false, 'the page claims the right-click, so no menu-less click');

  const menu = doc.getElementById('context-menu');
  assert.ok(menu, 'a menu appeared');
  assert.equal(menu.getAttribute('role'), 'menu');
  assert.deepEqual(items(doc), ['Cut', 'Copy', 'Paste', 'Select all']);

  assert.ok(parseFloat(menu.style.left) >= 8, 'it does not hang off the left edge');
  assert.ok(parseFloat(menu.style.top) >= 8, 'it does not hang off the top edge');
});

test('a right-click without a selection offers Paste and Select all, not Copy', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const area = doc.getElementById('idea-input');
  area.value = 'no selection here';
  area.setSelectionRange(4, 4);

  rightClick(dom, area);
  assert.deepEqual(items(doc), ['Paste', 'Select all']);
});

test('a slider is not a text field, so it keeps the ordinary right-click', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const slider = doc.getElementById('idea-style');
  assert.equal(rightClick(dom, slider), true, 'the event is left alone over a range input');
  assert.equal(doc.getElementById('context-menu'), null);
});

// ------------------------------------------------------------------ paste

test('a clipboard that cannot be read says how to paste instead', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const area = doc.getElementById('idea-input');
  area.value = '';
  area.setSelectionRange(0, 0);
  Object.defineProperty(dom.window.navigator, 'clipboard', {
    configurable: true,
    value: { readText: () => Promise.reject(new Error('not allowed')) }
  });

  rightClick(dom, area);
  [...doc.querySelectorAll('#context-menu [role="menuitem"]')]
    .find(item => text(item) === 'Paste').click();
  await pause(80);

  const hint = doc.getElementById('page-hint');
  assert.ok(hint, 'the fallback sentence is shown');
  assert.equal(text(hint), 'Press Ctrl+V to paste (Cmd+V on a Mac).');
});

test('a clipboard that reads inserts the text at the caret', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const area = doc.getElementById('idea-input');
  area.value = 'start  end';
  area.setSelectionRange(6, 6);
  Object.defineProperty(dom.window.navigator, 'clipboard', {
    configurable: true,
    value: { readText: async () => 'PASTED' }
  });

  rightClick(dom, area);
  [...doc.querySelectorAll('#context-menu [role="menuitem"]')]
    .find(item => text(item) === 'Paste').click();
  await pause(80);

  assert.equal(area.value, 'start PASTED end');
  assert.equal(doc.getElementById('page-hint'), null, 'no fallback sentence was needed');
});

// ------------------------------------------------------------------ select all and closing

test('Select all over the conversation selects the block it was opened on', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const messages = doc.getElementById('idea-messages');
  const paragraph = messages.querySelector('p');
  assert.ok(paragraph, 'the conversation has text');

  // A selection is what makes the menu appear over plain text.
  const range = doc.createRange();
  range.selectNodeContents(paragraph);
  dom.window.getSelection().removeAllRanges();
  dom.window.getSelection().addRange(range);

  rightClick(dom, paragraph);
  [...doc.querySelectorAll('#context-menu [role="menuitem"]')]
    .find(item => text(item) === 'Select all').click();
  await pause(30);

  assert.ok(dom.window.getSelection().toString().includes(text(paragraph)),
    'the whole block is selected, not just the word under the pointer');
  assert.equal(doc.getElementById('context-menu'), null, 'the menu closes after an action');
});

test('Escape closes the menu', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  rightClick(dom, doc.getElementById('idea-input'));
  assert.ok(doc.getElementById('context-menu'));

  doc.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await pause(20);
  assert.equal(doc.getElementById('context-menu'), null);
});

test('a right-click on the page with no selection shows no menu at all', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  dom.window.getSelection().removeAllRanges();
  const main = doc.querySelector('main') || doc.body;

  assert.equal(rightClick(dom, main), true, 'the event is not cancelled');
  assert.equal(doc.getElementById('context-menu'), null);
});

// ------------------------------------------------------------------ the styling that makes it usable

test('the conversation text stays selectable', () => {
  const rule = (css.match(/\.idea-messages[^{]*\{[^}]*user-select[^}]*\}/) || [''])[0];
  assert.ok(rule, 'a .idea-messages rule sets user-select');
  assert.match(rule, /user-select:\s*text/, 'the pane must not disable selecting the chat');
});

test('the menu is styled in the app colours and inside the window', () => {
  const rule = (css.match(/\.context-menu\s*\{[^}]*\}/) || [''])[0];
  assert.ok(rule, 'there is a .context-menu rule');
  assert.match(rule, /position:\s*fixed/);
  assert.match(rule, /background:\s*var\(--panel/, 'it follows the app background');
  assert.match(rule, /color:\s*var\(--ink/, 'it follows the app text colour');
  assert.match(rule, /border:\s*1px solid var\(--line/, 'it follows the app border colour');
});
