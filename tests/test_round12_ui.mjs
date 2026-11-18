// Round 12: a slow draft is not thrown away, "Discuss idea" asks for a chat reply, and the
// right-click menu keeps the person's text and focus.
//
// Four separate promises, all of them about the page giving up on something too early:
//
//  * the draft request waited 60 s while the plugin waits 120 s for the model, so a 62 s draft died;
//  * the posted message dropped `action`, so "Discuss idea" was answered with a filled form;
//  * Copy of a page selection copied nothing, because focusing the menu's first item clears it;
//  * a menu dismissed by clicking elsewhere pulled the focus back to the field it was opened on;
//  * a read-only field got no menu at all, and a keyboard-opened menu landed in the window corner.
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

const pause = (ms = 40) => new Promise(resolve => setTimeout(resolve, ms));
const text = node => ((node && node.textContent) || '').trim();

async function boot() {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  const timers = [];
  const nativeSetTimeout = window.setTimeout.bind(window);
  // The draft's own deadline is a setTimeout the page registers: recording the delays is how the
  // test sees how long the page is willing to wait.
  window.setTimeout = (fn, delay, ...args) => {
    timers.push(delay);
    return nativeSetTimeout(fn, delay, ...args);
  };
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
  return { dom, window, timers, doc: window.document };
}

function rightClick(booted, node, x = 40, y = 30) {
  return node.dispatchEvent(new booted.window.MouseEvent('contextmenu',
    { bubbles: true, cancelable: true, clientX: x, clientY: y }));
}

const items = doc => [...doc.querySelectorAll('#context-menu [role="menuitem"]')].map(text);
const pick = (doc, label) => [...doc.querySelectorAll('#context-menu [role="menuitem"]')]
  .find(item => text(item) === label);

function selectParagraph(booted) {
  const paragraph = booted.doc.querySelector('#idea-messages p');
  assert.ok(paragraph, 'the conversation has text');
  const range = booted.doc.createRange();
  range.selectNodeContents(paragraph);
  booted.window.getSelection().removeAllRanges();
  booted.window.getSelection().addRange(range);
  return paragraph;
}

// A frame with a parent to answer: without one paneDraft refuses before it posts anything.
function framed(booted) {
  const sent = [];
  Object.defineProperty(booted.window, 'parent', {
    value: { postMessage: message => sent.push(message) }, configurable: true
  });
  return sent;
}

// ------------------------------------------------------------------ the draft request

test('the page waits longer for a draft than the plugin does', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  framed(booted);
  booted.doc.getElementById('idea-input').value = 'a night drive, warm synths';

  booted.doc.getElementById('idea-send').click();
  await pause(60);

  // music_studio/llm_bridge.py waits 120 s for the model. A page that gives up before that throws
  // away a draft that was still being written.
  const longest = booted.timers.length ? Math.max(...booted.timers) : 0;
  assert.ok(longest >= 150000, `the draft deadline is ${longest} ms, which is not above the plugin's 120 s`);
});

test('"Discuss idea" asks the pane for a chat reply', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const sent = framed(booted);
  booted.doc.getElementById('idea-input').value = 'a night drive, warm synths';

  booted.doc.getElementById('idea-send').click();
  await pause(60);

  const ask = sent.find(m => m.type === 'hermes-music-studio:draft');
  assert.ok(ask, 'the pane is asked to draft');
  // The pane turns this into /studio/draft's `action`; without it every answer was a draft.
  assert.equal(ask.action, 'chat');
});

test('"Fill my draft" still asks for a draft', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const sent = framed(booted);
  booted.doc.getElementById('idea-input').value = 'a night drive, warm synths';

  // "Fill my draft": the button's id is the page's, looked up rather than guessed from its label.
  const apply = booted.doc.getElementById('idea-apply');
  assert.ok(apply, 'the Fill my draft button exists');
  apply.click();
  await pause(60);

  const ask = sent.find(m => m.type === 'hermes-music-studio:draft');
  assert.ok(ask, 'the pane is asked to draft');
  assert.equal(ask.action, 'draft');
});

