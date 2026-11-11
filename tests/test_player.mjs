// The footer transport, exercised in a real DOM: play/pause, ±10 s, the scrubber and the
// volume slider must move the <audio> element itself — not a mirror of it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

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

const html = await readFile(new URL('../standalone/static/index.html', import.meta.url), 'utf8');
const appjs = await readFile(new URL('../standalone/static/app.js', import.meta.url), 'utf8');

async function boot(options = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8794/', runScripts: 'outside-only' });
  const { window } = dom;
  window.fetch = async () => ({ ok: true, json: async () => ({ success: true }) });
  // The page polls engine status on an interval; a test does not, so the process can exit.
  window.setInterval = () => 0;
  const audio = window.document.getElementById('player');
  // jsdom has no media stack: give the element the state a real <audio> would have.
  let position = 0;
  let paused = true;
  Object.defineProperty(audio, 'currentTime', {
    get: () => position, set: value => { position = Number(value) || 0; }, configurable: true });
  Object.defineProperty(audio, 'paused', { get: () => paused, configurable: true });
  // A mutable length, so a test can present a stream with no known duration at all.
  let span = options.duration === undefined ? 200 : options.duration;
  Object.defineProperty(audio, 'duration', { get: () => span, configurable: true });
  audio.play = () => { paused = false; audio.dispatchEvent(new window.Event('play')); return Promise.resolve(); };
  audio.pause = () => { paused = true; audio.dispatchEvent(new window.Event('pause')); };
  audio.load = () => {};
  window.eval(appjs);
  // Let the page's own first refresh settle; anything still pending when the window
  // closes would surface as an unhandled rejection from the app, not from the test.
  await new Promise(done => setTimeout(done, 40));
  const setDuration = value => {
    span = value;
    audio.dispatchEvent(new window.Event('durationchange'));
  };
  return { window, audio, document: window.document, setDuration };
}

test('the scrubber moves the audio position and shows the time', async t => {
  const { window, audio, document } = await boot();
  t.after(() => window.close());

  audio.dispatchEvent(new window.Event('loadedmetadata'));
  const seek = document.getElementById('player-seek');
  seek.value = '90';
  seek.dispatchEvent(new window.Event('input'));

  assert.equal(audio.currentTime, 90);
  assert.equal(document.getElementById('player-elapsed').textContent, '1:30');
});

test('the ten-second buttons step back and forward', async t => {
  const { window, audio, document } = await boot();
  t.after(() => window.close());

  audio.currentTime = 30;
  document.getElementById('player-forward').click();
  assert.equal(audio.currentTime, 40, 'forward adds ten seconds');
  document.getElementById('player-back').click();
  document.getElementById('player-back').click();
  assert.equal(audio.currentTime, 20, 'back never goes below zero and steps ten seconds');
});

test('back stops at the start of the file', async t => {
  const { window, audio, document } = await boot();
  t.after(() => window.close());

  audio.currentTime = 4;
  document.getElementById('player-back').click();
  assert.equal(audio.currentTime, 0);
});

test('the volume slider sets the element volume', async t => {
  const { window, audio, document } = await boot();
  t.after(() => window.close());

  const volume = document.getElementById('player-volume');
  volume.value = '0.35';
  volume.dispatchEvent(new window.Event('input'));
  assert.equal(audio.volume, 0.35);
});

test('play toggles the element and the button says which it will do', async t => {
  const { window, audio, document } = await boot();

  audio.src = 'http://127.0.0.1:8794/api/studio/sample';
  const button = document.getElementById('player-play');
  assert.equal(button.textContent, '▶', 'nothing is playing at boot');

  button.click();
  assert.equal(audio.paused, false);
  assert.equal(button.textContent, '❚❚', 'while playing the button offers pause');

  button.click();
  assert.equal(audio.paused, true);
  assert.equal(button.textContent, '▶');
  await new Promise(done => setTimeout(done, 25));
  window.close();
});

test('a loaded file fills the scrubber range and the total time', async t => {
  const { window, audio, document } = await boot();

  audio.dispatchEvent(new window.Event('loadedmetadata'));
  assert.equal(document.getElementById('player-seek').max, '200');
  assert.equal(document.getElementById('player-total').textContent, '3:20');
  // jsdom schedules media bookkeeping on a timer; let it land before the window closes.
  await new Promise(done => setTimeout(done, 25));
  window.close();
});

test('the scrubber gets a real range from the take length', async t => {
  const { window, audio, document } = await boot({ duration: 200 });
  t.after(() => window.close());

  audio.dispatchEvent(new window.Event('loadedmetadata'));
  const seek = document.getElementById('player-seek');

  assert.equal(seek.max, '200', 'a known length becomes the range, so the bar can be dragged');
  assert.equal(seek.disabled, false);
  assert.equal(document.getElementById('player-total').textContent, '3:20');
});

test('a take with no known length disables the bar instead of leaving it dead', async t => {
  const { window, audio, document, setDuration } = await boot({ duration: Infinity });
  t.after(() => window.close());

  audio.dispatchEvent(new window.Event('loadedmetadata'));
  const seek = document.getElementById('player-seek');

  // A chunked response has no length; the element reports Infinity and nothing can be dragged.
  assert.equal(seek.max, '0');
  assert.equal(seek.disabled, true, 'say so rather than showing a bar that cannot move');
  assert.equal(document.getElementById('player-total').textContent, '--:--');
  assert.match(seek.title, /no known length/i);

  setDuration(180);
  assert.equal(seek.disabled, false, 'the bar comes back the moment the length is known');
  assert.equal(seek.max, '180');
  assert.equal(document.getElementById('player-total').textContent, '3:00');
});

test('a playback failure is shown, not swallowed', async t => {
  const { window, audio, document } = await boot();
  t.after(() => window.close());

  // A media element reports failure through its error event; the player must say something.
  Object.defineProperty(audio, 'error', { value: { code: 4 }, configurable: true });
  audio.dispatchEvent(new window.Event('error'));

  const note = document.getElementById('player-subtitle');
  assert.match(note.textContent, /could not be played/i);
  assert.ok(note.classList.contains('error'), 'the message is styled as a problem, not a subtitle');
});
