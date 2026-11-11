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

const sdk = 'export const ROUTES_AREA="routes", SIDEBAR_NAV_AREA="sidebar.nav"; export const host={};';
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

test('falls back to the host recorded on disk when the route is not mounted', async () => {
  // A gateway started before GET /studio/host existed answers 404; the host it
  // already runs is recorded next to the library, so the pane still works.
  const read = [];
  window.hermesDesktop = {
    desktopPluginsRoot: async () => '/tmp/hermes-home/desktop-plugins',
    readFileText: async path => {
      read.push(path);
      if (path.endsWith('/plugin-data/hermes-music-studio/studio-host.json')) {
        return { text: JSON.stringify({ port: 8799, pid: 4242 }) };
      }
      throw new Error('ENOENT');
    }
  };
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, status: 200, opaque: true });
  try {
    mount(async () => { throw new Error('{"detail":"Not Found"}'); });
    const frame = await waitFor(() => {
      const found = document.querySelector('iframe');
      assert.ok(found, 'the pane still frames the host it found on disk');
      return found;
    });
    assert.equal(frame.getAttribute('src'), 'http://127.0.0.1:8799/');
    assert.ok(read.some(p => p.endsWith('/hermes-music-studio/studio-host.json')));
  } finally {
    globalThis.fetch = realFetch;
    delete window.hermesDesktop;
  }
});

test('an empty reply is an error, not a broken frame', async () => {
  mount(async () => ({ success: true }));
  assert.ok(await screen.findByText(/studio host is not running/i));
  assert.equal(document.querySelector('iframe'), null);
  assert.match(document.body.textContent, /returned no host url/);
});