// ------------------------------------------------------------------ Copy of a page selection

test('Copy copies a page selection after the menu took it away', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const { doc, window } = booted;
  const paragraph = selectParagraph(booted);

  rightClick(booted, paragraph);

  // A real browser clears the page selection when the menu's first item takes the focus. Doing it
  // here is what makes this test about the fix rather than about jsdom's focus handling.
  window.getSelection().removeAllRanges();
  let copied = null;
  doc.execCommand = () => { copied = window.getSelection().toString(); return true; };

  pick(doc, 'Copy').click();
  await pause(30);

  assert.ok(copied && copied.includes(text(paragraph)),
    `Copy read "${copied}", not the paragraph the person selected`);
  assert.equal(doc.getElementById('page-hint'), null, 'Copy worked, so no fallback sentence');
});

// ------------------------------------------------------------------ closing the menu

test('a menu dismissed by clicking elsewhere does not pull the focus back', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const { doc } = booted;
  const area = doc.getElementById('idea-input');
  area.value = 'a late-night drive';
  area.setSelectionRange(0, 4);
  area.focus();

  rightClick(booted, area);
  assert.ok(doc.getElementById('context-menu'), 'the menu opened over the field');

  doc.getElementById('idea-messages').dispatchEvent(
    new booted.window.MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: 5, clientY: 5 }));
  await pause(20);

  assert.equal(doc.getElementById('context-menu'), null, 'the menu closed');
  assert.notEqual(doc.activeElement, area,
    'the click was meant for somewhere else; the focus must not go back to the field');
});

test('Escape closes the menu and gives the caret back, and Tab does not', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const { doc } = booted;
  const area = doc.getElementById('idea-input');
  area.value = 'a late-night drive';
  area.setSelectionRange(0, 4);
  area.focus();

  rightClick(booted, area);
  doc.dispatchEvent(new booted.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await pause(20);
  assert.equal(doc.getElementById('context-menu'), null, 'Escape closes the menu');
  assert.equal(doc.activeElement, area, 'Escape leaves the caret where the person was working');

  rightClick(booted, area);
  doc.dispatchEvent(new booted.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
  await pause(20);
  assert.equal(doc.getElementById('context-menu'), null, 'Tab closes the menu');
  assert.notEqual(doc.activeElement, area, 'Tab moves on; it does not come back to the field');
});

// ------------------------------------------------------------------ read-only fields

test('a read-only field offers Copy and Select all, never Cut or Paste', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const { doc } = booted;
  // What "Copy engines" shows when the clipboard cannot be written: read-only, and selected so it
  // can be copied by hand. That is the whole point of the box, so it needs the menu.
  const fallback = doc.getElementById('copy-engines-fallback');
  assert.ok(fallback, 'the engines fallback box exists');
  assert.equal(fallback.readOnly, true, 'and it is read-only');
  fallback.hidden = false;
  fallback.value = '[engine]\nname = ace';
  fallback.setSelectionRange(0, 8);

  rightClick(booted, fallback);

  assert.deepEqual(items(doc), ['Copy', 'Select all']);
});

// ------------------------------------------------------------------ keyboard-opened menus

test('a menu opened from the keyboard opens at the text, not in the corner', async t => {
  const booted = await boot();
  t.after(() => booted.window.close());
  const { doc } = booted;
  const paragraph = selectParagraph(booted);
  // Shift+F10 and the ContextMenu key report no pointer position at all.
  paragraph.getBoundingClientRect = () => ({ left: 120, top: 240, right: 320, bottom: 262, width: 200, height: 22 });

  rightClick(booted, paragraph, 0, 0);

  const menu = doc.getElementById('context-menu');
  assert.ok(menu, 'the keyboard can open the menu');
  assert.ok(parseFloat(menu.style.left) >= 100, `left is ${menu.style.left}, i.e. the window edge`);
  assert.ok(parseFloat(menu.style.top) >= 200, `top is ${menu.style.top}, i.e. the window edge`);
});
