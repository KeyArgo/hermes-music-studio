// Music engines page UI tests - check HTML structure and key elements.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const html = await readFile(new URL('../standalone/static/index.html', import.meta.url), 'utf8');

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
