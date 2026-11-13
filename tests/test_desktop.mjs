// The desktop pane is a frame around the real studio UI, so these tests pin the
// contract that matters: the pane asks the plugin route where the host is, frames
// exactly that URL, and says so honestly when the host is down.
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
// jsdom ships no fetch. The pane asks the host one opaque question — is anything listening — so the
// default answer here is "yes". A test that cares about the answer overrides it and restores after.
const realFetch = globalThis.fetch;
globalThis.fetch = async () => ({ ok: true, status: 200, opaque: true, type: 'opaque' });

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost' });
globalThis.window = dom.window; globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement; globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// Development dependency, resolved locally in a clone; the fallback covers machines where it is
// already installed beside a Hermes checkout.
// React 18's DOM build reads `navigator` while loading. Node 26 defines it; Node 20 (the version
// CI and many laptops run) does not, so requiring the library there throws ReferenceError, not a
// missing-module error. Define it before the require, not after.
if (!globalThis.navigator) {
  globalThis.navigator = { userAgent: 'node.js' };
}
let testingLibrary;
try {
  testingLibrary = require('@testing-library/react');
} catch (error) {
  // Only an explicit override: a hardcoded path to the author's Hermes checkout cannot work on
  // a clone, and a fallback that fails is worse than saying what to install.
  if (!process.env.TESTING_LIBRARY_PACKAGE) throw error;
  testingLibrary = createRequire(process.env.TESTING_LIBRARY_PACKAGE)('@testing-library/react');
}
const { render, screen, fireEvent, waitFor, cleanup } = testingLibrary;

// The stub must export everything the pane imports. It did not export SandboxedFrame, so importing
// the pane threw "does not provide an export named 'SandboxedFrame'" and every test in this file
// failed at load — the pane's frame contract was not covered at all.
const reactUrl = pathToFileURL(require.resolve('react')).href;
const sdk = `import { createElement } from ${JSON.stringify(reactUrl)};
export const ROUTES_AREA="routes", SIDEBAR_NAV_AREA="sidebar.nav"; export const host={};
export function SandboxedFrame(props) { return createElement('iframe', props); }`;;
let source = await readFile(new URL('../desktop/plugin.js', import.meta.url), 'utf8');
source = source.replaceAll("'@hermes/plugin-sdk'", JSON.stringify('data:text/javascript,' + encodeURIComponent(sdk)))
  .replaceAll("'react/jsx-runtime'", JSON.stringify(pathToFileURL(require.resolve('react/jsx-runtime')).href))
  .replaceAll("'react'", JSON.stringify(pathToFileURL(require.resolve('react')).href));
const plugin = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;

function contributionsOf() {
  const contributions = [];
  plugin.register({ register: c => contributions.push(c) });
  return contributions;
}

function mount(rest) {
  const contributions = [];
  // Same shape the app hands a plugin: register + the namespaced REST caller.
  plugin.register({ register: c => contributions.push(c), rest });
  const route = contributions.find(c => c.area === 'routes');
  assert.ok(route, 'a route contribution is registered');
  assert.ok(contributions.some(c => c.area === 'sidebar.nav'), 'a sidebar nav entry is registered');
  render(route.render());
}

test.afterEach(cleanup);

test('registers the studio route at /music-studio', () => {
  const contributions = contributionsOf();
  const route = contributions.find(c => c.area === 'routes');
  const nav = contributions.find(c => c.area === 'sidebar.nav');
  assert.equal(route.data.path, '/music-studio');
  assert.deepEqual(nav.data, { path: '/music-studio', label: 'Music Studio', codicon: 'unmute' });
});

test('frames the host URL the plugin route returns', async () => {
  const calls = [];
  mount(async path => {
    calls.push(path);
    if (path === '/studio/host') return { url: 'http://127.0.0.1:8775', port: 8775, started: false };
    throw new Error('unexpected request ' + path);
  });
  const frame = await waitFor(() => {
    const found = document.querySelector('iframe');
    assert.ok(found, 'an iframe is rendered');
    return found;
  });
  assert.equal(frame.getAttribute('src'), 'http://127.0.0.1:8775/');
  assert.equal(frame.getAttribute('title'), 'Music Studio');
  assert.deepEqual(calls, ['/studio/host']);
  assert.match(document.body.textContent, /same library as the agent tool/);
});

test('retries when the host route reports no host, then frames it', async () => {
  let attempt = 0;
  mount(async path => {
    assert.equal(path, '/studio/host');
    attempt += 1;
    if (attempt === 1) throw new Error('studio host unavailable: no interpreter with uvicorn found');
    return { url: 'http://127.0.0.1:8776' };
  });
  assert.ok(await screen.findByText(/studio host is not running/i));
  assert.match(document.body.textContent, /no interpreter with uvicorn found/);
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  const frame = await waitFor(() => {
    const found = document.querySelector('iframe');
    assert.ok(found);
    return found;
  });
  assert.equal(frame.getAttribute('src'), 'http://127.0.0.1:8776/');
  assert.equal(attempt, 2);
});

