// Round 10: the pane must hand the frame's action to the route that owns the model.
//
// app.js asks for action 'chat' from "Discuss idea" and 'draft' from "Fill my draft". The pane
// dropped `action` from the forwarded body, so every request reached /studio/draft as a draft and
// "Discuss idea" answered with a draft instead of a chat reply.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

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
// jsdom ships no fetch; the pane asks the host one opaque question, so the default answer is yes.
const realFetch = globalThis.fetch;
globalThis.fetch = async () => ({ ok: true, status: 200, opaque: true, type: 'opaque' });

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost' });
globalThis.window = dom.window; globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement; globalThis.IS_REACT_ACT_ENVIRONMENT = true;
if (!globalThis.navigator) {
  globalThis.navigator = { userAgent: 'node.js' };
}
let testingLibrary;
try {
  testingLibrary = require('@testing-library/react');
} catch (error) {
  if (!process.env.TESTING_LIBRARY_PACKAGE) throw error;
  testingLibrary = createRequire(process.env.TESTING_LIBRARY_PACKAGE)('@testing-library/react');
}
const { render, waitFor, cleanup } = testingLibrary;

const reactUrl = pathToFileURL(require.resolve('react')).href;
const sdk = `import { createElement } from ${JSON.stringify(reactUrl)};
export const ROUTES_AREA="routes", SIDEBAR_NAV_AREA="sidebar.nav"; export const host={};
export function SandboxedFrame(props) { return createElement('iframe', props); }`;
let source = await readFile(new URL('../desktop/plugin.js', import.meta.url), 'utf8');
source = source.replaceAll("'@hermes/plugin-sdk'", JSON.stringify('data:text/javascript,' + encodeURIComponent(sdk)))
  .replaceAll("'react/jsx-runtime'", JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))
  .replaceAll("'react'", JSON.stringify(pathToFileURL(require.resolve('react')).href));
const plugin = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;

test.afterEach(() => {
  cleanup();
  globalThis.fetch = async () => ({ ok: true, status: 200, opaque: true, type: 'opaque' });
});

// Mount the pane, wait for the studio frame, then send the frame's draft message and answer it.
async function draftFromFrame(message) {
  const calls = [];
  const contributions = [];
  plugin.register({
    register: c => contributions.push(c),
    rest: async (path, options) => {
      calls.push({ path, options });
      if (path === '/studio/host') return { url: 'http://127.0.0.1:8775', token: 'tok' };
      return { draft: { title: 'Night Drive' }, content: 'Try a quiet bridge.' };
    }
  });
  render(contributions.find(c => c.area === 'routes').render());
  const frame = await waitFor(() => {
    const found = document.querySelector('iframe');
    assert.ok(found, 'the studio is framed');
    return found;
  });
  const posted = [];
  frame.contentWindow.postMessage = value => posted.push(value);
  window.dispatchEvent(new dom.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:draft', id: 'r10', prompt: 'a night drive', tone: 'warm',
            style: 70, ...message },
    source: frame.contentWindow
  }));
  await waitFor(() => assert.ok(posted.length, 'the frame was answered'));
  const call = calls.find(c => c.path === '/studio/draft');
  assert.ok(call, 'the request went to the route that owns the model');
  return { body: call.options.body, answer: posted[0] };
}

test("the frame's chat action reaches the route as chat", async () => {
  const { body, answer } = await draftFromFrame({ action: 'chat' });
  assert.equal(body.action, 'chat', 'a chat question was forwarded as a draft');
  assert.equal(body.prompt, 'a night drive');
  assert.equal(answer.ok, true);
  assert.equal(answer.content, 'Try a quiet bridge.');
});

test("the frame's draft action reaches the route as draft", async () => {
  const { body, answer } = await draftFromFrame({ action: 'draft' });
  assert.equal(body.action, 'draft');
  assert.equal(answer.draft.title, 'Night Drive');
});

test('an action other than chat or draft is sent as draft, never verbatim', async () => {
  for (const action of ['delete', 'CHAT', '', 3, null]) {
    const { body } = await draftFromFrame({ action });
    assert.notEqual(body.action, action, `the pane forwarded ${JSON.stringify(action)}`);
    assert.ok(body.action === 'draft' || body.action === undefined,
      `unexpected action ${JSON.stringify(body.action)}`);
    cleanup();
  }
});

test('a frame message with no action still drafts', async () => {
  const { body } = await draftFromFrame({});
  assert.ok(body.action === 'draft' || body.action === undefined);
  assert.equal(body.prompt, 'a night drive');
});
