// A Desktop older than plugin RPC builds its SDK module without a `host` export. Desktop makes that
// module from the names it has, so a pane that imported `host` by name would fail to load at all.
// These tests load the pane against such an SDK: it must still load, and a draft must use the route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (error) {
  const elsewhere = process.env.JSDOM_PACKAGE
    || require('path').join(require('os').homedir(), '.hermes/hermes-agent/apps/desktop/package.json');
  ({ JSDOM } = createRequire(elsewhere)('jsdom'));
}
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

// No `host` here: the SDK of a Desktop that predates plugin RPC.
const reactUrl = pathToFileURL(require.resolve('react')).href;
const sdk = `import { createElement } from ${JSON.stringify(reactUrl)};
export const ROUTES_AREA="routes", SIDEBAR_NAV_AREA="sidebar.nav";
export function SandboxedFrame(props) { return createElement('iframe', props); }`;
let source = await readFile(new URL('../desktop/plugin.js', import.meta.url), 'utf8');
source = source.replaceAll("'@hermes/plugin-sdk'", JSON.stringify('data:text/javascript,' + encodeURIComponent(sdk)))
  .replaceAll("'react/jsx-runtime'", JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))
  .replaceAll("'react'", JSON.stringify(pathToFileURL(require.resolve('react')).href));
const plugin = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;

test.afterEach(cleanup);

test('the pane loads on a Desktop whose SDK has no host', () => {
  const contributions = [];
  plugin.register({ register: c => contributions.push(c) });
  assert.ok(contributions.some(c => c.area === 'routes'));
});

test('without host.request a draft goes to the route', async () => {
  const calls = [];
  const contributions = [];
  plugin.register({
    register: c => contributions.push(c),
    rest: async (path, opts) => {
      calls.push({ path, opts });
      return path === '/studio/host' ? { url: 'http://127.0.0.1:8775' } : { draft: { title: 'Route' } };
    }
  });
  render(contributions.find(c => c.area === 'routes').render());
  const frame = await waitFor(() => { const f = document.querySelector('iframe'); assert.ok(f); return f; });
  const posted = [];
  frame.contentWindow.postMessage = value => posted.push(value);
  window.dispatchEvent(new dom.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:draft', id: 'old', prompt: 'night' }, source: frame.contentWindow
  }));
  await waitFor(() => assert.ok(posted.length), { timeout: 5000 });
  assert.equal(posted[0].ok, true);
  assert.equal(posted[0].draft.title, 'Route');
  assert.equal(calls.find(c => c.path === '/studio/draft').opts.method, 'POST');
});
