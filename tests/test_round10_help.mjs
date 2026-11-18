// Round 10 (lane help): the "?" buttons open their help text where it is needed, and the
// controls that had no help at all now carry a title. Every test below failed on the code
// before its fix (the buttons were inert and 34 controls had no help).
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

const pause = (ms = 50) => new Promise(resolve => setTimeout(resolve, ms));
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
  await pause(80);
  return dom;
}

function iconFor(doc, describedby) {
  const icon = [...doc.querySelectorAll('.help-icon')]
    .find(button => button.getAttribute('aria-describedby') === describedby);
  assert.ok(icon, 'a ? button describes ' + describedby);
  return icon;
}

const popovers = doc => [...doc.querySelectorAll('.help-popover')];

// ------------------------------------------------------------------ every ? has its text

test('every question mark has a title and matching help text', () => {
  const doc = new JSDOM(html).window.document;
  const icons = [...doc.querySelectorAll('.help-icon')];
  assert.equal(icons.length, 17, 'the 17 ? buttons are still here');
  for (const icon of icons) {
    assert.ok((icon.getAttribute('title') || '').trim(), 'title on ' + icon.outerHTML.slice(0, 80));
    const ids = (icon.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
    assert.ok(ids.length, 'aria-describedby on ' + icon.outerHTML.slice(0, 80));
    const said = ids.map(id => {
      const node = doc.getElementById(id);
      assert.ok(node, 'described element ' + id + ' exists');
      return text(node);
    }).join(' ');
    assert.ok(said.length > 0, 'help text for ' + ids.join(' '));
  }
});

// ------------------------------------------------- controls that had no help now have it

const HELPED = {
  ids: ['simple-mode', 'custom-mode', 'engine-select', 'workspace-select', 'render-takes',
    'preset-select', 'preset-save', 'idea-provider', 'idea-model', 'idea-tone', 'idea-style',
    'test-server-btn', 'add-engine-btn', 'settings-steps', 'settings-cfg', 'settings-cfg-scale',
    'settings-top-k', 'settings-sampler', 'settings-scheduler', 'settings-scan-hosts',
    'settings-export-dir', 'workflow-file', 'workflow-target'],
  names: ['title', 'caption', 'lyrics', 'instrumental', 'bpm', 'keyscale', 'vocal_language',
    'duration_seconds'],
};

test('the controls that had no help now carry a short title', () => {
  const doc = new JSDOM(html).window.document;
  const said = [];
  for (const id of HELPED.ids) {
    const node = doc.getElementById(id);
    assert.ok(node, id + ' exists in index.html');
    said.push([id, node.getAttribute('title')]);
  }
  for (const name of HELPED.names) {
    const node = doc.querySelector('[name="' + name + '"]');
    assert.ok(node, name + ' exists in index.html');
    said.push([name, node.getAttribute('title')]);
  }
  for (const [label, title] of said) {
    assert.ok((title || '').trim(), label + ' has a title');
    assert.ok(title.length <= 140, label + ' title is short (' + title.length + ' chars)');
  }
});

// ------------------------------------------------------------------ the popover itself

test('a question mark opens its help beside the button, and pressing it again closes it', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const icon = iconFor(doc, 'help-addr');
  const expected = text(doc.getElementById('help-addr'));

  icon.click();
  await pause(20);
  const open = popovers(doc);
  assert.equal(open.length, 1, 'one help box is open');
  assert.equal(text(open[0]), expected, 'it shows the text the button points at');
  assert.equal(open[0].classList.contains('help-popover'), true);
  assert.equal(icon.getAttribute('aria-expanded'), 'true');
  assert.equal(open[0].parentNode, doc.body, 'the box sits in the page, not inside the label');

  icon.click();
  await pause(20);
  assert.equal(popovers(doc).length, 0, 'the second press closes it');
  assert.equal(icon.getAttribute('aria-expanded'), 'false');
});

