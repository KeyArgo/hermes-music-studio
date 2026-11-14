// The footer waveform, exercised in a real DOM: a take's peaks are fetched once, drawn on a
// HiDPI canvas, and the strip seeks. A missing waveform leaves the plain range input working.
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

const STATUS = { success: true, engine_available: false, engines: [], projects: [
  { project_id: 'p1', title: 'First song', caption: 'quiet', engine: 'ace', approved: true, takes: [
    { take_id: 't1', state: 'succeeded', engine_id: 'ace_local', requested_seed: 7 },
    { take_id: 't2', state: 'succeeded', engine_id: 'ace_local', requested_seed: 8 }
  ] }
] };
const PEAKS = [
  { min: -0.2, max: 0.2, rms: 0.1 },
  { min: -0.6, max: 0.6, rms: 0.3 },
  { min: -0.3, max: 0.4, rms: 0.2 },
  { min: -0.1, max: 0.1, rms: 0.05 }
];

async function boot(options = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
  const { window } = dom;
  const doc = window.document;
  window.setInterval = () => 0;
  window.confirm = () => true;
  Object.defineProperty(window, 'devicePixelRatio', { value: options.dpr || 1, configurable: true });
  Object.defineProperty(window.HTMLCanvasElement.prototype, 'clientWidth', { get: () => 300, configurable: true });
  Object.defineProperty(window.HTMLCanvasElement.prototype, 'clientHeight', { get: () => 48, configurable: true });
  const context = { fillStyle: '', fills: [], clears: 0, transforms: [],
    setTransform(...args) { this.transforms.push(args); },
    clearRect() { this.clears += 1; },
    fillRect() { this.fills.push(this.fillStyle); } };
  window.HTMLCanvasElement.prototype.getContext = () => context;

  const audio = doc.getElementById('player');
  let position = 0;
  const span = options.span === undefined ? 200 : options.span;
  Object.defineProperty(audio, 'currentTime', {
    get: () => position, set: value => { position = Number(value) || 0; }, configurable: true });
  Object.defineProperty(audio, 'paused', { get: () => true, configurable: true });
  Object.defineProperty(audio, 'duration', { get: () => span, configurable: true });
  audio.play = () => Promise.resolve();
  audio.pause = () => {};
  audio.load = () => {};

  const calls = [];
  const peakCalls = [];
  window.fetch = async (url, opts = {}) => {
    const path = String(url).replace('http://127.0.0.1:8794', '');
    calls.push({ path, method: opts.method || 'GET' });
    if (path === '/api/status') return { ok: true, json: async () => STATUS };
    if (path.includes('/peaks')) {
      peakCalls.push(path);
      if (options.failPeaks) return { ok: false, json: async () => ({ detail: 'no peaks' }) };
      return { ok: true, json: async () => ({ success: true, peaks: PEAKS,
        duration_seconds: options.span === undefined ? 200 : options.span }) };
    }
    return { ok: true, json: async () => ({ success: true }) };
  };
  window.eval(appjs);
  await new Promise(done => setTimeout(done, 40));
  return { window, doc, audio, calls, peakCalls, context };
}

function takeButtons(doc) {
  return [...doc.querySelectorAll('#library button')].filter(b => /▶ Play/.test(b.textContent));
}
async function playTake(doc, index) {
  const button = takeButtons(doc)[index];
  assert.ok(button, 'the take offers Play');
  button.click();
  await new Promise(done => setTimeout(done, 25));
}

test('playing a take draws its peaks on a crisp canvas above the seek bar', async t => {
  const { window, doc, peakCalls, context } = await boot({ dpr: 2 });
  t.after(() => window.close());
  doc.documentElement.style.setProperty('--accent', '#ff0000');
  doc.documentElement.style.setProperty('--muted', '#00ff00');

  await playTake(doc, 0);
  const canvas = doc.getElementById('player-wave');
  assert.equal(canvas.hidden, false, 'the strip appears when peaks arrive');
  assert.ok(doc.getElementById('player-track').classList.contains('has-wave'));
  assert.equal(canvas.width, 600, 'the backing store follows devicePixelRatio');
  assert.equal(canvas.height, 96);
  assert.match(peakCalls[0], /^\/api\/projects\/p1\/takes\/t1\/peaks\?buckets=/);
  assert.ok(context.fills.includes('#ff0000'), 'the played part uses the accent token');
  assert.ok(context.fills.includes('#00ff00'), 'the rest uses the muted token');
});

test('clicking and dragging on the waveform seeks the audio', async t => {
  const { window, doc, audio } = await boot();
  t.after(() => window.close());
  await playTake(doc, 0);

  const canvas = doc.getElementById('player-wave');
  canvas.getBoundingClientRect = () => ({ left: 100, width: 200, right: 300, top: 0, bottom: 48 });
  canvas.dispatchEvent(new window.MouseEvent('pointerdown', { clientX: 200, bubbles: true }));
  assert.equal(audio.currentTime, 100, 'the middle of the strip is half way through');
  canvas.dispatchEvent(new window.MouseEvent('pointermove', { clientX: 250, bubbles: true }));
  assert.equal(audio.currentTime, 150, 'a drag keeps seeking');
  assert.equal(doc.getElementById('player-seek').value, '150');
  window.dispatchEvent(new window.MouseEvent('pointerup'));
});

test('peaks are fetched once per take and reused', async t => {
  const { window, doc, peakCalls } = await boot();
  t.after(() => window.close());

  await playTake(doc, 0);
  await playTake(doc, 0);
  assert.equal(peakCalls.filter(path => path.includes('/takes/t1/')).length, 1,
    'the same take is requested once');

  await playTake(doc, 1);
  assert.equal(peakCalls.filter(path => path.includes('/takes/t2/')).length, 1,
    'a different take gets its own peaks');
});

test('a failed peaks route hides the canvas but leaves the range input working', async t => {
  const { window, doc, audio } = await boot({ failPeaks: true });
  t.after(() => window.close());
  await playTake(doc, 0);

  const canvas = doc.getElementById('player-wave');
  assert.equal(canvas.hidden, true, 'no fake waveform is drawn');
  assert.equal(doc.getElementById('player-track').classList.contains('has-wave'), false);
  audio.dispatchEvent(new window.Event('loadedmetadata'));
  const seek = doc.getElementById('player-seek');
  seek.value = '50';
  seek.dispatchEvent(new window.Event('input'));
  assert.equal(audio.currentTime, 50, 'the plain seek bar still moves the audio');
});

test('the waveform redraws on resize and when the theme changes', async t => {
  const { window, doc, context } = await boot();
  t.after(() => window.close());
  await playTake(doc, 0);

  const before = context.clears;
  window.dispatchEvent(new window.Event('resize'));
  assert.ok(context.clears > before, 'a resize repaints the strip');

  const themed = context.clears;
  doc.documentElement.dataset.theme = 'light';
  await new Promise(done => setTimeout(done, 10));
  assert.ok(context.clears > themed, 'a theme change repaints with the new tokens');
});
