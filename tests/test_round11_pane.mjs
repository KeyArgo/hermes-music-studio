// Round 11: "Fill my draft" must say what went wrong, not show raw JSON.
//
// The route answers a model failure with `{detail: "Hermes' model did not answer within 120
// seconds…"}`, and ctx.rest throws it as `502: {"detail":"…"}`. The pane recognised only the
// trust-refusal body ({error, commands}), so the person read a status line and a JSON blob.
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

// Mount the pane, wait for the frame, then send a draft message the route fails.
const draftCalls = [];
async function failedDraft(err) {
  const contributions = [];
  plugin.register({
    register: c => contributions.push(c),
    rest: async (path, opts) => {
      if (path === '/studio/host') return { url: 'http://127.0.0.1:8775', token: 'tok' };
      draftCalls.push({ path, opts });
      throw err;
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
    data: { type: 'hermes-music-studio:draft', id: 'r11', prompt: 'a night drive', tone: 'warm',
            style: 70, action: 'draft' },
    source: frame.contentWindow
  }));
  await waitFor(() => assert.ok(posted.length, 'the frame was answered'));
  return posted[0];
}

test("a 502 body's detail sentence is what the person reads", async () => {
  const sentence = "Hermes' model did not answer within 120 seconds. Try again, or pick a faster model.";
  const answer = await failedDraft(new Error('502: ' + JSON.stringify({ detail: sentence })));
  assert.equal(answer.ok, false);
  assert.equal(answer.error, sentence);
  assert.equal(answer.commands, undefined, 'a plain refusal carries no commands');
});

test('the trust refusal still wins, with its commands', async () => {
  const answer = await failedDraft(new Error('403: ' + JSON.stringify({
    error: 'Hermes refused the chosen provider or model.', commands: ['hermes config set a b']
  })));
  assert.equal(answer.ok, false);
  assert.equal(answer.error, 'Hermes refused the chosen provider or model.');
  assert.deepEqual(answer.commands, ['hermes config set a b']);
});

test('a failure with no JSON body is left as the host said it', async () => {
  const answer = await failedDraft(new Error('hermes.api error 500: internal server error'));
  assert.equal(answer.ok, false);
  assert.equal(answer.error, 'hermes.api error 500: internal server error');
});

test('a draft asks Desktop to wait longer than the model may take', async () => {
  // Desktop gives a plugin request 30 s by default; on levy9 a draft failed as "Timed out connecting
  // to Hermes backend after 30000ms" while the model was still writing (it may take 120 s).
  draftCalls.length = 0;
  await failedDraft(new Error('502: {"detail":"x"}'));
  const call = draftCalls.find(c => c.path === '/studio/draft');
  assert.ok(call, 'the draft went to the plugin route');
  assert.ok(call.opts && call.opts.timeoutMs > 120000,
    `timeoutMs ${call.opts && call.opts.timeoutMs} must exceed the model's 120 s`);
});