test('Escape closes the help and puts the cursor back on the button', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const icon = iconFor(doc, 'help-name');
  icon.click();
  await pause(20);
  assert.equal(popovers(doc).length, 1);

  doc.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await pause(20);
  assert.equal(popovers(doc).length, 0, 'Escape closes it');
  assert.equal(icon.getAttribute('aria-expanded'), 'false');
  assert.equal(doc.activeElement, icon, 'focus returns to the ? button');
});

test('a click somewhere else closes the help', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  iconFor(doc, 'help-addr').click();
  await pause(20);
  assert.equal(popovers(doc).length, 1);

  doc.querySelector('[name="title"]').click();
  await pause(20);
  assert.equal(popovers(doc).length, 0);
});

test('only one help box is open at a time', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const first = iconFor(doc, 'help-name');
  const second = iconFor(doc, 'help-model');

  first.click();
  await pause(20);
  second.click();
  await pause(20);

  const open = popovers(doc);
  assert.equal(open.length, 1, 'one box, not two');
  assert.equal(text(open[0]), text(doc.getElementById('help-model')));
  assert.equal(first.getAttribute('aria-expanded'), 'false');
  assert.equal(second.getAttribute('aria-expanded'), 'true');
});

test('Enter and Space open and close the help from the keyboard', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const icon = iconFor(doc, 'help-key');

  icon.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await pause(20);
  assert.equal(popovers(doc).length, 1, 'Enter opens it');
  assert.equal(icon.getAttribute('aria-expanded'), 'true');

  icon.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
  await pause(20);
  assert.equal(popovers(doc).length, 0, 'Space closes it');
  assert.equal(icon.getAttribute('aria-expanded'), 'false');
});

test('a question mark added after the page loaded opens too', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  const row = doc.createElement('div');
  row.className = 'control-row';
  const icon = doc.createElement('button');
  icon.type = 'button';
  icon.className = 'help-icon';
  icon.textContent = '?';
  icon.setAttribute('aria-describedby', 'help-later');
  const note = doc.createElement('span');
  note.id = 'help-later';
  note.className = 'sr-only';
  note.textContent = 'Added by a later render.';
  row.append(icon, note);
  doc.body.append(row);

  icon.click();
  await pause(20);
  const open = popovers(doc);
  assert.equal(open.length, 1);
  assert.equal(text(open[0]), 'Added by a later render.');
});

// ------------------------------------------------------------------ narrow screens

test('the help box stays inside a 320px wide window', async t => {
  const dom = await boot();
  t.after(() => dom.window.close());
  const doc = dom.window.document;
  Object.defineProperty(dom.window, 'innerWidth', { value: 320, configurable: true, writable: true });
  const right = iconFor(doc, 'help-models');
  // A button near the right edge would put an unclamped box off the screen.
  right.getBoundingClientRect = () => ({ left: 318, right: 342, top: 10, bottom: 34, width: 24, height: 24 });
  right.click();
  await pause(20);
  const box = popovers(doc)[0];
  assert.ok(box, 'the box opened');
  const left = parseFloat(box.style.left);
  const width = parseFloat(box.style.maxWidth);
  assert.ok(left >= 8, 'it does not hang off the left edge (' + left + ')');
  assert.ok(left + width <= 320 - 8 + 0.5, 'it does not hang off the right edge (' + (left + width) + ')');
});

test('the help box is styled in the app colours with no fixed width', () => {
  const rule = (css.match(/\.help-popover\s*\{[^}]*\}/) || [''])[0];
  assert.ok(rule, 'there is a .help-popover rule');
  assert.match(rule, /position:\s*fixed/);
  assert.match(rule, /max-width:[^;]*100vw/, 'it can shrink to the window');
  assert.match(rule, /background:\s*var\(--panel/, 'it follows the app background');
  assert.match(rule, /color:\s*var\(--ink/, 'it follows the app text colour');
  assert.match(rule, /border:[^;]*var\(--line/, 'it follows the app border colour');
  assert.equal(/width:\s*\d+px;/.test(rule), false, 'no fixed width');
});