test('a 404 from the plugin route is an error, not a reason to read the desktop bridge', async () => {
  // Review item 2: the pane must use the plugin's own route, not the app's private bridge. An older
  // gateway answers 404; the pane says so instead of reaching for files it is not entitled to read.
  const read = [];
  window.hermesDesktop = {
    desktopPluginsRoot: async () => '/tmp/hermes-home/desktop-plugins',
    readFileText: async path => { read.push(path); throw new Error('ENOENT'); }
  };
  try {
    mount(async () => { throw new Error('{"detail":"Not Found"}'); });
    assert.ok(await screen.findByText(/studio host is not running/i));
    assert.equal(document.querySelector('iframe'), null);
    assert.match(document.body.textContent, /route said/i);
    assert.deepEqual(read, [], 'the pane must not read studio-host.json through the desktop bridge');
  } finally {
    delete window.hermesDesktop;
  }
});

test('an empty reply is an error, not a broken frame', async () => {
  mount(async () => ({ success: true }));
  assert.ok(await screen.findByText(/studio host is not running/i));
  assert.equal(document.querySelector('iframe'), null);
  assert.match(document.body.textContent, /returned no host url/);
});

function mountWithOs(rest) {
  const opened = [];
  const contributions = [];
  plugin.register({
    register: c => contributions.push(c),
    rest,
    os: { openExternal: async url => { opened.push(url); } }
  });
  render(contributions.find(c => c.area === 'routes').render());
  return opened;
}

test('a postMessage from any window but the studio frame is ignored', async () => {
  const opened = mountWithOs(async () => ({ url: 'http://127.0.0.1:8775', token: 'tok' }));
  const frame = await waitFor(() => {
    const found = document.querySelector('iframe');
    assert.ok(found, 'the studio is framed');
    return found;
  });
  // Not the frame: window itself, a stray opener, anything else.
  window.dispatchEvent(new dom.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:open-external', url: 'https://example.com/elsewhere' },
    source: window
  }));
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.deepEqual(opened, [], 'a window that is not the studio frame must not be able to act');

  // From the frame, the same message is honoured.
  window.dispatchEvent(new dom.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:open-external', url: 'https://example.com/ok' },
    source: frame.contentWindow
  }));
  await waitFor(() => assert.deepEqual(opened, ['https://example.com/ok']));
});

test('only http(s) links reach the operating system', async () => {
  const opened = mountWithOs(async () => ({ url: 'http://127.0.0.1:8775', token: 'tok' }));
  const frame = await waitFor(() => {
    const found = document.querySelector('iframe');
    assert.ok(found);
    return found;
  });
  for (const hostile of ['file:///etc/passwd', 'javascript:alert(1)', 'smb://host/share', 'data:text/html,x']) {
    window.dispatchEvent(new dom.window.MessageEvent('message', {
      data: { type: 'hermes-music-studio:open-external', url: hostile },
      source: frame.contentWindow
    }));
  }
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.deepEqual(opened, [], 'a non-web scheme was handed to openExternal');
});

test('a draft request from the frame is answered through the plugin route', async () => {
  const calls = [];
  const contributions = [];
  plugin.register({
    register: c => contributions.push(c),
    rest: async (path, options) => {
      calls.push({ path, options });
      if (path === '/studio/host') return { url: 'http://127.0.0.1:8775', token: 'tok' };
      return { draft: { title: 'Night Drive' } };
    }
  });
  render(contributions.find(c => c.area === 'routes').render());
  const frame = await waitFor(() => {
    const found = document.querySelector('iframe');
    assert.ok(found, 'the studio is framed');
    return found;
  });
  const posted = [];
  frame.contentWindow.postMessage = message => posted.push(message);
  window.dispatchEvent(new dom.window.MessageEvent('message', {
    data: { type: 'hermes-music-studio:draft', id: 'd1', prompt: 'a night drive', tone: 'warm', style: 70 },
    source: frame.contentWindow
  }));
  await waitFor(() => assert.ok(posted.length, 'the frame was answered'));
  const draftCall = calls.find(c => c.path === '/studio/draft');
  assert.ok(draftCall, 'drafting goes to the route that owns the model');
  assert.equal(draftCall.options.method, 'POST');
  assert.equal(draftCall.options.body.prompt, 'a night drive');
  assert.equal(posted[0].type, 'hermes-music-studio:draft-result');
  assert.equal(posted[0].id, 'd1');
  assert.equal(posted[0].draft.title, 'Night Drive');
});
