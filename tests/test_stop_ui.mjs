import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const app = readFileSync(new URL('../standalone/static/app.js', import.meta.url), 'utf8');
function load(name, context) {
  const source = app.match(new RegExp('^(?:async )?function ' + name + '\\([^]*?^}', 'm'))[0];
  return vm.runInNewContext('(' + source + ')', context);
}

const project = {project_id: 'p', takes: [
  {take_id: 'a', batch_id: 'mix', state: 'running'},
  {take_id: 'b', batch_id: 'mix', state: 'queued'},
  {take_id: 'c', batch_id: 'mix', state: 'queued'},
  {take_id: 'd', batch_id: 'other', state: 'queued'}
]};

test('stop confirmation counts queued siblings and shows the response', async () => {
  let question, shown;
  const detail = 'Stopped here; the server did not confirm';
  const stop = load('stopTake', {projects: [project],
    askConfirm: async text => { question = text; return true; },
    api: async () => ({detail}), refresh: async () => {}, library() {},
    message: text => { shown = text; }, libMessage: text => { shown = text; }
  });
  await stop('p', 'a');
  assert.match(question, /2 queued/);
  assert.equal(shown, detail);
});

test('followed batch with a cancelled sibling offers finished playback', () => {
  let shown = false;
  const project = {project_id: 'p', takes: [{take_id: 'a', state: 'succeeded'}, {take_id: 'b', state: 'cancelled'}]};
  load('renderActiveTake', {projects: [project], followed: {projectId: 'p', ids: ['a', 'b']},
    $: () => ({}), findActiveTake: () => null, showFinishedTake: () => { shown = true; }
  })();
  assert.equal(shown, true);
});
